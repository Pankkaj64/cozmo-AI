"""Sweep store, checkpoints, finish/export, and background agents (research, deferred checks)."""

from __future__ import annotations

import asyncio
import copy
import json
import time
import uuid

from fastapi import HTTPException

from .. import config
from ..config import settings
from .claim import SweepStart, build_claim_packet, empty_packet
from .identification import promote_verified_book
from .providers import research_inventory
from .report import report_html
from .utils import performance_summary, stamp, trace_step
from .workflow import now, refresh_workflow, stage

ROOT = config.ROOT_DIR
FRAME_DIR = config.FRAME_DIR
PACKET_DIR = config.PACKET_DIR
CLAIM_DIR = config.CLAIM_DIR

SWEEPS: dict[str, dict] = {}
ACTIVE_FRAMES: set[str] = set()
LIVE_RESEARCH_TASKS: dict[str, asyncio.Task] = {}
CROP_VERIFICATION_TASKS: dict[str, asyncio.Task] = {}
EVENT_STREAM_LIFETIME_S = 15


@trace_step("sweep.checkpoint")
def save_active_sweep(sweep_id: str) -> None:
    """Keep an unfinished sweep recoverable if the dev server reloads."""
    sweep = SWEEPS[sweep_id]
    path = PACKET_DIR / f"{sweep_id}.active.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(sweep), encoding="utf-8")
    temporary.replace(path)
    print(f"[DEBUG {stamp()}] sweep.checkpoint.saved path={str(path)}", flush=True)


def restore_active_sweeps() -> None:
    for path in PACKET_DIR.glob("*.active.json"):
        try:
            sweep = json.loads(path.read_text(encoding="utf-8"))
            SWEEPS[sweep["packet"]["sweep"]["id"]] = sweep
            print(
                f"[DEBUG {stamp()}] sweep.restored sweep_id={sweep['packet']['sweep']['id']}",
                flush=True,
            )
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
            print(
                f"[WARN {stamp()}] sweep.restore.skipped path={str(path)} error={str(exc)}",
                flush=True,
            )


def require_sweep(sweep_id: str) -> dict:
    sweep = SWEEPS.get(sweep_id)
    if sweep is None:
        try:
            canonical = str(uuid.UUID(sweep_id))
            packet = json.loads((PACKET_DIR / f"{canonical}.json").read_text())
            sweep = {
                "packet": packet,
                "status": "finished",
                "position": 0,
                "started": time.time(),
            }
            SWEEPS[sweep_id] = sweep
        except (ValueError, OSError) as exc:
            raise HTTPException(404, "Sweep not found") from exc
    return sweep


def require_packet(sweep_id: str) -> dict:
    return require_sweep(sweep_id)["packet"]


def evidence_frame(packet: dict, ref: str) -> dict:
    frame = next((f for f in packet.get("frames", []) if f["frame_ref"] == ref), None)
    if frame is None:
        raise HTTPException(422, "Select a saved frame from this sweep")
    return frame


def find_line(packet: dict, ref_id: str) -> dict | None:
    return next(
        (line for line in packet["books"] + packet["items"] if line["id"] == ref_id),
        None,
    )


@trace_step("sweep.create")
def start_sweep(start: SweepStart) -> dict:
    sweep_id = str(uuid.uuid4())
    packet = empty_packet(sweep_id, start.country, start.currency, start.device)
    packet["sweep"]["country_code"] = start.country_code
    packet["appraisal_threshold"] = start.appraisal_threshold
    SWEEPS[sweep_id] = {
        "packet": packet,
        "started": time.time(),
        "position": 0,
        "status": "active",
    }
    refresh_workflow(packet)
    save_active_sweep(sweep_id)
    print(f"[DEBUG {stamp()}] sweep.created sweep_id={sweep_id}", flush=True)

    return {"sweep_id": sweep_id, "packet": packet}


def _check_evidence(packet: dict) -> None:
    for frame in packet.get("frames", []):
        ref = frame.get("frame_ref", "")
        if not ref.startswith("data/frames/") or not (ROOT / ref).is_file():
            finding = {
                "ref_id": ref,
                "reason": "Referenced evidence frame is missing from disk.",
                "origin": "workflow",
            }
            if finding not in packet["review_queue"]:
                packet["review_queue"].append(finding)


def write_exports(packet: dict) -> tuple[str, str]:
    """Write the internal packet, and the contract packet + report; return claim paths."""
    sweep_id = packet["sweep"]["id"]
    PACKET_DIR.mkdir(parents=True, exist_ok=True)
    (PACKET_DIR / f"{sweep_id}.json").write_text(json.dumps(packet, indent=2), encoding="utf-8")
    claim_dir = ROOT / "data" / "claims" / sweep_id
    claim_dir.mkdir(parents=True, exist_ok=True)
    (claim_dir / "claim_packet.json").write_text(
        json.dumps(build_claim_packet(packet), indent=2), encoding="utf-8"
    )
    (claim_dir / "report.html").write_text(report_html(packet), encoding="utf-8")
    print(
        f"[DEBUG {stamp()}] packet.exports.saved sweep_id={sweep_id} claim_dir={str(claim_dir)}",
        flush=True,
    )
    return (
        str((claim_dir / "claim_packet.json").relative_to(ROOT)),
        str((claim_dir / "report.html").relative_to(ROOT)),
    )


@trace_step("sweep.finish")
def finish_sweep(sweep_id: str) -> dict:
    sweep = require_sweep(sweep_id)
    if sweep_id in ACTIVE_FRAMES:
        raise HTTPException(409, "Wait for frame processing before finishing")
    packet = sweep["packet"]
    if sweep.get("status") != "finished" and "capture_stopped" not in sweep:
        packet["sweep"]["duration_s"] = round(time.time() - sweep["started"])
    refresh_workflow(packet)
    _check_evidence(packet)
    packet["sweep"]["finished_at"] = now()
    packet["sweep"]["time_to_packet_s"] = (
        round(time.time() - sweep["capture_stopped"], 3) if "capture_stopped" in sweep else None
    )
    packet["sweep"]["summary"] = summary_text(packet)
    packet["sweep"]["packet_status"] = (
        "needs_review" if packet["review_queue"] else "ready_for_review"
    )
    with stage(packet, "claim_packet"):
        json_file, report_file = write_exports(packet)
    packet["performance"] = performance_summary(packet)
    print(
        f"[DEBUG {stamp()}] packet.totals.calculated books={packet['totals']['book_count']} items={len(packet['items'])} review_flags={len(packet['review_queue'])}",
        flush=True,
    )
    sweep["status"] = "finished"
    save_active_sweep(sweep_id)
    return {"packet": packet, "json_file": json_file, "report_file": report_file}


async def run_research(sweep_id: str) -> dict:
    """Retrieve catalogue and price evidence for the whole inventory, bounded in time."""
    packet = require_packet(sweep_id)
    if packet.get("workflow", {}).get("research", {}).get("status") == "running":
        raise HTTPException(409, "Market research is already running")
    snapshot = copy.deepcopy(packet)
    with stage(packet, "research") as output:
        try:
            async with asyncio.timeout(settings.pricing_research_timeout_s):
                await research_inventory(snapshot)
        except TimeoutError:
            output["warning"] = "Research time limit reached; partial results retained"
        for key in ("research", "quotes", "fx_rates", "cost"):
            if key in snapshot:
                packet[key] = snapshot[key]
        # Apply only ISBN-backed metadata to the same still-present physical track.
        for observed in snapshot["books"]:
            source = observed.get("identity_source", {})
            current = next((b for b in packet["books"] if b["id"] == observed["id"]), None)
            if current and observed.get("title_rejected") and not current.get("identity_source"):
                for key in ("proposed_title", "title", "author", "status", "title_rejected"):
                    current[key] = observed[key]
                finding = {
                    "ref_id": current["id"],
                    "reason": observed["title_rejected"],
                    "origin": "research",
                }
                if finding not in packet["review_queue"]:
                    packet["review_queue"].append(finding)
            if (
                current
                and source.get("source") == "Open Library ISBN edition record"
                and not current.get("identity_source")
            ):
                for key in (
                    "title",
                    "author",
                    "publisher",
                    "identity_source",
                    "status",
                ):
                    current[key] = observed[key]
        output["lines"] = len(packet.get("research", []))
        output["quotes"] = len(packet.get("quotes", []))
    refresh_workflow(packet)
    save_active_sweep(sweep_id)
    return {"packet": packet}


async def _live_research(sweep_id: str) -> None:
    try:
        await run_research(sweep_id)
    except Exception as exc:  # noqa: BLE001 - background task must never crash the server
        print(f"[WARN {stamp()}] research.live.failed error_type={type(exc).__name__}", flush=True)


def schedule_live_research(sweep_id: str) -> None:
    if not settings.enable_live_research or not settings.any_price_source_configured:
        return
    task = LIVE_RESEARCH_TASKS.get(sweep_id)
    if task and not task.done():
        return
    packet = require_packet(sweep_id)
    # Avoid repeated lookups for an unchanged observed inventory.
    signature = tuple(
        (
            line["id"],
            line.get("title", ""),
            line.get("isbn", ""),
            line.get("brand_model", ""),
        )
        for line in packet["books"] + packet["items"]
    )
    if signature == SWEEPS[sweep_id].get("research_signature"):
        return
    SWEEPS[sweep_id]["research_signature"] = signature
    LIVE_RESEARCH_TASKS[sweep_id] = asyncio.create_task(_live_research(sweep_id))


def schedule_deferred_verification(sweep_id: str) -> None:
    """Finish bounded live-frame checks in the background after capture stops."""
    existing = CROP_VERIFICATION_TASKS.get(sweep_id)
    if existing and not existing.done():
        return
    packet = require_packet(sweep_id)
    pending = [
        line
        for line in packet["books"] + packet["items"]
        if (
            (line.get("crop_verification") or {}).get("status") == "deferred"
            or (line.get("reader_evidence") or {}).get("status") == "deferred"
        )
        and not line.get("identity_source")
    ]
    if not pending:
        return
    packet["verification_progress"] = {
        "status": "running",
        "completed": 0,
        "total": len(pending),
    }
    save_active_sweep(sweep_id)
    CROP_VERIFICATION_TASKS[sweep_id] = asyncio.create_task(verify_deferred(sweep_id, pending))


async def read_deferred_title(packet: dict, line: dict, candidate: dict, raw: bytes) -> None:
    """Run the title reader on a crop the live frame budget skipped; proposals only."""
    from .perception import BOOK_READER_MODEL, read_visual_title

    try:
        reading = await read_visual_title(raw, candidate)
    except Exception as exc:  # noqa: BLE001 - one unreadable crop must not stop the pass
        line["reader_evidence"] = {
            "status": "unavailable",
            "model": BOOK_READER_MODEL,
            "reason": type(exc).__name__,
        }
        return
    line["reader_evidence"] = dict(reading, model=BOOK_READER_MODEL, status="proposal")
    line["proposed_author"] = reading.get("author", "")
    line["proposed_publisher"] = reading.get("publisher", "")
    if reading["is_book"] and reading["title"]:
        line["proposed_title"] = reading["title"]
    elif reading["is_book"] is False:
        finding = {
            "ref_id": line["id"],
            "reason": "Crop reader says this box is not a book; the line stays unidentified for review.",
            "origin": "deferred_reader",
        }
        if finding not in packet["review_queue"]:
            packet["review_queue"].append(finding)
    print(
        f"[DEBUG {stamp()}] book.reader.deferred.complete ref_id={line['id']} "
        f"is_book={reading['is_book']} proposed_title={bool(reading['title'])}",
        flush=True,
    )


async def verify_deferred(sweep_id: str, pending: list[dict]) -> None:
    from .crop_verifier import verify_candidates

    packet = require_packet(sweep_id)
    try:
        for index, line in enumerate(pending):
            still_present = any(line is current for current in packet["books"] + packet["items"])
            if not line.get("identity_source") and still_present:
                ref = line.get("frame_ref", "")
                path = (ROOT / ref).resolve()
                if path.is_relative_to(FRAME_DIR.resolve()) and path.is_file():
                    candidate = dict(line, bbox=line.get("object_bbox") or line.get("bbox"))
                    is_book = line in packet["books"]
                    raw = path.read_bytes()
                    if is_book and (line.get("reader_evidence") or {}).get("status") == "deferred":
                        await read_deferred_title(packet, line, candidate, raw)
                    if (line.get("crop_verification") or {}).get("status") == "deferred":
                        await verify_candidates(
                            raw,
                            [candidate] if is_book else [],
                            [] if is_book else [candidate],
                            ref,
                        )
                        # A review received while inference ran takes precedence.
                        if not line.get("identity_source"):
                            line["crop_verification"] = candidate["crop_verification"]
                            if not is_book:
                                line["category_verified"] = candidate["category_verified"]
                                line["reader_category"] = candidate.get("reader_category", "")
                    if is_book and not line.get("identity_source"):
                        frame = next(
                            (f for f in packet.get("frames", []) if f["frame_ref"] == ref),
                            {},
                        )
                        promote_verified_book(line, frame.get("validation", {}))
                else:
                    line["crop_verification"] = {
                        "agreed": False,
                        "status": "unavailable",
                        "reason": "Saved evidence missing",
                    }
            packet["verification_progress"]["completed"] = index + 1
            refresh_workflow(packet)
            save_active_sweep(sweep_id)
        packet["verification_progress"]["status"] = "complete"
        finish_sweep(sweep_id)
    except Exception as exc:  # noqa: BLE001 - record and keep the sweep usable
        packet["verification_progress"].update(status="failed", reason=type(exc).__name__)
        save_active_sweep(sweep_id)
        print(
            f"[WARN {stamp()}] crop_verification.background.failed error_type={type(exc).__name__}",
            flush=True,
        )


def summary_text(packet: dict) -> str:
    """Read-back summary for the claimant, assembled from code-computed totals only."""
    t, cur = packet["totals"], packet["sweep"].get("currency", "")
    return (
        f"Sweep complete. I counted {t['book_count']} books, identified {t['books_identified']} "
        f"and left {t['books_unidentified']} unreadable. Books priced at {t['books_replacement_cost']:g} {cur} "
        f"replacement and {t['books_used_value']:g} {cur} used value; {t.get('item_count', 0)} other items at "
        f"{t['items_replacement_cost_low']:g} to {t['items_replacement_cost_high']:g} {cur}. "
        f"{t['excluded_from_totals']} lines have no sourced value and {len(packet['review_queue'])} findings need review."
    )

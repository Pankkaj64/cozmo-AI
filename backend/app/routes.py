"""HTTP routes. Transport only: every rule lives in app.logic.*"""

from __future__ import annotations

import asyncio
import json
import time
import uuid

from fastapi import APIRouter, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field

from .config import settings
from .logic import state
from .logic.claim import SweepStart, build_claim_packet, calculate_totals
from .logic.conversation import Turn, converse
from .logic.evaluation import evaluate
from .logic.measurement import Calibration, RoomInput, measure_spine, room_geometry
from .logic.perception import capture_quality, inspect_frame
from .logic.pricing import Offer, apply_prices, known_locales, resolve_locale, sweep_locale
from .logic.providers import compare_locale, configured_providers
from .logic.report import claim_bundle, report_html
from .logic.utils import event, performance_summary, trace_step
from .logic.workflow import audit, merge_observations, now, refresh_workflow, stage

app = FastAPI(title="Library Contents Claim Agent", version="0.3.0")
app.add_middleware(
    CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"]
)


sweeps_router = APIRouter(prefix="/api/sweeps", tags=["sweeps"])


@sweeps_router.post("")
async def create_sweep(start: SweepStart):
    return state.start_sweep(start)


@sweeps_router.get("")
async def list_sweeps():
    records: dict[str, dict] = {}
    paths = sorted(state.PACKET_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in paths[:100]:
        try:
            data = json.loads(path.read_text())
            sweep = data.get("packet", data)["sweep"]
            records.setdefault(
                sweep["id"],
                {
                    "id": sweep["id"],
                    "captured_at": sweep["captured_at"],
                    "country": sweep["country"],
                },
            )
        except (ValueError, KeyError, OSError):
            continue
    return list(records.values())


@sweeps_router.get("/locales")
async def locales():
    """Supported countries with their default currency (declared before /{sweep_id})."""
    return {"locales": known_locales()}


@sweeps_router.get("/{sweep_id}")
@trace_step("sweep.read")
async def get_sweep(sweep_id: str):
    sweep = state.require_sweep(sweep_id)
    if sweep.get("status") == "finished":
        state.schedule_deferred_verification(sweep_id)
    calculate_totals(sweep["packet"])
    return sweep["packet"]


@sweeps_router.get("/{sweep_id}/events")
async def workflow_events(sweep_id: str, request: Request):
    sweep = state.require_sweep(sweep_id)

    async def updates():
        previous = ""
        expires = time.monotonic() + state.EVENT_STREAM_LIFETIME_S
        yield "retry: 1500\n\n"
        while time.monotonic() < expires and not await request.is_disconnected():
            packet = sweep["packet"]
            encoded = json.dumps(
                {
                    "workflow": packet.get("workflow", {}),
                    "progress": packet.get("live_progress", {}),
                    "guidance": packet.get("guidance"),
                    "audit_count": len(packet.get("audit_trail", [])),
                    "packet": packet,
                }
            )
            if encoded != previous:
                yield "data: " + encoded + "\n\n"
                previous = encoded
            else:
                yield ": keepalive\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(
        updates(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@sweeps_router.post("/{sweep_id}/stop-capture")
async def stop_capture(sweep_id: str):
    sweep = state.require_sweep(sweep_id)
    sweep.setdefault("capture_stopped", time.time())
    sweep["packet"]["sweep"]["duration_s"] = round(sweep["capture_stopped"] - sweep["started"])
    state.save_active_sweep(sweep_id)
    return {"stopped_at": sweep["capture_stopped"]}


@sweeps_router.post("/{sweep_id}/finish")
async def finish(sweep_id: str):
    sweep = state.require_sweep(sweep_id)
    if sweep_id in state.ACTIVE_FRAMES:
        raise HTTPException(409, "Wait for frame processing before finishing")
    task = state.LIVE_RESEARCH_TASKS.get(sweep_id)
    if task and not task.done():
        await task
    if sweep.get("status") != "finished" and settings.any_price_source_configured:
        await state.run_research(sweep_id)
    result = state.finish_sweep(sweep_id)
    state.schedule_deferred_verification(sweep_id)
    return result


class ClaimSettings(BaseModel):
    appraisal_threshold: float = Field(gt=0, allow_inf_nan=False)


@sweeps_router.post("/{sweep_id}/settings")
async def update_settings(sweep_id: str, body: ClaimSettings):
    packet = state.require_packet(sweep_id)
    packet["appraisal_threshold"] = body.appraisal_threshold
    audit(packet, "settings.updated", appraisal_threshold=body.appraisal_threshold)
    refresh_workflow(packet)
    state.save_active_sweep(sweep_id)
    return {"packet": packet}


capture_router = APIRouter(prefix="/api/sweeps", tags=["capture"])
MAX_FRAME_BYTES = 12_000_000
MAX_VIDEO_BYTES = 220_000_000


@capture_router.post("/{sweep_id}/frames")
@trace_step("frame.upload")
async def add_frame(
    sweep_id: str, image: UploadFile = File(...), shelf: str = Form("Unassigned shelf")
):
    sweep = state.SWEEPS.get(sweep_id)
    if sweep is None:
        raise HTTPException(404, "Sweep not found")
    if image.content_type not in {"image/jpeg"}:
        raise HTTPException(415, "Use a JPEG frame")
    if sweep.get("status") == "finished":
        raise HTTPException(409, "This sweep has finished; start a new sweep to capture more")
    if sweep_id in state.ACTIVE_FRAMES:
        raise HTTPException(409, "A frame is already processing for this sweep")
    raw = await image.read(MAX_FRAME_BYTES + 1)
    event("frame.received", bytes=len(raw), shelf=shelf, content_type=image.content_type)
    if len(raw) > MAX_FRAME_BYTES:
        raise HTTPException(413, "Frame must be under 12 MB")
    name = f"{sweep_id}-{uuid.uuid4().hex}.jpg"
    state.FRAME_DIR.mkdir(parents=True, exist_ok=True)
    (state.FRAME_DIR / name).write_bytes(raw)
    ref = f"data/frames/{name}"
    event("frame.saved", frame_ref=ref, bytes=len(raw))
    analysis_started = time.perf_counter()
    try:
        quality = capture_quality(raw)
    except Exception:  # noqa: BLE001 - a quality failure must not block capture
        quality = {"warnings": ["Image quality could not be evaluated."]}
    packet = sweep["packet"]
    packet["live_progress"] = {
        "stage": "perception",
        "shelf": shelf,
        "frame_ref": ref,
        "guidance": " ".join(quality["warnings"]) or "Reading the spines and checking the count.",
    }
    state.ACTIVE_FRAMES.add(sweep_id)
    try:
        with stage(packet, "perception") as output:
            candidate = await inspect_frame(raw, ref)
            output.update(
                vision_status=candidate.get("vision_status"),
                primary_count=candidate.get("primary_count"),
                validator_count=candidate.get("validation", {}).get("count"),
            )
    finally:
        state.ACTIVE_FRAMES.discard(sweep_id)
    packet["live_progress"] = {"stage": "ready", "shelf": shelf}
    candidate["quality"] = quality
    candidate["elapsed_s"] = round(time.perf_counter() - analysis_started, 3)
    with stage(packet, "identification"):
        packet = merge_observations(sweep, candidate, shelf, ref)
    refresh_workflow(packet)
    state.save_active_sweep(sweep_id)
    state.schedule_live_research(sweep_id)
    return {"frame_ref": ref, "candidate": candidate, "packet": packet}


@capture_router.post("/{sweep_id}/video")
@trace_step("video.save")
async def save_video(sweep_id: str, video: UploadFile = File(...)):
    sweep = state.require_sweep(sweep_id)
    mime = (video.content_type or "").split(";")[0]
    if mime not in {"video/webm", "video/mp4"}:
        raise HTTPException(415, "Use WebM or MP4 camera evidence")
    video_dir = state.ROOT / "data" / "videos"
    video_dir.mkdir(parents=True, exist_ok=True)
    path = video_dir / f"{sweep_id}-{uuid.uuid4().hex}.{'mp4' if mime == 'video/mp4' else 'webm'}"
    size = 0
    try:
        with path.open("wb") as output:
            while chunk := await video.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_VIDEO_BYTES:
                    raise HTTPException(413, "Video must be under 220 MB")
                output.write(chunk)
        if not size:
            raise HTTPException(422, "Video is empty")
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    ref = str(path.relative_to(state.ROOT))
    sweep["packet"].setdefault("videos", []).append(
        {"ref": ref, "bytes": size, "mime": mime, "audio": False, "saved_at": now()}
    )
    audit(sweep["packet"], "video.saved", ref=ref, bytes=size)
    state.save_active_sweep(sweep_id)
    return {"video_ref": ref}


conversation_router = APIRouter(prefix="/api/sweeps", tags=["conversation"])


@conversation_router.post("/{sweep_id}/turns")
@conversation_router.post("/{sweep_id}/corrections")
@trace_step("conversation.turn")
async def add_turn(sweep_id: str, body: Turn):
    sweep = state.require_sweep(sweep_id)
    result = await converse(sweep["packet"], body)
    state.save_active_sweep(sweep_id)
    return result


measurement_router = APIRouter(prefix="/api/sweeps", tags=["measurement"])


@measurement_router.post("/{sweep_id}/calibration")
async def calibrate(sweep_id: str, body: Calibration):
    packet = state.require_packet(sweep_id)
    frame = state.evidence_frame(packet, body.frame_ref)
    if not frame.get("quality", {}).get("width"):
        raise HTTPException(422, "Frame dimensions are unavailable")
    packet.setdefault("calibrations", {})[body.frame_ref] = body.model_dump()
    audit(
        packet,
        "calibration.recorded",
        frame_ref=body.frame_ref,
        reference=body.reference,
    )
    refresh_workflow(packet)
    state.save_active_sweep(sweep_id)
    return {"packet": packet}


class SpineBounds(BaseModel):
    ref_id: str
    bbox: list[float] = Field(min_length=4, max_length=4)


@measurement_router.post("/{sweep_id}/spine-bounds")
async def spine_bounds(sweep_id: str, body: SpineBounds):
    packet = state.require_packet(sweep_id)
    book = next((b for b in packet["books"] if b["id"] == body.ref_id), None)
    if not book:
        raise HTTPException(404, "Book not found")
    calibration = packet.get("calibrations", {}).get(book["frame_ref"])
    if not calibration:
        raise HTTPException(422, "Calibrate this book's evidence frame first")
    quality = state.evidence_frame(packet, book["frame_ref"])["quality"]
    try:
        result = measure_spine(
            body.bbox,
            Calibration.model_validate(calibration),
            quality["width"],
            quality["height"],
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    book.update(result, bbox=body.bbox, bbox_kind="spine")
    audit(packet, "spine.measured", ref_id=book["id"], frame_ref=book["frame_ref"])
    refresh_workflow(packet)
    state.save_active_sweep(sweep_id)
    return {"packet": packet}


@measurement_router.post("/{sweep_id}/room")
async def measure_room(sweep_id: str, body: RoomInput):
    packet = state.require_packet(sweep_id)
    state.evidence_frame(packet, body.frame_ref)
    try:
        packet["room"] = room_geometry(body)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    audit(packet, "room.measured", frame_ref=body.frame_ref, method=body.method)
    refresh_workflow(packet)
    state.save_active_sweep(sweep_id)
    return {"packet": packet}


class ItemMeasurement(BaseModel):
    ref_id: str
    frame_ref: str
    w: float = Field(gt=0, le=10000, allow_inf_nan=False)
    h: float = Field(gt=0, le=10000, allow_inf_nan=False)
    d: float = Field(gt=0, le=10000, allow_inf_nan=False)
    source: str = Field(min_length=3, max_length=500)


@measurement_router.post("/{sweep_id}/item-measurement")
async def item_measurement(sweep_id: str, body: ItemMeasurement):
    packet = state.require_packet(sweep_id)
    item = next((i for i in packet["items"] if i["id"] == body.ref_id), None)
    if item is None:
        raise HTTPException(404, "Non-book item not found")
    if item["frame_ref"] != body.frame_ref:
        raise HTTPException(422, "Use the selected item's saved frame")
    item["dimensions_cm"] = {"w": body.w, "h": body.h, "d": body.d}
    item["measurement"] = {
        "source": body.source,
        "frame_ref": body.frame_ref,
        "method": "operator supplied reference geometry",
    }
    audit(packet, "item.measured", ref_id=body.ref_id)
    refresh_workflow(packet)
    state.save_active_sweep(sweep_id)
    return {"packet": packet}


pricing_router = APIRouter(prefix="/api/sweeps", tags=["pricing"])


@pricing_router.post("/{sweep_id}/offers")
async def record_offer(sweep_id: str, body: Offer):
    packet = state.require_packet(sweep_id)
    line = state.find_line(packet, body.ref_id)
    if not line or ((body.kind == "item") != ("category" in line)):
        raise HTTPException(422, "Select a matching book or non-book item")
    packet.setdefault("offers", []).append(body.model_dump(mode="json"))
    audit(
        packet,
        "price.source.recorded",
        ref_id=body.ref_id,
        source=body.source,
        country=body.country,
        currency=body.currency,
    )
    refresh_workflow(packet)
    state.save_active_sweep(sweep_id)
    return {"packet": packet}


@pricing_router.post("/{sweep_id}/research")
async def research(sweep_id: str):
    return await state.run_research(sweep_id)


@pricing_router.get("/{sweep_id}/prices")
async def price_details(sweep_id: str):
    """Every price decision: chosen quote, candidates considered and rejections with reasons."""
    packet = state.require_packet(sweep_id)
    valuation = apply_prices(packet, write=False)
    return {
        "locale": sweep_locale(packet).model_dump()
        if packet["sweep"].get("country") and packet["sweep"].get("currency")
        else None,
        "providers": [p.name for p in configured_providers()],
        "min_match_score": settings.pricing_min_match,
        "appraisal_threshold": packet.get("appraisal_threshold"),
        "priced_lines": valuation["priced"],
        "quotes_recorded": len(packet.get("quotes", [])) + len(packet.get("offers", [])),
        "fx_rates": packet.get("fx_rates", {}),
        "details": valuation["details"],
        "locale_comparison": packet.get("locale_comparison"),
        "provider_calls": packet.get("cost", {}).get("provider_calls", {}),
    }


class LocaleBody(BaseModel):
    country_code: str = Field(default="", pattern=r"^([A-Z]{2})?$")
    country: str = ""
    currency: str = Field(default="", pattern=r"^([A-Z]{3})?$")


@pricing_router.post("/{sweep_id}/compare-locale")
async def compare(sweep_id: str, body: LocaleBody):
    """Price the same (up to) ten identified books for a second country."""
    packet = state.require_packet(sweep_id)
    try:
        target = resolve_locale(
            country_code=body.country_code, country=body.country, currency=body.currency
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    base = sweep_locale(packet)
    if target.matches(base.country, base.currency):
        raise HTTPException(422, "Choose a different country or currency from the claim locale")
    with stage(packet, "locale_comparison") as output:
        result = await compare_locale(packet, target)
        output.update(
            books=len(result["book_ids"]),
            quotes=result["quotes_retrieved"],
            target=target.model_dump(),
        )
    audit(
        packet,
        "locale.compared",
        country=target.country,
        currency=target.currency,
        books=len(result["book_ids"]),
    )
    state.save_active_sweep(sweep_id)
    return result


review_router = APIRouter(prefix="/api/sweeps", tags=["review"])


class InventoryReview(BaseModel):
    ref_id: str
    source: str = Field(min_length=3, max_length=1000)
    title: str = Field(default="", max_length=300)
    author: str = Field(default="", max_length=300)
    publisher: str = Field(default="", max_length=300)
    edition: str = Field(default="", max_length=300)
    category: str = Field(default="", max_length=100)
    material: str = Field(default="", max_length=300)
    brand_model: str = Field(default="", max_length=300)
    is_print: bool | None = None
    exclude: bool = False


@review_router.post("/{sweep_id}/inventory-review")
async def review_inventory(sweep_id: str, body: InventoryReview):
    sweep = state.require_sweep(sweep_id)
    if sweep_id in state.ACTIVE_FRAMES:
        raise HTTPException(409, "Wait for this frame to finish before reviewing inventory")
    packet = sweep["packet"]
    collection = next(
        (
            entries
            for entries in (packet["books"], packet["items"])
            if any(line["id"] == body.ref_id for line in entries)
        ),
        None,
    )
    if collection is None:
        raise HTTPException(404, "Inventory line not found")
    line = next(line for line in collection if line["id"] == body.ref_id)
    evidence = {
        "source": body.source,
        "frame_ref": line["frame_ref"],
        "reviewed_at": now(),
        "kind": "claimant_review",
    }
    if body.exclude:
        collection.remove(line)
        line["exclusion"] = evidence
        line["inventory_kind"] = "book" if collection is packet["books"] else "item"
        packet.setdefault("excluded_inventory", []).append(line)
    elif collection is packet["books"]:
        for field in ("title", "author", "publisher", "edition"):
            line[field] = getattr(body, field).strip()
        line["status"] = "identified" if line["title"] else "unidentified"
        line["identity_source"] = evidence
    else:
        if not body.category.strip():
            raise HTTPException(422, "An item category is required")
        for field in ("category", "material", "brand_model"):
            line[field] = getattr(body, field).strip()
        line["category_verified"] = True
        line["identity_source"] = evidence
        if body.is_print is not None:
            line["is_print"] = body.is_print
    packet["review_queue"] = [q for q in packet["review_queue"] if q["ref_id"] != body.ref_id]
    audit(
        packet,
        "inventory.reviewed",
        ref_id=body.ref_id,
        excluded=body.exclude,
        evidence=evidence,
    )
    refresh_workflow(packet)
    state.save_active_sweep(sweep_id)
    state.schedule_live_research(sweep_id)
    return {"packet": packet}


exports_router = APIRouter(prefix="/api/sweeps", tags=["exports"])


@exports_router.get("/{sweep_id}/claim-packet")
async def claim_packet(sweep_id: str):
    packet = state.require_packet(sweep_id)
    refresh_workflow(packet)
    return build_claim_packet(packet)


@exports_router.get("/{sweep_id}/report", response_class=HTMLResponse)
def report(sweep_id: str):
    """Render the report from the latest packet so old or missing exports still open."""
    html = report_html(state.require_packet(sweep_id))
    return HTMLResponse(
        html.replace('href="../../frames/', 'href="/data/frames/').replace(
            'href="../../videos/', 'href="/data/videos/'
        )
    )


@exports_router.get("/{sweep_id}/bundle")
async def bundle(sweep_id: str):
    if sweep_id in state.ACTIVE_FRAMES:
        raise HTTPException(409, "Wait for frame processing before exporting")
    packet = state.require_packet(sweep_id)
    refresh_workflow(packet)
    try:
        content = claim_bundle(packet, state.ROOT)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return StreamingResponse(
        iter([content]),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="claim-{uuid.UUID(sweep_id)}.zip"'},
    )


class GroundTruthSubmission(BaseModel):
    ground_truth: dict


@exports_router.post("/{sweep_id}/evaluate")
async def evaluate_sweep(sweep_id: str, body: GroundTruthSubmission):
    packet = state.require_packet(sweep_id)
    try:
        result = evaluate(packet, body.ground_truth)
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise HTTPException(422, "Invalid ground truth: " + str(exc)) from exc
    packet["evaluation"] = result
    canonical = str(uuid.UUID(sweep_id))
    (state.PACKET_DIR / f"{canonical}.ground_truth.json").write_text(
        json.dumps(body.ground_truth, indent=2)
    )
    (state.PACKET_DIR / f"{canonical}.results.json").write_text(json.dumps(result, indent=2))
    audit(packet, "evaluation.completed", all_pass=result["all_pass"])
    state.save_active_sweep(sweep_id)
    return {"packet": packet, "evaluation": result}


@exports_router.get("/{sweep_id}/performance")
async def performance(sweep_id: str):
    return performance_summary(state.require_packet(sweep_id))


ROUTERS = (
    sweeps_router,
    capture_router,
    conversation_router,
    measurement_router,
    pricing_router,
    review_router,
    exports_router,
)

"""FastAPI entry point for a single-room camera sweep."""

import json, time, uuid
from pathlib import Path
from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Body, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from .schemas import SweepStart, empty_packet
from .packet import calculate_totals, report_html
from .materials import identified_materials
from .vision import inspect_frame, VISION_PROVIDER, MODEL, OLLAMA_VISION_MODEL
from .local_detector import DETECTOR_MODEL, OCR_ROLE_MODEL, VALIDATOR_DETECTOR_MODEL, ROOM_DETECTOR_MODEL, BOOK_READER_MODEL
from .logging_utils import event, log_context, trace_step
from .agent import (
    merge_observations,
    refresh_workflow,
    capture_quality,
    stage,
    audit,
    now,
)
from .conversation import Turn, respond
from .dialogue import converse
from .measurement import Calibration, RoomInput, room_geometry
from .pricing import Offer, apply_prices
from .research import research_inventory
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field
import asyncio
import copy
import os

ROOT = Path(__file__).resolve().parents[2]
FRAME_DIR, PACKET_DIR = ROOT / "data" / "frames", ROOT / "data" / "packets"
FRAME_DIR.mkdir(parents=True, exist_ok=True)
PACKET_DIR.mkdir(parents=True, exist_ok=True)
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
    event("sweep.checkpoint.saved", path=str(path))


def restore_active_sweeps() -> None:
    for path in PACKET_DIR.glob("*.active.json"):
        try:
            sweep = json.loads(path.read_text(encoding="utf-8"))
            sweep_id = sweep["packet"]["sweep"]["id"]
            SWEEPS[sweep_id] = sweep
            event("sweep.restored", sweep_id=sweep_id)
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
            event(
                "sweep.restore.skipped", level="warning", path=str(path), error=str(exc)
            )
            continue


app = FastAPI(title="Library Contents Claim Agent", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/data", StaticFiles(directory=ROOT / "data"), name="evidence")
restore_active_sweeps()
event(
    "backend.ready",
    vision_provider=VISION_PROVIDER,
    vision_model=DETECTOR_MODEL if VISION_PROVIDER == "detector" else OLLAMA_VISION_MODEL if VISION_PROVIDER == "ollama" else MODEL,
    recovered_sweeps=len(SWEEPS),
)


@app.middleware("http")
async def log_request(request: Request, call_next):
    request_id = uuid.uuid4().hex[:12]
    token = log_context.set({"request_id": request_id})
    started = time.perf_counter()
    event("http.received", method=request.method, path=request.url.path)
    try:
        response = await call_next(request)
        event(
            "http.responded",
            level="warning" if response.status_code >= 400 else "info",
            status=response.status_code,
            elapsed_s=round(time.perf_counter() - started, 3),
        )
        response.headers["X-Request-ID"] = request_id
        return response
    except Exception as exc:
        event(
            "http.failed", level="error", error_type=type(exc).__name__, error=str(exc)
        )
        raise
    finally:
        log_context.reset(token)


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "vision_model": DETECTOR_MODEL if VISION_PROVIDER == "detector" else OLLAMA_VISION_MODEL if VISION_PROVIDER == "ollama" else MODEL,
        "room_detector": ROOM_DETECTOR_MODEL if VISION_PROVIDER == "detector" else None,
        "book_reader": BOOK_READER_MODEL if VISION_PROVIDER == "detector" else None,
        "crop_verifier": os.getenv("CROP_VERIFIER_MODEL") or None,
        "ocr_role_model": OCR_ROLE_MODEL if VISION_PROVIDER == "detector" else None,
        "vision_provider": VISION_PROVIDER,
        "ocr": "Apple Vision on macOS; PaddleOCR on other platforms",
        "validator": VALIDATOR_DETECTOR_MODEL if VISION_PROVIDER == "detector" else "Independent local Ollama count check",
    }


@trace_step("sweep.create")
def start_sweep(start: SweepStart):
    sweep_id = str(uuid.uuid4())
    SWEEPS[sweep_id] = {
        "packet": empty_packet(sweep_id, start.country, start.currency, start.device),
        "started": time.time(),
        "position": 0,
        "status": "active",
    }
    SWEEPS[sweep_id]["packet"]["sweep"]["country_code"] = start.country_code
    save_active_sweep(sweep_id)
    event("sweep.created", sweep_id=sweep_id)
    refresh_workflow(SWEEPS[sweep_id]["packet"])
    SWEEPS[sweep_id]["packet"]["appraisal_threshold"] = start.appraisal_threshold
    save_active_sweep(sweep_id)
    return {"sweep_id": sweep_id, "packet": SWEEPS[sweep_id]["packet"]}


@app.post("/api/sweeps")
async def api_start(start: SweepStart):
    return start_sweep(start)


@app.post("/api/sweeps/{sweep_id}/frames")
@trace_step("frame.upload")
async def add_frame(
    sweep_id: str, image: UploadFile = File(...), shelf: str = Form("Unassigned shelf")
):
    sweep = SWEEPS.get(sweep_id)
    if sweep is None:
        raise HTTPException(404, "Sweep not found")
    if image.content_type not in {"image/jpeg"}:
        raise HTTPException(415, "Use a JPEG frame")
    if sweep.get("status") == "finished":
        raise HTTPException(
            409, "This sweep has finished; start a new sweep to capture more"
        )
    if sweep_id in ACTIVE_FRAMES:
        raise HTTPException(409, "A frame is already processing for this sweep")
    raw = await image.read(12_000_001)
    event(
        "frame.received", bytes=len(raw), shelf=shelf, content_type=image.content_type
    )
    if len(raw) > 12_000_000:
        raise HTTPException(413, "Frame must be under 12 MB")
    name = f"{sweep_id}-{uuid.uuid4().hex}.jpg"
    (FRAME_DIR / name).write_bytes(raw)
    ref = f"data/frames/{name}"
    event("frame.saved", frame_ref=ref, bytes=len(raw))
    analysis_started = time.perf_counter()
    try:
        quality = capture_quality(raw)
    except Exception:
        quality = {"warnings": ["Image quality could not be evaluated."]}
    sweep["packet"]["live_progress"] = {
        "stage": "perception",
        "shelf": shelf,
        "frame_ref": ref,
        "guidance": " ".join(quality["warnings"])
        or "Reading the spines and checking the count.",
    }
    ACTIVE_FRAMES.add(sweep_id)
    try:
        with stage(sweep["packet"], "perception") as output:
            candidate = await inspect_frame(raw, ref)
            output.update(
                vision_status=candidate.get("vision_status"),
                primary_count=candidate.get("primary_count"),
                validator_count=candidate.get("validation", {}).get("count"),
            )
    finally:
        ACTIVE_FRAMES.discard(sweep_id)
    sweep["packet"]["live_progress"] = {"stage": "ready", "shelf": shelf}

    candidate["quality"] = quality
    candidate["elapsed_s"] = round(time.perf_counter() - analysis_started, 3)
    with stage(sweep["packet"], "identification"):
        packet = merge_observations(sweep, candidate, shelf, ref)
    refresh_workflow(packet)
    save_active_sweep(sweep_id)
    schedule_live_research(sweep_id)
    return {"frame_ref": ref, "candidate": candidate, "packet": packet}


@app.post("/api/sweeps/{sweep_id}/corrections")
@app.post("/api/sweeps/{sweep_id}/turns")
@trace_step("conversation.turn")
async def add_correction(sweep_id: str, body: Turn):
    sweep = require_sweep(sweep_id)
    result = await converse(sweep["packet"], body)
    save_active_sweep(sweep_id)
    return result


@trace_step("sweep.finish")
def finish_sweep(sweep_id: str):
    sweep = SWEEPS.get(sweep_id)
    if sweep is None:
        raise HTTPException(404, "Sweep not found")
    if sweep_id in ACTIVE_FRAMES:
        raise HTTPException(409, "Wait for frame processing before finishing")
    packet = sweep["packet"]
    if sweep.get("status") != "finished" and "capture_stopped" not in sweep:
        packet["sweep"]["duration_s"] = round(time.time() - sweep["started"])
    refresh_workflow(packet)
    for frame in packet.get("frames", []):
        ref = frame.get("frame_ref", "")
        if not ref.startswith("data/frames/") or not (ROOT / ref).is_file():
            finding = {"ref_id": ref, "reason": "Referenced evidence frame is missing from disk.", "origin": "workflow"}
            if finding not in packet["review_queue"]:
                packet["review_queue"].append(finding)
    packet["sweep"]["finished_at"] = now()
    packet["sweep"]["time_to_packet_s"] = round(time.time()-sweep["capture_stopped"],3) if "capture_stopped" in sweep else None
    packet["sweep"]["packet_status"] = (
        "needs_review" if packet["review_queue"] else "ready_for_review"
    )
    event(
        "packet.totals.calculated",
        books=packet["totals"]["book_count"],
        items=len(packet["items"]),
        review_flags=len(packet["review_queue"]),
    )
    json_path, html_path = (
        PACKET_DIR / f"{sweep_id}.json",
        PACKET_DIR / f"{sweep_id}.html",
    )
    with stage(packet, "claim_packet"):
        json.dumps(packet)
        report_html(packet)
    from .performance import performance_summary
    packet["performance"] = performance_summary(packet)
    json_path.write_text(json.dumps(packet, indent=2), encoding="utf-8")
    event("packet.json.saved", path=str(json_path))
    html_path.write_text(report_html(packet), encoding="utf-8")
    event("packet.html.saved", path=str(html_path))
    claim_dir = ROOT / "data" / "claims" / sweep_id
    claim_dir.mkdir(parents=True, exist_ok=True)
    canonical_json = claim_dir / "claim_packet.json"
    canonical_report = claim_dir / "report.html"
    canonical_json.write_text(json.dumps(identified_materials(packet), indent=2), encoding="utf-8")
    canonical_report.write_text(report_html(packet).replace('href="../frames/', 'href="../../frames/').replace('href="../videos/', 'href="../../videos/'), encoding="utf-8")
    sweep["status"] = "finished"
    save_active_sweep(sweep_id)
    return {
        "packet": packet,
        "json_file": str(canonical_json.relative_to(ROOT)),
        "report_file": str(canonical_report.relative_to(ROOT)),
    }


@app.post("/api/sweeps/{sweep_id}/finish")
async def api_finish(sweep_id: str):
    sweep = require_sweep(sweep_id)
    if sweep_id in ACTIVE_FRAMES:
        raise HTTPException(409, "Wait for frame processing before finishing")
    task = LIVE_RESEARCH_TASKS.get(sweep_id)
    if task and not task.done():
        await task
    if sweep.get("status") != "finished" and (os.getenv("EBAY_ACCESS_TOKEN") or os.getenv("ENABLE_CATALOGUE_LOOKUP", "false").lower() == "true"):
        await run_research(sweep_id)
    result = finish_sweep(sweep_id)
    schedule_deferred_verification(sweep_id)
    return result


def schedule_deferred_verification(sweep_id):
    """Finish bounded live-frame checks in the background after capture stops."""
    existing = CROP_VERIFICATION_TASKS.get(sweep_id)
    if existing and not existing.done():
        return
    packet = require_sweep(sweep_id)['packet']
    pending = [line for line in packet['books'] + packet['items']
               if (line.get('crop_verification') or {}).get('status') == 'deferred' and not line.get('identity_source')]
    if not pending:
        return
    packet['verification_progress'] = {'status':'running','completed':0,'total':len(pending)}
    save_active_sweep(sweep_id)
    CROP_VERIFICATION_TASKS[sweep_id] = asyncio.create_task(verify_deferred(sweep_id, pending))


async def verify_deferred(sweep_id, pending):
    from .crop_verifier import verify_candidates
    packet = require_sweep(sweep_id)['packet']
    try:
        for index, line in enumerate(pending):
            if not line.get('identity_source') and any(line is current for current in packet['books'] + packet['items']):
                ref = line.get('frame_ref','')
                path = (ROOT / ref).resolve()
                if path.is_relative_to(FRAME_DIR.resolve()) and path.is_file():
                    candidate = dict(line, bbox=line.get('object_bbox') or line.get('bbox'))
                    is_book = line in packet['books']
                    await verify_candidates(path.read_bytes(), [candidate] if is_book else [], [] if is_book else [candidate], ref)
                    # A review received while inference ran takes precedence.
                    if not line.get('identity_source'):
                        line['crop_verification'] = candidate['crop_verification']
                        if not is_book:
                            line['category_verified'] = candidate['category_verified']
                            line['reader_category'] = candidate.get('reader_category','')
                        else:
                            from .identification import promote_verified_book
                            frame = next((frame for frame in packet.get('frames',[]) if frame['frame_ref'] == ref), {})
                            promote_verified_book(line, frame.get('validation',{}))
                else:
                    line['crop_verification'] = {'agreed':False,'status':'unavailable','reason':'Saved evidence missing'}
            packet['verification_progress']['completed'] = index + 1
            refresh_workflow(packet)
            save_active_sweep(sweep_id)
        packet['verification_progress']['status'] = 'complete'
        finish_sweep(sweep_id)
    except Exception as exc:
        packet['verification_progress'].update(status='failed', reason=type(exc).__name__)
        save_active_sweep(sweep_id)
        event('crop_verification.background.failed', level='warning', error_type=type(exc).__name__)


@app.get("/api/sweeps/{sweep_id}")
@trace_step("sweep.read")
async def get_sweep(sweep_id: str):
    sweep = require_sweep(sweep_id)
    if sweep.get('status') == 'finished':
        schedule_deferred_verification(sweep_id)
    calculate_totals(sweep["packet"])
    return sweep["packet"]


@app.get("/api/sweeps/{sweep_id}/report", response_class=HTMLResponse)
def get_report(sweep_id: str):
    """Render the report from the latest packet so old/missing exports still open."""
    sweep = require_sweep(sweep_id)
    html = report_html(sweep["packet"])
    html = html.replace('href="../frames/', 'href="/data/frames/').replace('href="../videos/', 'href="/data/videos/')
    return HTMLResponse(html)


def require_sweep(sweep_id):
    sweep = SWEEPS.get(sweep_id)
    if sweep is None:
        try:
            canonical = str(uuid.UUID(sweep_id))
            packet = json.loads((PACKET_DIR / f"{canonical}.json").read_text())
            sweep = {"packet": packet, "status": "finished", "position": 0, "started": time.time()}
            SWEEPS[sweep_id] = sweep
        except (ValueError, OSError):
            raise HTTPException(404, "Sweep not found")
    return sweep


@app.get("/api/sweeps")
async def list_sweeps():
    records = {}
    for path in sorted(
        PACKET_DIR.glob("*.json"), 
        key=lambda p: p.stat().st_mtime, reverse=True)[:100]:
        try:
            data = json.loads(path.read_text())
            packet = data.get("packet", data)
            sweep = packet["sweep"]
            records.setdefault(sweep["id"], {
                "id": sweep["id"], 
                "captured_at": sweep["captured_at"], 
                "country": sweep["country"]
            })
        except (ValueError, KeyError, OSError):
            continue
    return list(records.values())


def evidence_frame(packet, ref):
    frame = next((f for f in packet.get("frames", []) if f["frame_ref"] == ref), None)
    if frame is None:
        raise HTTPException(422, "Select a saved frame from this sweep")
    return frame


@app.get("/api/sweeps/{sweep_id}/events")
async def workflow_events(sweep_id: str, request: Request):
    sweep = require_sweep(sweep_id)

    async def updates():
        previous = ""
        expires = time.monotonic() + EVENT_STREAM_LIFETIME_S
        yield "retry: 1500\n\n"
        while time.monotonic() < expires and not await request.is_disconnected():
            packet = sweep["packet"]
            state = {
                "workflow": packet.get("workflow", {}),
                "progress": packet.get("live_progress", {}),
                "guidance": packet.get("guidance"),
                "audit_count": len(packet.get("audit_trail", [])),
                "packet": packet,
            }
            encoded = json.dumps(state)
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


@app.post("/api/sweeps/{sweep_id}/calibration")
async def calibrate(sweep_id: str, body: Calibration):
    packet = require_sweep(sweep_id)["packet"]
    frame = evidence_frame(packet, body.frame_ref)
    quality = frame.get("quality", {})
    if not quality.get("width"):
        raise HTTPException(422, "Frame dimensions are unavailable")
    packet.setdefault("calibrations", {})[body.frame_ref] = body.model_dump()
    audit(
        packet,
        "calibration.recorded",
        frame_ref=body.frame_ref,
        reference=body.reference,
    )
    refresh_workflow(packet)
    save_active_sweep(sweep_id)
    return {"packet": packet}


class SpineBounds(BaseModel):
    ref_id: str
    bbox: list[float] = Field(min_length=4, max_length=4)


@app.post("/api/sweeps/{sweep_id}/spine-bounds")
async def spine_bounds(sweep_id: str, body: SpineBounds):
    from .measurement import measure_spine

    packet = require_sweep(sweep_id)["packet"]
    book = next((b for b in packet["books"] if b["id"] == body.ref_id), None)
    if not book:
        raise HTTPException(404, "Book not found")
    calibration = packet.get("calibrations", {}).get(book["frame_ref"])
    if not calibration:
        raise HTTPException(422, "Calibrate this book's evidence frame first")
    quality = evidence_frame(packet, book["frame_ref"])["quality"]
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
    save_active_sweep(sweep_id)
    return {"packet": packet}


@app.post("/api/sweeps/{sweep_id}/room")
async def measure_room(sweep_id: str, body: RoomInput):
    packet = require_sweep(sweep_id)["packet"]
    evidence_frame(packet, body.frame_ref)
    try:
        packet["room"] = room_geometry(body)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    audit(packet, "room.measured", frame_ref=body.frame_ref, method=body.method)
    refresh_workflow(packet)
    save_active_sweep(sweep_id)
    return {"packet": packet}


@app.post("/api/sweeps/{sweep_id}/offers")
async def record_offer(sweep_id: str, body: Offer):
    packet = require_sweep(sweep_id)["packet"]
    line = next(
        (b for b in packet["books"] + packet["items"] if b["id"] == body.ref_id), None
    )
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
    save_active_sweep(sweep_id)
    return {"packet": packet}


class Locale(BaseModel):
    country: str = Field(min_length=2)
    currency: str = Field(pattern=r"^[A-Z]{3}$")


@app.post("/api/sweeps/{sweep_id}/compare-locale")
async def compare_locale(sweep_id: str, body: Locale):
    packet = require_sweep(sweep_id)["packet"]
    result = apply_prices(
        copy.deepcopy(packet), country=body.country, currency=body.currency
    )
    packet.setdefault("locale_comparisons", []).append(result)
    save_active_sweep(sweep_id)
    return result


@app.post("/api/sweeps/{sweep_id}/video")
@trace_step("video.save")
async def save_video(sweep_id: str, video: UploadFile = File(...)):
    sweep = require_sweep(sweep_id)
    mime = (video.content_type or "").split(";")[0]
    if mime not in {"video/webm", "video/mp4"}:
        raise HTTPException(415, "Use WebM or MP4 camera evidence")
    folder = ROOT / "data" / "videos"
    folder.mkdir(exist_ok=True)
    path = folder / f"{sweep_id}-{uuid.uuid4().hex}.{'mp4' if mime == 'video/mp4' else 'webm'}"
    size = 0
    try:
        with path.open("wb") as output:
            while chunk := await video.read(1024*1024):
                size += len(chunk)
                if size > 220_000_000:
                    raise HTTPException(413, "Video must be under 220 MB")
                output.write(chunk)
        if not size:
            raise HTTPException(422, "Video is empty")
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    ref = str(path.relative_to(ROOT))
    sweep["packet"].setdefault("videos", []).append({"ref": ref, "bytes": size, "mime": mime, "audio": False, "saved_at": now()})
    audit(sweep["packet"], "video.saved", ref=ref, bytes=size)
    save_active_sweep(sweep_id)
    return {"video_ref": ref}


@app.post("/api/sweeps/{sweep_id}/stop-capture")
async def stop_capture(sweep_id: str):
    sweep = require_sweep(sweep_id)
    sweep.setdefault("capture_stopped", time.time())
    sweep["packet"]["sweep"]["duration_s"] = round(sweep["capture_stopped"]-sweep["started"])
    save_active_sweep(sweep_id)
    return {"stopped_at": sweep["capture_stopped"]}


class ItemMeasurement(BaseModel):
    ref_id: str
    frame_ref: str
    w: float = Field(gt=0, le=10000, allow_inf_nan=False)
    h: float = Field(gt=0, le=10000, allow_inf_nan=False)
    d: float = Field(gt=0, le=10000, allow_inf_nan=False)
    source: str = Field(min_length=3, max_length=500)


@app.post("/api/sweeps/{sweep_id}/item-measurement")
async def item_measurement(sweep_id: str, body: ItemMeasurement):
    packet = require_sweep(sweep_id)["packet"]
    item = next((i for i in packet["items"] if i["id"] == body.ref_id), None)
    if item is None:
        raise HTTPException(404, "Non-book item not found")
    if item["frame_ref"] != body.frame_ref:
        raise HTTPException(422, "Use the selected item's saved frame")
    item["dimensions_cm"] = {"w": body.w, "h": body.h, "d": body.d}
    item["measurement"] = {"source": body.source, "frame_ref": body.frame_ref, "method": "operator supplied reference geometry"}
    audit(packet, "item.measured", ref_id=body.ref_id)
    refresh_workflow(packet)
    save_active_sweep(sweep_id)
    return {"packet": packet}


@app.post("/api/sweeps/{sweep_id}/research")
async def run_research(sweep_id: str):
    packet = require_sweep(sweep_id)["packet"]
    if packet.get("workflow", {}).get("research", {}).get("status") == "running":
        raise HTTPException(409, "Market research is already running")
    snapshot = copy.deepcopy(packet)
    with stage(packet, "research") as output:
        try:
            async with asyncio.timeout(180):
                await research_inventory(snapshot)
        except TimeoutError:
            output["warning"] = "Research time limit reached; partial results retained"
        packet["research"] = snapshot.get("research", [])
        # Apply only ISBN-backed metadata to the same still-present physical track.
        for observed in snapshot["books"]:
            source = observed.get("identity_source", {})
            current = next((b for b in packet["books"] if b["id"] == observed["id"]), None)
            if current and source.get("source") == "Open Library ISBN edition record" and not current.get("identity_source"):
                for key in ("title", "author", "publisher", "identity_source", "status"):
                    current[key] = observed[key]
        output["lines"] = len(packet["research"])
    refresh_workflow(packet)
    save_active_sweep(sweep_id)
    return {"packet": packet}

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


@app.post("/api/sweeps/{sweep_id}/inventory-review")
async def review_inventory(sweep_id: str, body: InventoryReview):
    sweep = require_sweep(sweep_id)
    if sweep_id in ACTIVE_FRAMES:
        raise HTTPException(409, "Wait for this frame to finish before reviewing inventory")
    packet = sweep["packet"]
    collection = next((entries for entries in (packet["books"], packet["items"])
                       if any(line["id"] == body.ref_id for line in entries)), None)
    if collection is None:
        raise HTTPException(404, "Inventory line not found")
    line = next(line for line in collection if line["id"] == body.ref_id)
    evidence = {"source": body.source, "frame_ref": line["frame_ref"], "reviewed_at": now(), "kind": "claimant_review"}
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
    audit(packet, "inventory.reviewed", ref_id=body.ref_id, excluded=body.exclude, evidence=evidence)
    refresh_workflow(packet)
    save_active_sweep(sweep_id)
    schedule_live_research(sweep_id)
    return {"packet": packet}


@app.get('/api/sweeps/{sweep_id}/bundle')
async def download_bundle(sweep_id: str):
    from .bundle import claim_bundle
    if sweep_id in ACTIVE_FRAMES:
        raise HTTPException(409, 'Wait for frame processing before exporting')
    packet = require_sweep(sweep_id)['packet']
    refresh_workflow(packet)
    try:
        content = claim_bundle(packet, ROOT)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return StreamingResponse(iter([content]), media_type='application/zip', headers={
        'Content-Disposition': f'attachment; filename="claim-{uuid.UUID(sweep_id)}.zip"'})


class ClaimSettings(BaseModel):
    appraisal_threshold: float = Field(gt=0, allow_inf_nan=False)


@app.post('/api/sweeps/{sweep_id}/settings')
async def claim_settings(sweep_id: str, body: ClaimSettings):
    packet = require_sweep(sweep_id)['packet']
    packet['appraisal_threshold'] = body.appraisal_threshold
    audit(packet, 'settings.updated', appraisal_threshold=body.appraisal_threshold)
    refresh_workflow(packet)
    save_active_sweep(sweep_id)
    return {'packet': packet}


class GroundTruthSubmission(BaseModel):
    ground_truth: dict


@app.post('/api/sweeps/{sweep_id}/evaluate')
async def evaluate_sweep(sweep_id: str, body: GroundTruthSubmission):
    from tools.evaluate import evaluate
    packet = require_sweep(sweep_id)['packet']
    try:
        result = evaluate(packet, body.ground_truth)
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise HTTPException(422, 'Invalid ground truth: ' + str(exc))
    packet['evaluation'] = result
    canonical = str(uuid.UUID(sweep_id))
    (PACKET_DIR / f'{canonical}.ground_truth.json').write_text(json.dumps(body.ground_truth, indent=2))
    (PACKET_DIR / f'{canonical}.results.json').write_text(json.dumps(result, indent=2))
    audit(packet, 'evaluation.completed', all_pass=result['all_pass'])
    save_active_sweep(sweep_id)
    return {'packet': packet, 'evaluation': result}


@app.get('/api/sweeps/{sweep_id}/performance')
async def get_performance(sweep_id: str):
    from .performance import performance_summary
    return performance_summary(require_sweep(sweep_id)['packet'])


async def live_research(sweep_id):
    try:
        await run_research(sweep_id)
    except Exception as exc:
        event('research.live.failed', level='warning', error_type=type(exc).__name__)


def schedule_live_research(sweep_id):
    if os.getenv('ENABLE_LIVE_RESEARCH', 'false').lower() != 'true':
        return
    if not os.getenv('EBAY_ACCESS_TOKEN') and os.getenv('ENABLE_CATALOGUE_LOOKUP', 'false').lower() != 'true':
        return
    task = LIVE_RESEARCH_TASKS.get(sweep_id)
    if task and not task.done():
        return
    packet = require_sweep(sweep_id)['packet']
    # Avoid repeated paid/source lookups for an unchanged observed inventory.
    signature = tuple((line['id'], line.get('title', ''), line.get('isbn', ''), line.get('brand_model', ''))
                      for line in packet['books'] + packet['items'])
    if signature == SWEEPS[sweep_id].get('research_signature'):
        return
    SWEEPS[sweep_id]['research_signature'] = signature
    LIVE_RESEARCH_TASKS[sweep_id] = asyncio.create_task(live_research(sweep_id))


# Built UI shares the API origin; suitable behind an HTTPS reverse proxy for phones.
FRONTEND_DIST = ROOT / 'frontend' / 'dist'
if FRONTEND_DIST.is_dir():
    app.mount('/', StaticFiles(directory=FRONTEND_DIST, html=True), name='frontend')

"""Library workflow: perception -> inventory -> measurement -> pricing -> review.

HTTP transport does not own domain rules. Every stage has saved inputs/outputs and
an audit event; model results remain observations, never prices or metric scale.
"""
import asyncio
import io
import time
import uuid
import os
from datetime import datetime, timezone
from contextlib import contextmanager
from PIL import Image, ImageFilter, ImageStat
from .logging_utils import event
from .packet import calculate_totals
from .measurement import Calibration, measure_spine
from .pricing import apply_prices
from .identification import identify_observation
from .validation import validate_packet
from .tracking import match_track, retain_observation, scene_transform


def now():
    return datetime.now(timezone.utc).isoformat()


def audit(packet, name, **fields):
    entry = {"id": uuid.uuid4().hex, "time": now(), "step": name, **fields}
    packet.setdefault("audit_trail", []).append(entry)
    event(name, sweep_id=packet["sweep"]["id"], **fields)
    return entry


@contextmanager
def stage(packet, name):
    started = time.perf_counter()
    record = {"name": name, "started_at": now(), "status": "running"}
    packet.setdefault("workflow", {})[name] = record
    audit(packet, name + ".started")
    try:
        yield record
        record["status"] = "complete"
    except Exception as exc:
        record.update(status="failed", error=str(exc))
        raise
    finally:
        record["elapsed_s"] = round(time.perf_counter()-started,3)
        packet.setdefault("stage_runs", []).append(dict(record))
        audit(packet, name + "." + record["status"], elapsed_s=record["elapsed_s"])


def capture_quality(raw):
    with Image.open(io.BytesIO(raw)) as image:
        width,height = image.size
        image.thumbnail((640,640))
        gray = image.convert("L")
        edge = ImageStat.Stat(gray.filter(ImageFilter.FIND_EDGES)).var[0]
        hist = gray.histogram()
        bright = sum(hist[250:])/sum(hist)
        dark = sum(hist[:12])/sum(hist)
    warnings = []
    if min(width,height) < 720:
        warnings.append("Low-resolution capture. Move closer so the spines fill the view.")
    if edge < 60:
        warnings.append("The view has little sharp detail. Hold still and let the camera focus.")
    if bright > .2:
        warnings.append("Large bright regions may hide text. Tilt away from glare.")
    if dark > .6:
        warnings.append("The view is dark. Add light before moving on.")
    return {"width": width, "height": height, "edge_variance": round(edge,2),
            "bright_fraction": round(bright,3), "dark_fraction": round(dark,3), "warnings": warnings,
            "method": "Image heuristics; not proof of readability or complete coverage"}


def merge_observations(sweep, candidate, shelf, ref):
    packet = sweep["packet"]
    # Evidence is retained even if the claimant excluded this shelf.
    old_books = [b for b in packet["books"] + [e for e in packet.get("excluded_inventory", []) if e.get("inventory_kind") == "book"] if b.get("shelf") == shelf]
    old_items = list(packet["items"]) + [e for e in packet.get("excluded_inventory", []) if e.get("inventory_kind") == "item"]
    transforms = {}
    if candidate.get("scene"):
        needed_refs = {item.get("frame_ref") for item in old_items} | {o.get("frame_ref") for item in old_items for o in item.get("observations", [])[-5:]}
        for old_frame in packet.get("frames", []):
            if old_frame["frame_ref"] in needed_refs and old_frame.get("scene"):
                transforms[old_frame["frame_ref"]] = scene_transform(old_frame["scene"], candidate["scene"])
    packet.setdefault("frames", []).append({
        "scene": candidate.get("scene", {}),
        "frame_ref": ref,
        "shelf": shelf,
        "vision_status": candidate.get("vision_status", "failed"),
        "count_status": candidate.get("count_status"),
        "notes": candidate.get("notes", []),
        "model": candidate.get("model"),
        "model_response": candidate.get("model_response", ""),
        "detail_response": candidate.get("detail_response", ""),
        "items_response": candidate.get("items_response", ""),
        "primary_count": candidate.get("primary_count"),
        "candidate_count": candidate.get("candidate_count", candidate.get("primary_count")),
        "books": candidate.get("books", []),
        "items": candidate.get("items", []),
        "room_detector": candidate.get("room_detector"),
        "ocr_text": candidate.get("ocr_text", []),
        "validation": candidate.get("validation", {}),
        "quality": candidate.get("quality", {}),
        "captured_at": candidate.get("captured_at", now()),
        "elapsed_s": candidate.get("elapsed_s"),
        "pipeline_stages": candidate.get("pipeline_stages", []),
    })
    if shelf in packet.get("excluded_shelves", []):
        return packet
    # A sweep accumulates evidence. Empty/failed views cannot erase earlier tracks.
    # Association consumes each old track once, so two copies in one frame stay distinct.
    for note in candidate.get("notes", []):
        packet["review_queue"].append({"ref_id": ref, "reason": str(note)})
    sweep["position"] += 1
    proposed_books = candidate.get("books", [])
    frame_books = [book for book in proposed_books if isinstance(book, dict)] if isinstance(proposed_books, list) else []
    for index, found in enumerate(frame_books):
        title = str(found.get("title") or "").strip()
        confidence = float(found.get("confidence") or 0)
        source_text = " ".join(line["text"] for line in found.get("ocr_lines", [])) if found.get("bbox_kind") == "object" else " ".join(candidate.get("ocr_text", []))
        identity = identify_observation(found, source_text, candidate.get("validation", {}))
        text_seen = identity["ocr_match"]
        identified = identity["status"] == "identified"
        book = {
            "id": str(uuid.uuid4()),
            "shelf": shelf,
            "position": index + 1,
            "frame_ref": ref,
            "status": "identified" if identified else "unidentified",
            "title": title if identified else "",
            "proposed_title": title,
            "description": str(found.get("description") or ""),
            "count_only": bool(found.get("count_only")),
            "bbox": found.get("bbox"),
            "bbox_kind": found.get("bbox_kind"),
            "object_bbox": found.get("bbox") if found.get("bbox_kind") == "object" else None,
            "ocr_lines": found.get("ocr_lines", []),
            "partial": found.get("partial", False),
            "proposed_author": str(found.get("author") or ""),
            "author": str(found.get("author") or "") if identified and str(found.get("author") or "").casefold() in source_text.casefold() else "",
            "publisher": str(found.get("publisher") or "") if identified and str(found.get("publisher") or "").casefold() in source_text.casefold() else "",
            "proposed_publisher": str(found.get("publisher") or ""),
            "appearance": found.get("appearance"),
            "reader_evidence": found.get("reader_evidence", {}),
            "crop_verification": found.get("crop_verification", {}),
            "edition": identity["edition"],
            "isbn": identity["isbn"],
            "spine_height_cm": None,
            "spine_thickness_cm": None,
            "id_confidence": confidence,
            "replacement_cost": {
                "amount": None,
                "source": "",
                "url": "",
                "retrieved_at": "",
                "converted": False,
            },
            "used_value": {
                "amount": None,
                "source": "",
                "url": "",
                "retrieved_at": "",
                "condition_assumed": "",
            },
        }
        previous = match_track(found, old_books, "book")
        if previous:
            old_books.remove(previous)
            if previous.get("exclusion"):
                continue
        book = retain_observation(packet["books"], previous, book, found, ref)
        event("inventory.book.associated", book_id=book["id"], matched=bool(previous), observations=len(book["observations"]))
        event("book.checked", book_id=book["id"], frame_ref=ref, status=book["status"],
              confidence=confidence, ocr_match=text_seen,
              count_agrees=candidate.get("validation", {}).get("agrees"))
        if not identified:
            packet["review_queue"].append(
                {
                    "ref_id": book["id"],
                    "reason": "Review this localized book and its OCR title/author proposal." if found.get("bbox_kind") == "object" else "Title lacks matching local OCR, independent count agreement or sufficient confidence.",
                }
            )
    proposed_items = candidate.get("items", [])
    frame_items = [item for item in proposed_items if isinstance(item, dict)] if isinstance(proposed_items, list) else []
    for item in frame_items:
        entry = {
            "id": str(uuid.uuid4()), "shelf": shelf,
            "category": str(item.get("category") or "unknown"),
            "description": str(item.get("description") or ""),
            "brand_model": "", "material": "", "frame_ref": ref,
            "object_bbox": item.get("bbox"), "appearance": item.get("appearance"),
            "detector": item.get("detector", candidate.get("model")),
            "category_verified": item.get("category_verified", False),
            "crop_verification": item.get("crop_verification", {}),
            "proposed_material": item.get("proposed_material", ""),
            "proposed_brand_model": item.get("proposed_brand_model", ""),
            "reader_category": item.get("reader_category", ""),
            "detector_category": item.get("detector_category", item.get("category")),
            "dismissed_by_reader": item.get("dismissed_by_reader", False),
            "dimensions_cm": {"w": None, "h": None, "d": None},
            "status": "needs_appraisal",
            "replacement_cost": {"low": None, "high": None, "source": "", "url": "", "retrieved_at": ""},
            "confidence": float(item.get("confidence") or 0),
        }
        previous = match_track(item, old_items, "item", transforms)
        if previous:
            old_items.remove(previous)
            if previous.get("exclusion"):
                continue
        saved = retain_observation(packet["items"], previous, entry, item, ref)
        event("inventory.item.associated", item_id=saved["id"], category=saved["category"], matched=bool(previous))
    if candidate.get("validation", {}).get("agrees") is not True:
        packet["review_queue"].append(
            {
                "ref_id": ref,
                "reason": "Local second model count check disagreed or was unavailable.",
            }
        )
    if candidate.get("books") and not candidate.get("ocr_text"):
        packet["review_queue"].append(
            {
                "ref_id": ref,
                "reason": "Local OCR unavailable or no text detected; titles remain unknown.",
            }
        )
    return packet


def refresh_workflow(packet):
    with stage(packet, "measurement") as output:
        calibrations = packet.get("calibrations", {})
        measured = 0
        for book in packet["books"]:
            frame = next((f for f in packet.get("frames", []) if f["frame_ref"] == book["frame_ref"]), {})
            calibration = calibrations.get(book["frame_ref"])
            if calibration and book.get("bbox") and book.get("bbox_kind") != "object":
                try:
                    quality = frame.get("quality", {})
                    book.update(measure_spine(book["bbox"], Calibration.model_validate(calibration), quality["width"], quality["height"]))
                    measured += 1
                except (ValueError, KeyError) as exc:
                    book["measurement_error"] = str(exc)
        output["measured_spines"] = measured
    with stage(packet, "pricing") as output:
        valuation = apply_prices(packet)
        output.update(priced_lines=valuation["priced"], country=valuation["country"], currency=valuation["currency"])
    with stage(packet, "validation") as output:
        validate_packet(packet)
        calculate_totals(packet)
        output["flags"] = len(packet["review_queue"])
    packet["guidance"] = next_guidance(packet)
    packet["capabilities"] = {
        "capture": {"status": "recorded" if packet.get("frames") else "waiting", "frames": len(packet.get("frames", [])), "videos": len(packet.get("videos", [])), "note": "Detection uses sampled images; continuous video is retained as evidence."},
        "books": {"status": "review_required", "candidates": len(packet["books"]), "identified": sum(bool(b.get("title")) for b in packet["books"]), "note": "Book boxes, OCR and proposed title/author/publisher; no claim of complete count without ground truth."},
        "room_items": {"status": "review_required", "candidates": len(packet["items"]), "note": "YOLO candidates include shelving, furniture, appliances, lighting, art, rugs, electronics and decor. Verify categories."},
        "measurements": {"status": "recorded" if packet["room"].get("floor_area_m2") else "needs_scale", "note": "Known metric reference required for spine dimensions, shelf run and room floor/wall areas."},
        "valuation": {"status": "review_required", "excluded_lines": packet["totals"]["excluded_from_totals"], "note": "Only checked market sources contribute to totals. Unknown prices remain excluded; originals and special editions require appraisal."},
        "exports": {"status": "draft", "note": "JSON and HTML include evidence, review findings, totals, second-market comparisons and cost/latency."},
    }
    packet["cost"] = {"model_api_cost": 0, "currency": "USD", "basis": "Local inference; excludes electricity/hardware and browser speech service", "external_pricing_cost": None if os.getenv("EBAY_ACCESS_TOKEN") else 0}
    from .performance import performance_summary
    packet["performance"] = performance_summary(packet)
    return packet


def next_guidance(packet):
    latest = (packet.get("frames") or [{}])[-1]
    if not packet.get("frames"):
        return {"code": "start", "text": f"We are documenting in {packet['sweep']['country']} using {packet['sweep']['currency']}. Pan slowly across each shelf, then the walls and floor. Keep the complete spines in view.", "action": "capture"}
    if latest.get("shelf") in packet.get("excluded_shelves", []):
        return {"code": "excluded", "text": "This shelf is excluded. Move to the next shelf and change its label.", "action": "next_shelf"}
    if latest.get("vision_status") != "ok" or latest.get("count_status") == "needs_review":
        return {"code": "retake", "text": "The book counts do not verify yet. Pause here, move closer and keep the entire stack in view.", "action": "capture"}
    if latest.get("primary_count") and not latest.get("ocr_text"):
        return {"code": "unreadable", "text": "I can log book candidates, but I cannot read their spines. Hold still and move closer without cutting off the stack.", "action": "capture"}
    art = next((i for i in packet["items"] if any(w in i.get("category", "").lower() for w in ("art", "portrait", "painting")) and "is_print" not in i), None)
    if art:
        return {"code": "art", "text": "Is the artwork an original or a print? Select the item, then tell me.", "ref_id": art["id"], "action": "answer"}
    count = len([b for b in packet["books"] if b.get("shelf") == latest.get("shelf")])
    return {"code": "continue", "text": f"Logged {count} candidate books for {latest.get('shelf', 'this view')}. Move to the next section and say next shelf. Include the walls and floor before finishing.", "action": "next_shelf"}

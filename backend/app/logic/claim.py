"""Claim packet: request schemas, totals in code, rule-based validation, output contract, bundle."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from math import isfinite
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from ..config import IGNORED_CLASSES
from .pricing import DEFAULT_APPRAISAL_THRESHOLD


class SweepStart(BaseModel):
    appraisal_threshold: float = Field(
        default=DEFAULT_APPRAISAL_THRESHOLD, gt=0, allow_inf_nan=False
    )
    country: str = ""
    currency: str = ""
    device: str = "browser camera"
    country_code: str = Field(default="", pattern=r"^([A-Z]{2})?$")


class Room(BaseModel):
    length_m: float | None = None
    width_m: float | None = None
    height_m: float | None = None
    floor_area_m2: float | None = None
    wall_area_m2: float | None = None
    shelved_wall_area_m2: float | None = None
    floor_area_ft2: float | None = None
    wall_area_ft2: float | None = None
    shelved_wall_area_ft2: float | None = None
    scale_method: str = ""
    confidence: float = 0.0


def now() -> str:
    return datetime.now(UTC).isoformat()


def empty_packet(sweep_id: str, country: str = "", currency: str = "", device: str = "") -> dict:
    return {
        "sweep": {
            "id": sweep_id,
            "captured_at": now(),
            "device": device,
            "duration_s": 0,
            "country": country,
            "currency": currency,
        },
        "room": Room().model_dump(),
        "books": [],
        "items": [],
        "totals": {},
        "review_queue": [],
    }


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def total(values) -> float:
    return float(
        sum((Decimal(str(v)) for v in values if _number(v)), Decimal(0)).quantize(
            Decimal(".01"), rounding=ROUND_HALF_UP
        )
    )


def calculate_totals(packet: dict) -> dict:
    books, items = packet.get("books", []), packet.get("items", [])
    replacement = [(b.get("replacement_cost") or {}).get("amount") for b in books]
    used = [(b.get("used_value") or {}).get("amount") for b in books]
    low = [(i.get("replacement_cost") or {}).get("low") for i in items]
    high = [(i.get("replacement_cost") or {}).get("high") for i in items]
    thickness = [b["spine_thickness_cm"] for b in books if _number(b.get("spine_thickness_cm"))]
    identified = sum(b.get("status") == "identified" or bool(b.get("title")) for b in books)
    packet["totals"] = {
        "book_count": len(books),
        "books_identified": identified,
        "books_unidentified": len(books) - identified,
        "shelf_run_m": round(sum(thickness) / 100, 3),
        "books_replacement_cost": total(replacement),
        "books_used_value": total(used),
        "items_replacement_cost_low": total(low),
        "items_replacement_cost_high": total(high),
        "excluded_from_totals": sum(
            r is None or u is None for r, u in zip(replacement, used, strict=True)
        )
        + sum(lo is None or hi is None for lo, hi in zip(low, high, strict=True)),
        # Additional, clearly named figures (the contract allows added fields).
        "item_count": len(items),
        "books_needs_appraisal": sum(b.get("status") == "needs_appraisal" for b in books),
        "items_needs_appraisal": sum(i.get("status") == "needs_appraisal" for i in items),
        "spines_measured": len(thickness),
        "shelf_run_complete": bool(books) and len(thickness) == len(books),
        "excluded_book_replacement": sum(v is None for v in replacement),
        "excluded_book_used": sum(v is None for v in used),
        "excluded_items": sum(v is None for v in low),
        "definition": (
            "Known-price subtotals only, summed in code from the lines. "
            "excluded_from_totals counts distinct lines missing at least one required value; "
            "shelf_run_m sums measured spine thickness."
        ),
    }
    return packet["totals"]


PRICE_LABEL = {"replacement": "replacement cost", "used": "used value", "item": "replacement value"}


def _price_reason(kind: str, detail: dict, country: str) -> str:
    label = PRICE_LABEL[kind]
    status = detail.get("status", "no_source")
    if status == "needs_range":
        return f"Brand/model unknown and only one comparable {label}; a sourced range needs at least two comparables."
    if status == "not_identified":
        return f"Book is unidentified, so no {label} can be matched."
    rejected = detail.get("rejected", [])
    if rejected:
        reasons = sorted({r["reason"] for r in rejected})[:3]
        return f"No accepted {label}: " + "; ".join(reasons) + "."
    return f"No retrievable {label} source for {country or 'the claim market'}; excluded from this subtotal."


def validate_packet(packet: dict) -> list[dict]:
    queue = [q for q in packet["review_queue"] if q.get("origin") != "workflow"]
    sweep, details = packet["sweep"], packet.get("price_details") or {}

    def flag(ref: str, reason: str) -> None:
        queue.append({"ref_id": ref, "reason": reason, "origin": "workflow"})

    if not sweep.get("country") or not sweep.get("currency"):
        flag("locale", "Confirm country and currency before valuation.")

    for book in packet["books"]:
        if not book.get("title"):
            flag(book["id"], "Spine title unreadable or unverified. Keep as unidentified.")
        if book.get("status") == "needs_appraisal":
            reason = (
                details.get(book["id"], {}).get("appraisal_reason")
                or "Special edition or high value: human appraisal required; prices excluded."
            )
            flag(book["id"], reason)
        if not book.get("spine_height_cm") or not book.get("spine_thickness_cm"):
            flag(
                book["id"],
                "No calibrated spine dimensions. Mark a known reference in this saved frame.",
            )
        if book.get("status") != "needs_appraisal":
            for key, kind in (("replacement_cost", "replacement"), ("used_value", "used")):
                if (book.get(key) or {}).get("amount") is None:
                    flag(
                        book["id"],
                        _price_reason(
                            kind,
                            details.get(book["id"], {}).get(kind, {}),
                            sweep.get("country", ""),
                        ),
                    )

    for item in packet["items"]:
        if item.get("dismissed_by_reader"):
            flag(
                item["id"],
                "Image reader suggests a structural object/person rather than claim contents; inspect and exclude if incorrect.",
            )
        verification = item.get("crop_verification", {})
        if verification and not verification.get("agreed"):
            flag(
                item["id"],
                "Crop verification "
                + verification.get("status", "uncertain")
                + ": detector label is excluded from the identified report.",
            )
        if not item.get("category_verified"):
            flag(
                item["id"],
                "Detector category is a proposal; verify the saved object box and exclude false detections.",
            )
        if not item.get("material"):
            flag(item["id"], "Material unknown; review visible evidence or claimant information.")
        if item.get("status") == "needs_appraisal" and details.get(item["id"], {}).get(
            "appraisal_reason"
        ):
            flag(item["id"], details[item["id"]]["appraisal_reason"])
        elif (item.get("replacement_cost") or {}).get("low") is None:
            flag(
                item["id"],
                _price_reason(
                    "item", details.get(item["id"], {}).get("item", {}), sweep.get("country", "")
                ),
            )
        if not all((item.get("dimensions_cm") or {}).get(k) for k in ("w", "h", "d")):
            flag(item["id"], "Non-book dimensions have no recorded scale evidence.")
        if not item.get("brand_model"):
            flag(item["id"], "Object brand/model unknown; use a sourced comparable range.")

    room = packet["room"]
    if not room.get("floor_area_m2"):
        flag(
            "room",
            "Room geometry missing. Supply known dimensions or calibrated geometry from this sweep.",
        )
    elif not room.get("scale_method"):
        flag("room", "Room measurement lacks a documented metric scale method.")
    if not packet.get("coverage_confirmed"):
        flag(
            "coverage",
            "Complete room coverage is not confirmed. Include every shelf, wall and floor area.",
        )

    evidence = {f.get("frame_ref") for f in packet.get("frames", [])}
    seen: set[str] = set()
    for line in packet["books"] + packet["items"]:
        ref, is_book = line["id"], "title" in line
        if ref in seen:
            flag(ref, "Duplicate inventory ID; inspect associations.")
        seen.add(ref)
        if line.get("frame_ref") not in evidence:
            flag(ref, "Inventory line lacks a saved evidence-frame record.")
        if (
            is_book
            and line.get("title")
            and line.get("id_confidence", 0) < 0.75
            and not line.get("identity_source")
        ):
            flag(ref, "Identification confidence below threshold; verify identity.")
        dims = (
            [line.get("spine_height_cm"), line.get("spine_thickness_cm")]
            if is_book
            else list((line.get("dimensions_cm") or {}).values())
        )
        if any(
            v is not None
            and (not isinstance(v, (int, float)) or not isfinite(v) or v <= 0 or v > 10000)
            for v in dims
        ):
            flag(ref, "Dimensions implausible or non-finite.")
        for key in ("replacement_cost", "used_value") if is_book else ("replacement_cost",):
            price = line.get(key) or {}
            if price.get("amount", price.get("low")) is None:
                continue
            url = urlparse(price.get("url", ""))
            if not price.get("source") or url.scheme not in {"http", "https"} or not url.hostname:
                flag(ref, "Price lacks a retrievable source URL and provider.")
            if not price.get("retrieved_at"):
                flag(ref, "Price retrieval date missing.")
            if price.get("currency") != sweep.get("currency"):
                flag(ref, "Price currency does not match claim currency.")
            if price.get("converted") and not all(
                price.get(k) for k in ("original_currency", "fx_url", "fx_date", "fx_rate")
            ):
                flag(ref, "Converted price lacks complete FX evidence.")
            if (
                price.get("verification") == "provider_matched"
                and price.get("match_score", 1) < 0.95
            ):
                flag(
                    ref,
                    f"Provider-matched price (match {price['match_score']:.2f}); confirm the listing is the same physical edition.",
                )

    excluded = set(packet.get("excluded_shelves", []))
    old_refs = {f["frame_ref"] for f in packet.get("frames", []) if f.get("shelf") in excluded}
    queue = [q for q in queue if q["ref_id"] not in old_refs]
    packet["review_queue"] = list({(q["ref_id"], q["reason"]): q for q in queue}.values())
    return packet["review_queue"]


BOOK_STATUSES = {"identified", "unidentified", "needs_appraisal"}
ITEM_STATUSES = {"priced", "range", "needs_appraisal"}


def _text(value) -> str:
    return str(value) if value not in (None, "") else ""


def _num(value) -> float | int | None:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _money(price: dict | None, keys: tuple[str, ...]) -> dict:
    price = price or {}
    out: dict = {}
    for key in keys:
        if key in {"amount", "low", "high"}:
            out[key] = _num(price.get(key))
        elif key == "converted":
            out[key] = bool(price.get("converted"))
        else:
            out[key] = _text(price.get(key))
    if price.get("amount") is not None or price.get("low") is not None:
        for key in (
            "currency",
            "condition_assumed",
            "verification",
            "match_basis",
            "match_score",
            "listing_title",
            "original_amount",
            "original_currency",
            "fx_rate",
            "fx_url",
            "fx_date",
            "fx_source",
            "provider",
        ):
            if key not in out and price.get(key) not in (None, ""):
                out[key] = price[key]
    return out


def include_book(book: dict) -> bool:
    """A detected book stays unless evidence showed it is not a book."""
    return (book.get("reader_evidence") or {}).get("is_book") is not False


def include_item(item: dict) -> bool:
    category = str(item.get("category") or "").strip().lower()
    return (
        bool(category)
        and category not in IGNORED_CLASSES | {"book"}
        and not item.get("dismissed_by_reader")
    )


def contract_book(book: dict) -> dict:
    status = (
        book.get("status")
        if book.get("status") in BOOK_STATUSES
        else ("identified" if book.get("title") else "unidentified")
    )
    measurement = book.get("measurement") or {}
    return {
        "id": book["id"],
        "shelf": _text(book.get("shelf")),
        "position": int(book.get("position") or 0),
        "frame_ref": _text(book.get("frame_ref")),
        "status": status,
        "title": _text(book.get("title")),
        "author": _text(book.get("author")),
        "edition": _text(book.get("edition")),
        "isbn": _text(book.get("isbn")),
        "spine_height_cm": _num(book.get("spine_height_cm")),
        "spine_thickness_cm": _num(book.get("spine_thickness_cm")),
        "id_confidence": round(float(book.get("id_confidence") or 0), 3),
        "replacement_cost": _money(
            book.get("replacement_cost"), ("amount", "source", "url", "retrieved_at", "converted")
        ),
        "used_value": _money(
            book.get("used_value"), ("amount", "source", "url", "retrieved_at", "condition_assumed")
        ),
        # Added fields
        "publisher": _text(book.get("publisher")),
        "identity_source": _text(
            (book.get("identity_source") or {}).get("source")
            or (book.get("identity_source") or {}).get("kind")
        ),
        "measurement_method": _text(measurement.get("method")),
        "evidence_frames": sorted(
            {o.get("frame_ref", "") for o in book.get("observations", []) if o.get("frame_ref")}
            | ({book["frame_ref"]} if book.get("frame_ref") else set())
        ),
    }


def contract_item(item: dict) -> dict:
    dims = item.get("dimensions_cm") or {}
    status = item.get("status") if item.get("status") in ITEM_STATUSES else "needs_appraisal"
    return {
        "id": item["id"],
        "category": _text(item.get("category")),
        "description": _text(item.get("description")),
        "brand_model": _text(item.get("brand_model")),
        "frame_ref": _text(item.get("frame_ref")),
        "dimensions_cm": {k: _num(dims.get(k)) for k in ("w", "h", "d")},
        "status": status,
        "replacement_cost": _money(
            item.get("replacement_cost"), ("low", "high", "source", "url", "retrieved_at")
        ),
        "confidence": round(float(item.get("confidence") or 0), 3),
        # Added fields
        "material": _text(item.get("material")),
        "shelf": _text(item.get("shelf")),
        "is_print": item.get("is_print"),
        "category_verified": bool(item.get("category_verified")),
        "measurement_source": _text((item.get("measurement") or {}).get("source")),
    }


def contract_room(room: dict) -> dict:
    return {
        "length_m": _num(room.get("length_m")),
        "width_m": _num(room.get("width_m")),
        "height_m": _num(room.get("height_m")),
        "floor_area_m2": _num(room.get("floor_area_m2")),
        "wall_area_m2": _num(room.get("wall_area_m2")),
        "shelved_wall_area_m2": _num(room.get("shelved_wall_area_m2")),
        "scale_method": _text(room.get("scale_method")),
        "confidence": float(room.get("confidence") or 0),
        # Added fields
        "floor_area_ft2": _num(room.get("floor_area_ft2")),
        "wall_area_ft2": _num(room.get("wall_area_ft2")),
        "shelved_wall_area_ft2": _num(room.get("shelved_wall_area_ft2")),
        "shape": _text(room.get("shape")),
        "source": _text(room.get("source")),
        "frame_ref": _text(room.get("frame_ref")),
        "assumptions": _text(room.get("assumptions")),
    }


def build_claim_packet(packet: dict) -> dict:
    sweep = packet["sweep"]
    books = [contract_book(b) for b in packet.get("books", []) if include_book(b)]
    items = [contract_item(i) for i in packet.get("items", []) if include_item(i)]
    exported_ids = {line["id"] for line in books + items}
    review_queue = [
        {"ref_id": _text(q.get("ref_id")), "reason": _text(q.get("reason"))}
        for q in packet.get("review_queue", [])
        if q.get("ref_id") in exported_ids
        or q.get("ref_id")
        not in {b["id"] for b in packet.get("books", [])}
        | {i["id"] for i in packet.get("items", [])}
    ]
    claim = {
        "sweep": {
            "id": sweep["id"],
            "captured_at": _text(sweep.get("captured_at")),
            "device": _text(sweep.get("device")),
            "duration_s": _num(sweep.get("duration_s")) or 0,
            "country": _text(sweep.get("country")),
            "currency": _text(sweep.get("currency")),
            "country_code": _text(sweep.get("country_code")),
            "finished_at": _text(sweep.get("finished_at")),
            "time_to_packet_s": _num(sweep.get("time_to_packet_s")),
        },
        "room": contract_room(packet.get("room", {})),
        "books": books,
        "items": items,
        "totals": {},
        "review_queue": review_queue,
    }
    claim["totals"] = calculate_totals(claim)
    claim["appraisal_threshold"] = packet.get("appraisal_threshold")
    claim["evidence_frames"] = [
        {
            "frame_ref": f.get("frame_ref", ""),
            "shelf": f.get("shelf", ""),
            "captured_at": f.get("captured_at", ""),
        }
        for f in packet.get("frames", [])
    ]
    claim["videos"] = [v.get("ref", "") for v in packet.get("videos", [])]
    claim["price_details"] = {
        k: v for k, v in (packet.get("price_details") or {}).items() if k in exported_ids
    }
    claim["fx_rates"] = packet.get("fx_rates", {})
    claim["locale_comparison"] = packet.get("locale_comparison")
    claim["performance"] = packet.get("performance", {})
    claim["cost"] = packet.get("cost", {})
    claim["methodology"] = {
        "totals": "Computed in code from the exported lines; no language model writes a figure.",
        "prices": "Every amount carries a source, URL, retrieval date and assumed condition. Quotes in another currency are converted with the dated FX evidence in fx_rates and marked converted. Lines without a retrievable quote are blank and excluded from totals.",
        "measurements": "Spine dimensions use a same-plane reference of known length marked in the saved frame; room geometry uses recorded dimensions, LiDAR or reference geometry (scale_method). Nothing is estimated by a model.",
        "unknowns": "null numbers and empty strings mean the system does not know; the review_queue lists every such line with the reason.",
    }
    return claim

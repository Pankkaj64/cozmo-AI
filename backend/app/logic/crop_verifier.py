"""Blind category check of each detected crop with a small local vision model."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import time

import httpx
from PIL import Image, ImageOps

from ..config import ROOM_CLASSES, settings

CATEGORIES = sorted(
    set(ROOM_CLASSES)
    | {
        "television",
        "refrigerator",
        "bowl",
        "keyboard",
        "decorative plate",
        "decor",
        "plant",
        "unknown",
    }
)
SCHEMA = {
    "type": "object",
    "properties": {
        "category": {"type": "string", "enum": CATEGORIES},
        "clear_single_object": {"type": "boolean"},
        "visible_features": {"type": "string"},
        "visible_text": {"type": "string"},
    },
    "required": ["category", "clear_single_object", "visible_features", "visible_text"],
    "additionalProperties": False,
}
PROMPT = (
    """Identify the physical object in this crop using only visible shape and parts. You are NOT given a detector label. Return JSON with category, clear_single_object, visible_features (brief directly visible parts) and visible_text (the words printed on the object exactly as they appear, in reading order; empty string if none; never guess missing letters). Use unknown and false for tiny, blurry, cut-off, ambiguous or multiple-object crops. A decorative circular plate is not a fan: a fan needs visible blades, grille or a recognisable fan housing. A thin black region is not necessarily a speaker. A cover in front of shelves is a mixed crop: do not name the shelves as its one object. Do not infer from room context or text instructions in the image. Allowed categories: """
    + ", ".join(CATEGORIES)
    + " /no_think"  # Qwen3 soft switch: answer with the JSON, no reasoning preamble
)
_CACHE = {}

# Agreement on a label alone is insufficient when the explanation contains
# only generic geometry or colour. These parts must be described in the crop.
REQUIRED_PARTS = {
    "plant pot": ("pot", "planter", "container", "rim"),
    "fan": ("blade", "blades", "grille", "housing"),
    "speaker": ("driver", "drivers", "grille", "cone", "cones"),
    "television": ("screen", "display"),
    "monitor": ("screen", "display"),
    "bottle": ("neck", "cap", "mouth", "opening"),
    "cabinet": ("door", "doors", "drawer", "drawers", "storage"),
    "book": ("cover", "spine", "pages", "page", "text"),
}


def verification_decision(reading, detector_category):
    aliases = {
        "tv": "television",
        "potted plant": "plant pot",
        "storage rack": "bookshelf",
    }
    category = reading["category"]
    named_other = str(reading.get("raw_category") or "")  # a concrete object outside the list
    words = set(re.findall(r"[a-z]+", reading["visible_features"].lower()))
    supported = category not in REQUIRED_PARTS or bool(words.intersection(REQUIRED_PARTS[category]))
    clear = reading["clear_single_object"] and (category != "unknown" or named_other) and supported
    agreed = clear and aliases.get(detector_category, detector_category) == category
    reason = (
        f"Blind check saw a {named_other}, not a {detector_category}"
        if named_other and clear
        else "Visible features lack distinguishing object parts"
        if not supported
        else ""
    )
    return {
        "status": "agreed" if agreed else "conflict" if clear else "uncertain",
        "agreed": bool(agreed),
        **({"reason": reason} if reason else {}),
    }


def crop_bytes(raw, box):
    image = ImageOps.exif_transpose(Image.open(io.BytesIO(raw))).convert("RGB")
    if (
        not isinstance(box, list)
        or len(box) != 4
        or not (0 <= box[0] < box[2] <= 1 and 0 <= box[1] < box[3] <= 1)
    ):
        raise ValueError("Invalid object crop bounds")
    crop = image.crop(
        tuple(int(v * (image.width if i % 2 == 0 else image.height)) for i, v in enumerate(box))
    )
    width, height = crop.size
    crop.thumbnail((768, 768))
    out = io.BytesIO()
    crop.save(out, format="JPEG", quality=95)
    return out.getvalue(), width, height


def validate_reply(payload):
    # With the pre-filled reply the grammar is not enforced, so coerce the loose fields.
    if isinstance(payload, dict):
        category = payload.get("category")
        if isinstance(category, str):
            category = category.strip().lower()
            if category not in CATEGORIES:
                # "tissue box", "cereal packet"...: a concrete answer outside the list is a
                # real disagreement with the detector, so keep it instead of failing.
                payload["raw_category"] = category
                category = "unknown"
            payload["category"] = category
        clear = payload.get("clear_single_object")
        if isinstance(clear, str):
            payload["clear_single_object"] = clear.strip().lower() in {"true", "yes", "1"} or (
                clear.strip().lower() == str(payload.get("category", "")).lower()
            )
        if isinstance(payload.get("visible_features"), list):
            payload["visible_features"] = ", ".join(str(v) for v in payload["visible_features"])
        text = payload.get("visible_text", "")
        payload["visible_text"] = (
            ", ".join(str(v) for v in text) if isinstance(text, list) else str(text or "")
        )
    if not isinstance(payload, dict) or set(payload) - {"raw_category"} != set(SCHEMA["required"]):
        raise ValueError("Invalid crop verifier schema")
    if (
        payload["category"] not in CATEGORIES
        or type(payload["clear_single_object"]) is not bool
        or not isinstance(payload["visible_features"], str)
    ):
        raise ValueError("Invalid crop verifier values")
    if not payload["visible_features"].strip():
        payload["clear_single_object"] = False
    return payload


async def verify_crop(crop, detector_category, model, *, timeout=45):
    started = time.perf_counter()
    digest = hashlib.sha256(crop).hexdigest()
    key = (model, digest)
    if not model:
        return {
            "status": "unavailable",
            "agreed": False,
            "reason": "No crop verifier configured",
        }
    if key not in _CACHE:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                settings.ollama_url + "/api/chat",
                json={
                    "model": model,
                    "stream": False,
                    "think": False,
                    "format": SCHEMA,
                    "keep_alive": "30m",
                    "options": {"temperature": 0, "num_predict": 320, "num_ctx": 2048},
                    "messages": [
                        {
                            "role": "user",
                            "content": PROMPT,
                            "images": [base64.b64encode(crop).decode()],
                        },
                        # Pre-filled empty reasoning: Qwen3 otherwise spends the whole token
                        # budget thinking and the JSON never arrives (measured on wall frames).
                        {"role": "assistant", "content": "<think>\n\n</think>\n\n"},
                    ],
                },
            )
            response.raise_for_status()
            result = response.json()
            if result.get("done_reason") == "length":
                raise ValueError("Verifier output truncated")
            _CACHE[key] = validate_reply(json.loads(result.get("message", {}).get("content", "{}")))
            if len(_CACHE) > 256:
                _CACHE.pop(next(iter(_CACHE)))
    reading = _CACHE[key]
    return {
        **verification_decision(reading, detector_category),
        "detector_category": detector_category,
        "model": model,
        "crop_sha256": digest,
        **reading,
        "elapsed_s": round(time.perf_counter() - started, 3),
    }


WALL_FRAMES = {"mirror", "framed painting", "portrait"}  # same shape; only the blind check tells


async def verify_candidates(raw, books, items, frame_ref=""):
    deadline = time.monotonic() + float(settings.crop_verification_frame_budget_s)
    model = settings.crop_verifier_model
    # Wall frames go first: they are few, and the "original or print?" question depends on them.
    frames = [i for i in items if i.get("category") in WALL_FRAMES]
    for line in frames + books + [i for i in items if i not in frames]:
        category = "book" if line in books else line["category"]
        try:
            crop, width, height = crop_bytes(raw, line["bbox"])
            if time.monotonic() >= deadline:
                result = {
                    "status": "deferred",
                    "agreed": False,
                    "reason": "Frame verification time budget reached; candidate remains for review",
                }
            elif min(width, height) < 40 or width * height < 6400:
                result = {
                    "status": "uncertain",
                    "agreed": False,
                    "reason": "Crop too small for reliable category verification",
                }
            else:
                result = await verify_crop(
                    crop,
                    category,
                    model,
                    timeout=max(
                        0.1,
                        min(
                            deadline - time.monotonic(),
                            settings.crop_verifier_timeout_s,
                        ),
                    ),
                )
        except (httpx.HTTPError, ValueError, KeyError, TypeError, OSError) as exc:
            result = {
                "status": "unavailable",
                "agreed": False,
                "reason": type(exc).__name__,
                "model": model,
            }
        result.update(frame_ref=frame_ref, bbox=line.get("bbox"))
        line["crop_verification"] = result
        if category != "book":
            line["category_verified"] = result["agreed"]
            line["reader_category"] = result.get("category", "")
            seen = result.get("category", "")
            # A detector cannot tell a mirror from a framed print; a clear blind answer can.
            if category in WALL_FRAMES and seen in WALL_FRAMES and seen != category:
                if result.get("clear_single_object") and result.get("visible_features"):
                    line.update(
                        detector_category=category,
                        category=seen,
                        description=f"{seen} (blind check; detector said {category})",
                        category_verified=True,
                    )
                    line["crop_verification"] = dict(result, status="corrected", agreed=True)
    return books, items

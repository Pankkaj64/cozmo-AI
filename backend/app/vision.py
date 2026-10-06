"""Local vision and OCR. Only individual, supported observations become candidates."""

import asyncio
import base64
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import httpx
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field
from .logging_utils import event, trace_step

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
load_dotenv(Path(__file__).resolve().parents[1] / "model_choices.env")
MODEL = os.getenv("MOONDREAM_MODEL", "moondream3.1-9B-A2B")
VISION_PROVIDER = os.getenv("VISION_PROVIDER", "detector")
OLLAMA_VISION_MODEL = os.getenv("OLLAMA_VISION_MODEL", "qwen2.5vl:3b")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
VALIDATOR_MODEL = os.getenv("VALIDATOR_MODEL", "moondream:latest")
_ocr_lock = threading.Lock()


class BookObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str
    author: str
    confidence: float = Field(ge=0, le=1)
    description: str = Field(min_length=1)
    bbox: list[float] | None = Field(default=None, min_length=4, max_length=4)


class ItemObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: str
    description: str
    confidence: float = Field(ge=0, le=1)


class ItemsOnly(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[ItemObservation] = Field(max_length=40)


class Detection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    books: list[BookObservation] = Field(max_length=60)
    items: list[ItemObservation] = Field(max_length=40)
    image_quality: str


DETECTION_SCHEMA = Detection.model_json_schema()
PROMPT = (
    "Inspect the image. List each individually visible physical book in books, including "
    "horizontal books. One entry is ONE book, never a stack or group. Do not infer books "
    "hidden outside the image. For every book, describe its visible color and position "
    "so it can be distinguished from other books. Copy title/author only when legible; "
    "otherwise use empty strings. bbox is [left, top, right, bottom] in normalized image coordinates 0..1 enclosing just the spine; use null if uncertain. Confidence means a distinct book is visible. "
    "Items is for non-book objects only. State blur, cut-off books or illegible text in "
    "image_quality; otherwise leave it empty. No prices or measurements. Return JSON."
)


def _query_moondream(image_bytes: bytes, prompt: str) -> str:
    import moondream as md
    from PIL import Image

    global _moondream
    if "_moondream" not in globals():
        _moondream = md.photon(MODEL)
    return _moondream.query(
        Image.open(io.BytesIO(image_bytes)).convert("RGB"), prompt
    ).get("answer", "")


def parse_detection(answer: str) -> tuple[dict, list[str]]:
    start, end = answer.find("{"), answer.rfind("}") + 1
    if start < 0 or end <= start:
        raise ValueError("Vision model returned no structured observations.")
    parsed = Detection.model_validate_json(answer[start:end]).model_dump()
    notes, accepted, seen = [], [], set()
    for book in parsed["books"]:
        description = " ".join(book["description"].lower().split())
        if not re.search(
            r"\b(top|bottom|left|right|spine|cover|horizontal|vertical|stack)\b",
            description,
        ):
            notes.append(
                "A book description lacked visible features or a location and was excluded."
            )
            continue
        if re.match(r"^(?:(?:a|the)\s+)?(?:horizontal\s+)?stack of\b", description):
            notes.append(
                "A grouped stack description was excluded; individual books need a clearer view."
            )
            continue
        # Generic repetition and zero-confidence placeholders are not evidence of objects.
        if book["confidence"] < 0.5 or description in {
            "book",
            "books",
            "a book",
            "unknown",
            "unidentified book",
        }:
            notes.append(
                "An unsupported book entry was excluded. Capture a clearer view of the complete stack."
            )
            continue
        if description in seen:
            notes.append(
                "Repeated identical book observations were excluded from this frame."
            )
            continue
        seen.add(description)
        book["title"] = book["title"].strip()
        book["author"] = book["author"].strip()
        accepted.append(book)
    non_books = []
    for item in parsed["items"]:
        if item["category"].strip().lower() in {
            "book",
            "books",
            "book stack",
            "stack of books",
        }:
            notes.append(
                "The model described a book group without individual detections. Its entries were not converted into a book count."
            )
        else:
            non_books.append(item)
    parsed["books"], parsed["items"] = accepted, non_books
    if parsed["image_quality"].strip().casefold() not in {"", "clear", "good", "sharp", "none", "no issues"}:
        notes.insert(0, "Capture quality: " + parsed["image_quality"].strip())
    return parsed, list(dict.fromkeys(notes))


@trace_step("model.query")
async def query_local(image_bytes: bytes, prompt: str, schema: dict) -> tuple[str, str]:
    event(
        "model.request",
        provider=VISION_PROVIDER,
        model=OLLAMA_VISION_MODEL if VISION_PROVIDER == "ollama" else MODEL,
        image_bytes=len(image_bytes),
        response_fields=list(schema.get("properties", {})),
    )
    if VISION_PROVIDER == "photon":
        return await asyncio.to_thread(_query_moondream, image_bytes, prompt), "stop"
    if VISION_PROVIDER != "ollama":
        raise ValueError(f"Unknown VISION_PROVIDER: {VISION_PROVIDER}")
    # Original full-resolution evidence remains on disk and is used by OCR.
    from PIL import Image, ImageOps
    fields = set(schema.get("properties", {}))
    budget = 64 if fields == {"count"} else 900
    maximum = 800 if fields == {"count"} else 1280
    with Image.open(io.BytesIO(image_bytes)) as source:
        resized = ImageOps.exif_transpose(source).convert("RGB")
        resized.thumbnail((maximum, maximum))
        encoded = io.BytesIO()
        resized.save(encoded, format="JPEG", quality=95)
        image_bytes = encoded.getvalue()
    async with httpx.AsyncClient(timeout=180) as client:
        response = await client.post(
            f"{OLLAMA_URL}/api/chat",
            json={
                "model": OLLAMA_VISION_MODEL,
                "stream": False,
                "format": schema,
                **({"think": False} if "qwen3" in OLLAMA_VISION_MODEL else {}),
                "keep_alive": 0,
                "options": {"temperature": 0, "num_predict": budget, "num_ctx": 4096},
                "messages": [
                    {
                        "role": "user",
                        "content": prompt,
                        "images": [base64.b64encode(image_bytes).decode("ascii")],
                    }
                ],
            },
        )
        if not response.is_success:
            raise ValueError(
                f"Local model {OLLAMA_VISION_MODEL}: {response.text[:500]}"
            )
        payload = response.json()
        event(
            "model.response",
            status=response.status_code,
            stop_reason=payload.get("done_reason"),
            response_characters=len(payload.get("message", {}).get("content", "")),
        )
        return payload.get("message", {}).get("content", ""), payload.get(
            "done_reason", ""
        )


@trace_step("books.inspect")
async def _inspect_books(image_bytes: bytes, frame_ref: str) -> dict:
    candidate = {
        "frame_ref": frame_ref,
        "books": [],
        "items": [],
        "notes": [],
        "vision_status": "failed",
        "model": OLLAMA_VISION_MODEL if VISION_PROVIDER == "ollama" else MODEL,
    }
    try:
        candidate["ocr_text"] = await run_local_ocr(image_bytes)
        candidate["ocr_status"] = "ok"
        event("ocr.result", readable_lines=len(candidate["ocr_text"]))
    except Exception as exc:
        candidate["ocr_text"] = []
        candidate["ocr_status"] = "unavailable"
        event(
            "ocr.unavailable",
            level="warning",
            error_type=type(exc).__name__,
            error=str(exc),
        )
        candidate["notes"].append(
            f"Local text recognition unavailable: {type(exc).__name__}: {exc}"
        )
    try:
        # Small local models hallucinated title lists on blurry scenes. A separate,
        # short count task avoids asking them to invent a catalog from unreadable spines.
        count_schema = {
            "type": "object",
            "properties": {"count": {"type": "integer", "minimum": 0, "maximum": 200}},
            "required": ["count"],
            "additionalProperties": False,
        }
        answer, reason = await query_local(
            image_bytes,
            'How many individual physical books are visible in this photo? Include horizontally stacked books. Count only visible books, not hidden ones. Return only JSON: {"count": integer}.',
            count_schema,
        )
        candidate["model_response"] = answer
        if reason == "length":
            raise ValueError(
                "Vision response was cut off. Capture a closer, steady view."
            )
        count = json.loads(answer).get("count")
        if type(count) is not int or not 0 <= count <= 200:
            raise ValueError("Vision model returned an invalid book count.")
        candidate["primary_count"] = count
        event("books.primary_count", count=count)
        candidate["validation"] = await validate_locally(image_bytes, count)
        if candidate["validation"].get("agrees") is not True:
            other = candidate["validation"].get("count")
            candidate["vision_status"] = "needs_review"
            event(
                "books.count_unverified",
                level="warning",
                primary_count=count,
                validator_count=other,
            )
            candidate["notes"].append(
                f"Book count needs another capture: the first model counted {count}, the independent model counted {other if other is not None else 'unknown'}. Hold still with the entire stack visible."
            )
            return candidate
        candidate["books"] = [
            {
                "title": "",
                "author": "",
                "confidence": 0,
                "description": f"Book {index + 1} of {count} visible candidates; title not verified.",
                "count_only": True,
            }
            for index in range(count)
        ]
        candidate["vision_status"] = "ok"
        event("books.count_agreed", count=count)
        if count and not candidate["ocr_text"]:
            event("titles.skipped", reason="No readable OCR text", count=count)
            candidate["notes"].append(
                f"Both models counted {count} visible book candidates. No readable title text was found. Hold still, move closer and keep every spine inside the frame."
            )
        elif count:
            # Only OCR-supported strings can survive into title proposals. A richer
            # inventory is optional and must agree with the independent count pass.
            try:
                prompt = (
                    PROMPT
                    + f" The independent count pass found {count} books. "
                    + "Only copy titles supported by these local OCR lines: "
                    + json.dumps(candidate["ocr_text"])
                    + " Schema: "
                    + json.dumps(DETECTION_SCHEMA)
                )
                details, reason = await query_local(
                    image_bytes, prompt, DETECTION_SCHEMA
                )
                candidate["detail_response"] = details
                if reason == "length":
                    raise ValueError("Title-detail response was cut off")
                parsed, notes = parse_detection(details)
                event(
                    "titles.parsed",
                    observations=len(parsed["books"]),
                    review_notes=len(notes),
                )
                candidate["notes"].extend(notes)
                if len(parsed["books"]) == count:
                    for book in parsed["books"]:
                        title = book["title"].strip()
                        if not title or not any(
                            title.lower() in line.lower()
                            for line in candidate["ocr_text"]
                        ):
                            book["title"], book["author"] = "", ""
                    event(
                        "titles.checked",
                        supported_titles=sum(
                            bool(book["title"]) for book in parsed["books"]
                        ),
                    )
                    candidate["books"] = parsed["books"]
                    candidate["items"] = parsed["items"]
                else:
                    event(
                        "titles.count_mismatch",
                        level="warning",
                        expected=count,
                        observed=len(parsed["books"]),
                    )
                    candidate["notes"].append(
                        "Title observations did not match the agreed count; titles remain unverified."
                    )
            except Exception as exc:
                event(
                    "titles.failed",
                    level="warning",
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
                candidate["notes"].append(
                    f"Book count retained, but title reading needs review: {type(exc).__name__}."
                )
    except Exception as exc:
        event(
            "books.failed", level="error", error_type=type(exc).__name__, error=str(exc)
        )
        candidate["notes"].append(
            f"Book detection failed: {type(exc).__name__}: {exc}. No reliable book count is available."
        )
    candidate.setdefault(
        "validation",
        {"count": None, "agrees": None, "uncertainty": "Primary detection failed"},
    )
    return candidate


@trace_step("frame.analyze")
async def inspect_frame(image_bytes: bytes, frame_ref: str) -> dict:
    if VISION_PROVIDER == "detector":
        from .local_detector import inspect_local_frame
        return await inspect_local_frame(image_bytes, frame_ref)
    return await inspect_legacy_frame(image_bytes, frame_ref)


async def inspect_legacy_frame(image_bytes: bytes, frame_ref: str) -> dict:
    candidate = await _inspect_books(image_bytes, frame_ref)
    # Keep household-item capture independent of whether book text can be read.
    try:
        answer, reason = await query_local(
            image_bytes,
            "List distinct visible non-book household objects ONCE each with category, a short physical description including position and confidence from 0 to 1. Stop when all visible objects are listed; never repeat entries. Exclude books, people and objects depicted in posters or photos. Return JSON with only the items list.",
            ItemsOnly.model_json_schema(),
        )
        candidate["items_response"] = answer
        if reason == "length":
            raise ValueError("Item response was cut off")
        items = ItemsOnly.model_validate_json(answer).model_dump()["items"]
        candidate["items"] = [
            item
            for item in items
            if item["confidence"] >= 0.5
            and item["category"].strip().lower()
            not in {"book", "books", "book stack", "stack of books"}
        ]
        candidate["items"] = list({(item["category"].casefold(), item["description"].casefold()): item for item in candidate["items"]}.values())
        candidate["items_status"] = "ok"
        event("items.detected", count=len(candidate["items"]))
    except Exception as exc:
        candidate["items_status"] = "needs_review"
        event(
            "items.failed",
            level="warning",
            error_type=type(exc).__name__,
            error=str(exc),
        )
        candidate["notes"].append(
            f"Non-book item detection needs review: {type(exc).__name__}."
        )
    event(
        "frame.result",
        level="info" if candidate["vision_status"] == "ok" else "warning",
        vision_status=candidate["vision_status"],
        books=len(candidate["books"]),
        items=len(candidate["items"]),
        ocr_status=candidate["ocr_status"],
        review_notes=len(candidate["notes"]),
    )
    return candidate


def _run_ocr(image_bytes: bytes, *, regions: bool = False) -> list:
    with _ocr_lock, tempfile.NamedTemporaryFile(suffix=".jpg") as image_file:
        image_file.write(image_bytes)
        image_file.flush()
        event(
            "ocr.engine",
            engine="Apple Vision" if sys.platform == "darwin" else "PaddleOCR",
        )
        if sys.platform == "darwin":
            source = Path(__file__).with_name("macos_ocr.swift")
            runtime = source.parents[1] / ".runtime"
            runtime.mkdir(exist_ok=True)
            executable = runtime / "macos-ocr"
            if (
                not executable.exists()
                or executable.stat().st_mtime < source.stat().st_mtime
            ):
                event("ocr.helper.compile.started")
                subprocess.run(
                    [
                        "swiftc",
                        "-O",
                        "-module-cache-path",
                        str(runtime / "modules"),
                        str(source),
                        "-o",
                        str(executable),
                    ],
                    check=True,
                    capture_output=True,
                    timeout=120,
                )
            event("ocr.helper.ready", executable=str(executable))
            result = subprocess.run(
                [str(executable), image_file.name],
                check=True,
                capture_output=True,
                text=True,
                timeout=8 if regions else 30,
            )
            return [
                line if regions else line["text"]
                for line in json.loads(result.stdout)
                if line["confidence"] >= 0.5
            ]
        from paddleocr import PaddleOCR

        global _ocr
        if "_ocr" not in globals():
            _ocr = PaddleOCR(
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
                lang="en",
            )
        texts = []
        for item in _ocr.predict(image_file.name):
            data = item.json if hasattr(item, "json") else item
            if isinstance(data, str):
                data = json.loads(data)
            texts.extend(data.get("res", data).get("rec_texts", []))
        return [{"text": text, "confidence": None} for text in texts] if regions else texts


@trace_step("ocr")
async def run_local_ocr(image_bytes: bytes) -> list[str]:
    def rotated_text():
        from PIL import Image, ImageOps
        original = ImageOps.exif_transpose(Image.open(io.BytesIO(image_bytes))).convert("RGB")
        lines = []
        # Spine lettering can run in either direction; OCR on only an upright
        # room frame silently misses vertical titles.
        for angle in (0, 90, 270):
            output = io.BytesIO()
            original.rotate(angle, expand=True).save(output, format="JPEG", quality=95)
            found = _run_ocr(output.getvalue())
            event("ocr.rotation", angle=angle, readable_lines=len(found))
            lines.extend(found)
        return list(dict.fromkeys(lines))
    return await asyncio.to_thread(rotated_text)


@trace_step("count.validate")
async def validate_locally(image_bytes: bytes, book_count: int) -> dict:
    event("validator.request", model=VALIDATOR_MODEL, primary_count=book_count)
    if not VALIDATOR_MODEL or (
        VISION_PROVIDER == "ollama" and VALIDATOR_MODEL == OLLAMA_VISION_MODEL
    ):
        event(
            "validator.skipped",
            level="warning",
            reason="Independent model not configured",
        )
        return {
            "count": None,
            "agrees": None,
            "uncertainty": "A different model is required for an independent count.",
        }
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{OLLAMA_URL}/api/chat",
                json={
                    "model": VALIDATOR_MODEL,
                    "stream": False,
                    "keep_alive": 0,
                    "options": {"temperature": 0, "num_predict": 80, "num_ctx": 2048},
                    "format": {
                        "type": "object",
                        "properties": {"count": {"type": "integer", "minimum": 0}},
                        "required": ["count"],
                        "additionalProperties": False,
                    },
                    "messages": [
                        {
                            "role": "user",
                            "content": 'Count individually visible physical books, including horizontal stacks. Do not guess hidden books. Return {"count": integer}.',
                            "images": [base64.b64encode(image_bytes).decode("ascii")],
                        }
                    ],
                },
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("done_reason") == "length":
                raise ValueError("Count response was truncated")
            data = json.loads(payload.get("message", {}).get("content", "{}"))
            other = data.get("count")
            if type(other) is not int or other < 0:
                raise ValueError("Missing or invalid count")
            event(
                "validator.result",
                level="info" if other == book_count else "warning",
                primary_count=book_count,
                validator_count=other,
                agrees=other == book_count,
            )
            return {"count": other, "agrees": other == book_count, "uncertainty": ""}
    except Exception as exc:
        event(
            "validator.failed",
            level="warning",
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return {
            "count": None,
            "agrees": None,
            "uncertainty": f"Local validator unavailable: {type(exc).__name__}: {exc}",
        }

"""Cross-platform OCR (PaddleOCR default, EasyOCR alternative) behind one function."""

from __future__ import annotations

import io
import json
import threading

import easyocr  # heavy, loaded on first use
import numpy as np
from paddleocr import PaddleOCR
from PIL import Image

from ..config import settings
from .utils import stamp

# One engine per language per process; loading weights is slow, reading is cheap.
_engines: dict[str, tuple[str, object]] = {}
_lock = threading.Lock()
MIN_CONFIDENCE = 0.5


def _load_engine(lang: str | None = None):
    """Create the OCR engine for `lang` once (the configured language when not given)."""
    lang = lang or settings.ocr_lang
    with _lock:
        if lang in _engines:
            return _engines[lang]
        name = settings.ocr_engine
        print(f"[DEBUG {stamp()}] ocr.engine.load engine={name} lang={lang}", flush=True)
        if name == "easyocr":
            # EasyOCR's Devanagari models are trained together with English.
            langs = [lang] if lang == "en" else [lang, "en"]
            engine = ("easyocr", easyocr.Reader(langs, gpu=False, verbose=False))
        elif name == "paddle":
            engine = (
                "paddle",
                PaddleOCR(
                    lang=lang,
                    use_doc_orientation_classify=False,
                    use_doc_unwarping=False,
                    use_textline_orientation=False,
                ),
            )
        else:
            raise ValueError(f"Unknown OCR_ENGINE: {name!r} (use 'paddle' or 'easyocr')")
        _engines[lang] = engine
        return engine


def _paddle_lines(engine, image: np.ndarray) -> list[dict]:
    lines = []
    for result in engine.predict(image):
        # PaddleOCR 3.x returns result objects with a .json view; older versions return dicts.
        data = result.json if hasattr(result, "json") else result
        if isinstance(data, str):
            data = json.loads(data)
        data = data.get("res", data)
        for text, score in zip(data.get("rec_texts", []), data.get("rec_scores", []), strict=False):
            lines.append({"text": text, "confidence": round(float(score), 3)})
    return lines


def _easyocr_lines(engine, image: np.ndarray) -> list[dict]:
    return [
        {"text": text, "confidence": round(float(score), 3)}
        for _box, text, score in engine.readtext(image)
    ]


def read_text(image_bytes: bytes, lang: str | None = None) -> list[dict]:
    """Return ``[{"text", "confidence"}]`` for one image, keeping only confident lines."""
    kind, engine = _load_engine(lang)
    image = np.array(Image.open(io.BytesIO(image_bytes)).convert("RGB"))
    with _lock:  # engines are not thread-safe
        lines = _paddle_lines(engine, image) if kind == "paddle" else _easyocr_lines(engine, image)
    return [line for line in lines if line["text"].strip() and line["confidence"] >= MIN_CONFIDENCE]


def engine_name() -> str:
    return settings.ocr_engine

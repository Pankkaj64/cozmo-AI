"""FastAPI application and all HTTP routes (transport only; rules live in the domain modules)."""

from __future__ import annotations

import asyncio
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import config
from .config import settings
from .logic import perception, state
from .logic.utils import stamp
from .routes import ROUTERS

app = FastAPI(title="Library Contents Claim Agent", version="0.3.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_request(request: Request, call_next):
    request_id = uuid.uuid4().hex[:12]
    started = time.perf_counter()
    print(
        f"[DEBUG {stamp()}] http.received request_id={request_id} "
        f"method={request.method} path={request.url.path}",
        flush=True,
    )
    try:
        response = await call_next(request)
        level = "WARN" if response.status_code >= 400 else "DEBUG"
        print(
            f"[{level} {stamp()}] http.responded request_id={request_id} "
            f"status={response.status_code} elapsed_s={round(time.perf_counter() - started, 3)}",
            flush=True,
        )
        response.headers["X-Request-ID"] = request_id
        return response
    except Exception as exc:
        print(
            f"[ERROR {stamp()}] http.failed request_id={request_id} "
            f"error_type={type(exc).__name__} error={exc}",
            flush=True,
        )
        raise


@app.on_event("startup")
async def warm_models():
    """Load the Ollama models in the background so the first frame is not a cold start."""
    asyncio.create_task(perception.warm_models())


@app.get("/api/health")
def health():
    """Which local models and price sources this backend will use."""
    return {
        "status": "ok",
        "detector": settings.detector_model,
        "validator_detector": settings.validator_detector_model,
        "room_detector": settings.room_detector_model,
        "ocr_engine": settings.ocr_engine,
        "book_reader": settings.book_reader_model,
        "crop_verifier": settings.crop_verifier_model or None,
        "conversation_model": settings.conversation_model,
        "price_providers": {
            "ebay": settings.ebay_configured,
            "google_books": settings.enable_google_books,
            "fx": settings.enable_fx_conversion,
        },
    }


# Routers first: a later static mount at "/" would otherwise swallow every path.
for router in ROUTERS:
    app.include_router(router)

for directory in (config.FRAME_DIR, config.PACKET_DIR):
    directory.mkdir(parents=True, exist_ok=True)
app.mount("/data", StaticFiles(directory=config.DATA_DIR), name="evidence")
state.restore_active_sweeps()
print(
    f"[DEBUG {stamp()}] backend.ready detector={settings.detector_model} recovered_sweeps={len(state.SWEEPS)}",
    flush=True,
)

# The built UI shares the API origin; suitable behind an HTTPS reverse proxy for phones.
if config.FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=config.FRONTEND_DIST, html=True), name="frontend")

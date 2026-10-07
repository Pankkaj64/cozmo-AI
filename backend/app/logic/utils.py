"""Logging with per-request context, stage timing and performance summaries."""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import functools
import inspect
import json
import logging
import re
import sys
import time
from collections import defaultdict
from contextlib import asynccontextmanager

logger = logging.getLogger("library_claim")
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(
        "%(asctime)s.%(msecs)03dZ [backend] %(levelname)s %(message)s",
        "%Y-%m-%dT%H:%M:%S",
    )
    formatter.converter = time.gmtime
    handler.setFormatter(formatter)
    logger.addHandler(handler)
logger.setLevel(logging.INFO)
logger.propagate = False
log_context: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "log_context", default=None
)


def current_context() -> dict:
    return log_context.get() or {}


WAIT_LOG_INTERVAL = 10


def normalized(value) -> str:
    """Lower-cased words only; shared by matching, tracking and catalogue code."""
    return " ".join(re.findall(r"\w+", str(value or "").casefold()))


def event(name: str, *, level: str = "info", **fields) -> None:
    # JSON escaping keeps errors and user-supplied labels on a single terminal line.
    details = {**current_context(), **fields}
    getattr(logger, level)("%s %s", name, json.dumps(details, default=str, ensure_ascii=True))


def trace_step(name: str):
    """Log entry, exit, errors and a waiting message every ten seconds."""

    def decorate(function):
        signature = inspect.signature(function)

        def begin(args, kwargs):
            bound = signature.bind(*args, **kwargs).arguments
            fields = {
                key: bound[key] for key in ("sweep_id", "frame_ref", "book_count") if key in bound
            }
            token = log_context.set({**current_context(), **fields})
            event(name + ".started")
            return token, time.perf_counter()

        async def heartbeat(started):
            while True:
                await asyncio.sleep(WAIT_LOG_INTERVAL)
                event(name + ".waiting", elapsed_s=round(time.perf_counter() - started, 1))

        if inspect.iscoroutinefunction(function):

            @functools.wraps(function)
            async def async_wrapper(*args, **kwargs):
                token, started = begin(args, kwargs)
                waiting = asyncio.create_task(heartbeat(started))
                try:
                    result = await function(*args, **kwargs)
                    event(
                        name + ".finished",
                        elapsed_s=round(time.perf_counter() - started, 3),
                    )
                    return result
                except asyncio.CancelledError:
                    event(name + ".cancelled", level="warning")
                    raise
                except Exception as exc:
                    event(
                        name + ".failed",
                        level="error",
                        error_type=type(exc).__name__,
                        error=str(exc)[:1000],
                    )
                    raise
                finally:
                    waiting.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await waiting
                    log_context.reset(token)

            return async_wrapper

        @functools.wraps(function)
        def sync_wrapper(*args, **kwargs):
            token, started = begin(args, kwargs)
            try:
                result = function(*args, **kwargs)
                event(
                    name + ".finished",
                    elapsed_s=round(time.perf_counter() - started, 3),
                )
                return result
            except Exception as exc:
                event(
                    name + ".failed",
                    level="error",
                    error_type=type(exc).__name__,
                    error=str(exc)[:1000],
                )
                raise
            finally:
                log_context.reset(token)

        return sync_wrapper

    return decorate


STAGES = (
    "camera",
    "frame_extraction",
    "vision_book_detection",
    "ocr_spine_reading",
    "crop_verification",
    "identification",
    "measurement",
    "pricing",
    "validation",
    "claim_packet",
)


@asynccontextmanager
async def pipeline_stage(result, name):
    if name not in STAGES:
        raise ValueError(f"Unknown pipeline stage: {name}")
    started = time.perf_counter()
    record = {"name": name, "status": "running"}
    result.setdefault("pipeline_stages", []).append(record)
    try:
        yield record
        record["status"] = "complete"
    except Exception as exc:
        record.update(status="failed", error_type=type(exc).__name__)
        raise
    finally:
        record["elapsed_s"] = round(time.perf_counter() - started, 6)


def performance_summary(packet):
    samples = defaultdict(list)
    for run in packet.get("stage_runs", []):
        if isinstance(run.get("elapsed_s"), (int, float)):
            samples[run["name"]].append(run["elapsed_s"])
    for frame in packet.get("frames", []):
        for run in frame.get("pipeline_stages", []):
            if isinstance(run.get("elapsed_s"), (int, float)):
                samples[run["name"]].append(run["elapsed_s"])
    stages = {
        name: {
            "calls": len(values),
            "total_s": round(sum(values), 3),
            "mean_s": round(sum(values) / len(values), 3),
            "max_s": max(values),
        }
        for name, values in samples.items()
    }
    return {
        "stages": stages,
        "time_to_packet_s": packet["sweep"].get("time_to_packet_s"),
        "cost": packet.get("cost", {}),
        "note": "Measured operations only. Concurrent stages must not be summed as end-to-end latency.",
    }

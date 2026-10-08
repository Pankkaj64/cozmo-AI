"""Debug printing, stage timing and performance summaries."""

from __future__ import annotations

import asyncio
import contextlib
import functools
import inspect
import re
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime


def stamp() -> str:
    """Wall-clock time for every debug print, e.g. 14:03:21.457."""
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


WAIT_LOG_INTERVAL = 10


# `\w` alone drops combining marks, which splits Hindi words ("श्रीमद्भगवद्गीता" -> "श र मद ...");
# the Indic blocks (U+0900-U+0DFF) and combining diacritics are kept as part of a word.
WORD = re.compile(r"[\w\u0300-\u036F\u0900-\u0DFF\u1CD0-\u1CFF\uA8E0-\uA8FF]+")


def normalized(value) -> str:
    """Lower-cased words only; shared by matching, tracking and catalogue code."""
    return " ".join(WORD.findall(str(value or "").casefold()))


def trace_step(name: str):
    """Print entry, exit, errors and a waiting message every ten seconds."""

    def decorate(function):
        signature = inspect.signature(function)

        def begin(args, kwargs):
            bound = signature.bind(*args, **kwargs).arguments
            fields = " ".join(
                f"{key}={bound[key]}"
                for key in ("sweep_id", "frame_ref", "book_count")
                if key in bound
            )
            print(f"[DEBUG {stamp()}] {name}.started {fields}", flush=True)
            return fields, time.perf_counter()

        async def heartbeat(fields, started):
            while True:
                await asyncio.sleep(WAIT_LOG_INTERVAL)
                elapsed = round(time.perf_counter() - started, 1)
                print(f"[DEBUG {stamp()}] {name}.waiting {fields} elapsed_s={elapsed}", flush=True)

        if inspect.iscoroutinefunction(function):

            @functools.wraps(function)
            async def async_wrapper(*args, **kwargs):
                fields, started = begin(args, kwargs)
                waiting = asyncio.create_task(heartbeat(fields, started))
                try:
                    result = await function(*args, **kwargs)
                    elapsed = round(time.perf_counter() - started, 3)
                    print(
                        f"[DEBUG {stamp()}] {name}.finished {fields} elapsed_s={elapsed}",
                        flush=True,
                    )
                    return result
                except asyncio.CancelledError:
                    print(f"[WARN {stamp()}] {name}.cancelled {fields}", flush=True)
                    raise
                except Exception as exc:
                    print(
                        f"[ERROR {stamp()}] {name}.failed {fields} "
                        f"error_type={type(exc).__name__} error={str(exc)[:1000]}",
                        flush=True,
                    )
                    raise
                finally:
                    waiting.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await waiting

            return async_wrapper

        @functools.wraps(function)
        def sync_wrapper(*args, **kwargs):
            fields, started = begin(args, kwargs)
            try:
                result = function(*args, **kwargs)
                elapsed = round(time.perf_counter() - started, 3)
                print(f"[DEBUG {stamp()}] {name}.finished {fields} elapsed_s={elapsed}", flush=True)
                return result
            except Exception as exc:
                print(
                    f"[ERROR {stamp()}] {name}.failed {fields} "
                    f"error_type={type(exc).__name__} error={str(exc)[:1000]}",
                    flush=True,
                )
                raise

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

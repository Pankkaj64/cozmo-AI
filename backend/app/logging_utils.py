"""Readable terminal events with elapsed times and per-request context."""

import asyncio
import contextlib
import contextvars
import functools
import inspect
import json
import logging
import sys
import time

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
log_context = contextvars.ContextVar("log_context", default={})
WAIT_LOG_INTERVAL = 10


def event(name: str, *, level: str = "info", **fields) -> None:
    # JSON escaping keeps errors and user-supplied labels on a single terminal line.
    details = {**log_context.get(), **fields}
    getattr(logger, level)(
        "%s %s", name, json.dumps(details, default=str, ensure_ascii=True)
    )


def trace_step(name: str):
    """Log entry, exit, errors and a waiting message every ten seconds."""

    def decorate(function):
        signature = inspect.signature(function)

        def begin(args, kwargs):
            bound = signature.bind(*args, **kwargs).arguments
            fields = {
                key: bound[key]
                for key in ("sweep_id", "frame_ref", "book_count")
                if key in bound
            }
            token = log_context.set({**log_context.get(), **fields})
            event(name + ".started")
            return token, time.perf_counter()

        async def heartbeat(started):
            while True:
                await asyncio.sleep(WAIT_LOG_INTERVAL)
                event(
                    name + ".waiting", elapsed_s=round(time.perf_counter() - started, 1)
                )

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

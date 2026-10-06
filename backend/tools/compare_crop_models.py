"""Replay identical saved crops; diagnostic labels are not a full-room evaluation."""

import argparse
import asyncio
import json
import sys
import time
import os
import httpx
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.crop_verifier import verify_crop


async def run(args):
    cases = json.loads(Path(args.cases).read_text())
    results = []
    for model in args.models:
        for case in cases:
            started = time.perf_counter()
            try:
                reading = await verify_crop(
                    Path(case["crop"]).read_bytes(),
                    case["detector"],
                    model,
                    timeout=args.timeout,
                )
            except Exception as exc:
                reading = {
                    "status": "unavailable",
                    "agreed": False,
                    "reason": (
                        str(exc) if isinstance(exc, ValueError) else type(exc).__name__
                    ),
                    "elapsed_s": round(time.perf_counter() - started, 3),
                }
            truth = case["acceptable_categories"]
            accepted = reading.get("agreed", False)
            row = {
                **case,
                "model": model,
                "reading": reading,
                "correct_accepted": accepted and case["detector"] in truth,
                "wrong_accepted": accepted and case["detector"] not in truth,
                "correct_category": reading.get("category") in truth,
                "abstained": not accepted,
            }
            results.append(row)
            summary = {
                m: {
                    "cases": sum(r["model"] == m for r in results),
                    "correct_accepted": sum(
                        r["correct_accepted"] for r in results if r["model"] == m
                    ),
                    "wrong_accepted": sum(
                        r["wrong_accepted"] for r in results if r["model"] == m
                    ),
                    "correct_categories": sum(
                        r["correct_category"] for r in results if r["model"] == m
                    ),
                    "elapsed_s": sum(
                        r["reading"].get("elapsed_s", 0)
                        for r in results
                        if r["model"] == m
                    ),
                }
                for m in args.models
            }
            Path(args.output).write_text(
                json.dumps({"summary": summary, "cases": results}, indent=2)
            )
            print(
                model,
                case["index"],
                reading.get("category"),
                reading["status"],
                reading.get("elapsed_s"),
                flush=True,
            )
        # Release weights before loading another model on memory-limited machines.
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(
                    os.getenv("OLLAMA_URL", "http://localhost:11434") + "/api/generate",
                    json={"model": model, "keep_alive": 0},
                )
        except httpx.HTTPError:
            pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--models", nargs="+", required=True)
    parser.add_argument("--timeout", type=float, default=60)
    asyncio.run(run(parser.parse_args()))

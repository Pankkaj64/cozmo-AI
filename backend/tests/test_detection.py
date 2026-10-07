"""Regression checks for real detection failures, unsupported titles and repeated views."""

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import UploadFile
from starlette.datastructures import Headers

from app import routes as capture_routes
from app.logic import state
from app.logic.claim import SweepStart


class DetectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_repeated_identified_views_match_and_empty_or_failed_views_preserve_inventory(
        self,
    ):
        candidate = {
            "books": [
                {"title": "Example title", "confidence": 0.8, "description": "Black book on top"}
            ],
            "items": [],
            "vision_status": "ok",
            "notes": [],
            "ocr_text": [],
            "validation": {"agrees": False},
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                patch.object(state, "FRAME_DIR", root),
                patch.object(state, "PACKET_DIR", root),
                patch.object(state, "SWEEPS", {}),
                patch.object(
                    capture_routes, "inspect_frame", new=AsyncMock(return_value=candidate)
                ),
            ):
                sweep_id = state.start_sweep(SweepStart())["sweep_id"]

                async def capture(shelf):
                    upload = UploadFile(
                        io.BytesIO(b"image"),
                        headers=Headers({"content-type": "image/jpeg"}),
                    )
                    return await capture_routes.add_frame(sweep_id, upload, shelf)

                await capture("Shelf 1")
                result = await capture("Shelf 1")
                self.assertEqual(result["packet"]["totals"]["book_count"], 1)
                result = await capture("Shelf 2")
                self.assertEqual(result["packet"]["totals"]["book_count"], 2)
                candidate.update(books=[], vision_status="failed")
                result = await capture("Shelf 1")
                self.assertEqual(result["packet"]["totals"]["book_count"], 2)
                candidate.update(vision_status="ok")
                result = await capture("Shelf 1")
                self.assertEqual(result["packet"]["totals"]["book_count"], 2)

    async def test_failure_reason_survives_finish(self):
        candidate = {
            "books": [],
            "items": [],
            "vision_status": "failed",
            "notes": ["Book detection failed: missing model"],
            "ocr_text": [],
            "validation": {"agrees": None},
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            frames, packets = root / "frames", root / "packets"
            frames.mkdir()
            packets.mkdir()
            with (
                patch.object(state, "ROOT", root),
                patch.object(state, "FRAME_DIR", frames),
                patch.object(state, "PACKET_DIR", packets),
                patch.object(state, "SWEEPS", {}),
                patch.object(
                    capture_routes, "inspect_frame", new=AsyncMock(return_value=candidate)
                ),
            ):
                sweep_id = state.start_sweep(SweepStart())["sweep_id"]
                upload = UploadFile(
                    io.BytesIO(b"image"),
                    headers=Headers({"content-type": "image/jpeg"}),
                )
                await capture_routes.add_frame(sweep_id, upload, "Shelf 1")
                result = state.finish_sweep(sweep_id)
                self.assertEqual(result["packet"]["frames"][0]["vision_status"], "failed")
                self.assertIn(
                    candidate["notes"][0],
                    [q["reason"] for q in result["packet"]["review_queue"]],
                )
                self.assertIn("missing model", (packets / f"{sweep_id}.json").read_text())


if __name__ == "__main__":
    unittest.main()

"""Regression checks for real detection failures, unsupported titles and repeated views."""

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import UploadFile
from starlette.datastructures import Headers
from app import main, vision
from app.schemas import SweepStart


class DetectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_non_book_items_survive_uncertain_book_count(self):
        items = {
            "items": [
                {"category": "sofa", "description": "Grey sofa", "confidence": 0.9}
            ]
        }
        with patch.object(
            vision,
            "query_local",
            new=AsyncMock(
                side_effect=[('{"count":3}', "stop"), (json.dumps(items), "stop")]
            ),
        ), patch.object(
            vision, "run_local_ocr", new=AsyncMock(return_value=[])
        ), patch.object(
            vision,
            "validate_locally",
            new=AsyncMock(return_value={"count": 2, "agrees": False}),
        ):
            result = await vision.inspect_legacy_frame(b"image", "frame.jpg")
        self.assertEqual(result["books"], [])
        self.assertEqual(result["vision_status"], "needs_review")
        self.assertEqual(result["items"][0]["category"], "sofa")

    async def inspect(self, answers=None, other=2, ocr=None, error=None):
        query = AsyncMock(side_effect=error or answers or [('{"count": 2}', "stop")])
        with patch.object(vision, "query_local", new=query), patch.object(
            vision, "run_local_ocr", new=AsyncMock(return_value=ocr or [])
        ), patch.object(
            vision,
            "validate_locally",
            new=AsyncMock(
                side_effect=lambda image, count: {
                    "count": other,
                    "agrees": other == count if other is not None else None,
                }
            ),
        ):
            return await vision._inspect_books(b"image", "frame.jpg"), query

    async def test_count_agreement_without_readable_text_never_invents_titles(self):
        result, query = await self.inspect()
        self.assertEqual(result["vision_status"], "ok")
        self.assertEqual(len(result["books"]), 2)
        self.assertTrue(all(not book["title"] for book in result["books"]))
        self.assertEqual(query.await_count, 1)

    async def test_disagreement_does_not_publish_a_count(self):
        result, _ = await self.inspect(other=5)
        self.assertEqual(result["vision_status"], "needs_review")
        self.assertEqual(result["books"], [])
        self.assertIn("first model counted 2", result["notes"][-1])

    async def test_missing_validator_cannot_confirm_count(self):
        result, _ = await self.inspect(other=None)
        self.assertEqual(result["vision_status"], "needs_review")
        self.assertEqual(result["books"], [])

    async def test_truncated_response_is_failed(self):
        result, _ = await self.inspect(answers=[("", "length")])
        self.assertEqual(result["vision_status"], "failed")
        self.assertIn("cut off", result["notes"][-1])

    async def test_missing_model_is_failed(self):
        result, _ = await self.inspect(error=ModuleNotFoundError("missing model"))
        self.assertEqual(result["vision_status"], "failed")

    async def test_invalid_count_is_failed(self):
        for count in (-1, True, "two", 999):
            result, _ = await self.inspect(
                answers=[(json.dumps({"count": count}), "stop")]
            )
            self.assertEqual(result["vision_status"], "failed")

    async def test_successful_empty_frame(self):
        result, _ = await self.inspect(answers=[('{"count":0}', "stop")], other=0)
        self.assertEqual(result["vision_status"], "ok")
        self.assertEqual(result["books"], [])

    async def test_unsupported_generated_title_is_removed(self):
        details = {
            "books": [
                {
                    "title": "Invented title",
                    "author": "Invented author",
                    "confidence": 0.9,
                    "description": "Black cover at top",
                }
            ],
            "items": [],
            "image_quality": "",
        }
        result, _ = await self.inspect(
            answers=[('{"count":1}', "stop"), (json.dumps(details), "stop")],
            other=1,
            ocr=["EXTREME OWNERSHIP"],
        )
        self.assertEqual(len(result["books"]), 1)
        self.assertEqual(result["books"][0]["title"], "")
        self.assertEqual(result["books"][0]["author"], "")

    async def test_generic_book_groups_are_not_converted_into_books(self):
        parsed, notes = vision.parse_detection(
            '{"books": [], "items": [{"category": "books", "description": "books", "confidence": 0.9}], "image_quality": ""}'
        )
        self.assertEqual(parsed["books"], [])
        self.assertEqual(parsed["items"], [])
        self.assertTrue(notes)

    async def test_zero_confidence_and_duplicate_book_details_are_excluded(self):
        book = {
            "title": "",
            "author": "",
            "description": "Black spine at top",
            "confidence": 0.8,
        }
        parsed, _ = vision.parse_detection(
            json.dumps(
                {
                    "books": [book, book, dict(book, confidence=0)],
                    "items": [],
                    "image_quality": "blur",
                }
            )
        )
        self.assertEqual(len(parsed["books"]), 1)

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
            with patch.object(main, "FRAME_DIR", root), patch.object(
                main, "PACKET_DIR", root
            ), patch.object(main, "SWEEPS", {}), patch.object(
                main, "inspect_frame", new=AsyncMock(return_value=candidate)
            ):
                sweep_id = main.start_sweep(SweepStart())["sweep_id"]

                async def capture(shelf):
                    upload = UploadFile(
                        io.BytesIO(b"image"),
                        headers=Headers({"content-type": "image/jpeg"}),
                    )
                    return await main.add_frame(sweep_id, upload, shelf)

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
                patch.object(main, "ROOT", root),
                patch.object(main, "FRAME_DIR", frames),
                patch.object(main, "PACKET_DIR", packets),
                patch.object(main, "SWEEPS", {}),
                patch.object(
                    main, "inspect_frame", new=AsyncMock(return_value=candidate)
                ),
            ):
                sweep_id = main.start_sweep(SweepStart())["sweep_id"]
                upload = UploadFile(
                    io.BytesIO(b"image"),
                    headers=Headers({"content-type": "image/jpeg"}),
                )
                await main.add_frame(sweep_id, upload, "Shelf 1")
                result = main.finish_sweep(sweep_id)
                self.assertEqual(
                    result["packet"]["frames"][0]["vision_status"], "failed"
                )
                self.assertIn(
                    candidate["notes"][0],
                    [q["reason"] for q in result["packet"]["review_queue"]],
                )
                self.assertIn(
                    "missing model", (packets / f"{sweep_id}.json").read_text()
                )


if __name__ == "__main__":
    unittest.main()

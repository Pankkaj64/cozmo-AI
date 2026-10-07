import io
import unittest
from unittest.mock import patch

from PIL import Image

from app.logic.claim import empty_packet
from app.logic.crop_verifier import (
    _CACHE,
    validate_reply,
    verification_decision,
    verify_candidates,
    verify_crop,
)


class VerifierTests(unittest.IsolatedAsyncioTestCase):
    async def test_blind_verifier_rejects_detector_fan_when_crop_is_decor(self):
        import httpx

        seen = []

        def reply(request):
            import json

            body = json.loads(request.content)
            seen.append(body)
            return httpx.Response(
                200,
                json={
                    "message": {
                        "content": '{"category":"decorative plate","clear_single_object":true,"visible_features":"ornate circular plate, no blades"}'
                    }
                },
            )

        _CACHE.clear()
        client = httpx.AsyncClient(transport=httpx.MockTransport(reply))
        with patch("app.logic.crop_verifier.httpx.AsyncClient", return_value=client):
            result = await verify_crop(b"fixture", "fan", "fixture-model")
        self.assertFalse(result["agreed"])
        self.assertEqual(result["status"], "conflict")
        self.assertNotIn("detector label: fan", seen[0]["messages"][0]["content"])

    async def test_unavailable_and_small_crops_cannot_confirm_a_label(self):
        image = io.BytesIO()
        Image.new("RGB", (200, 200)).save(image, format="JPEG")
        items = [{"category": "lamp", "bbox": [0, 0, 0.1, 0.1]}]
        _, result = await verify_candidates(image.getvalue(), [], items)
        self.assertFalse(result[0]["category_verified"])
        self.assertEqual(result[0]["crop_verification"]["status"], "uncertain")

    def test_invalid_and_empty_feature_claims_are_rejected(self):
        with self.assertRaises(ValueError):
            validate_reply(
                {"category": "fan", "clear_single_object": "yes", "visible_features": "blades"}
            )
        self.assertFalse(
            validate_reply(
                {"category": "fan", "clear_single_object": True, "visible_features": ""}
            )["clear_single_object"]
        )

    def test_matching_names_with_generic_features_are_still_uncertain(self):
        result = verification_decision(
            {
                "category": "plant pot",
                "clear_single_object": True,
                "visible_features": "circular shape, dark color",
            },
            "plant pot",
        )
        self.assertFalse(result["agreed"])
        self.assertEqual(result["status"], "uncertain")
        result = verification_decision(
            {
                "category": "television",
                "clear_single_object": True,
                "visible_features": "rectangular screen",
            },
            "television",
        )
        self.assertTrue(result["agreed"])

    def test_deferred_book_promotion_still_requires_ocr_and_count_agreement(self):
        from app.logic.identification import promote_verified_book

        line = {
            "proposed_title": "Example readable title",
            "title": "",
            "partial": False,
            "reader_evidence": {"is_book": True, "title": "Example readable title"},
            "ocr_lines": [{"text": "Example readable title", "confidence": 0.99}],
            "crop_verification": {"agreed": True},
            "proposed_author": "Invented Writer",
        }
        promote_verified_book(line, {"agrees": False})
        self.assertFalse(line["title"])
        promote_verified_book(line, {"agrees": True})
        self.assertEqual(line["title"], "Example readable title")
        self.assertEqual(line["author"], "")

    async def test_deferred_worker_checks_saved_crop_and_refreshes_exports(self):
        import tempfile
        from pathlib import Path

        from app.logic import state
        from app.logic import state as background

        packet = empty_packet("worker")
        line = {
            "id": "object",
            "category": "television",
            "frame_ref": "data/frames/one.jpg",
            "object_bbox": [0, 0, 1, 1],
            "crop_verification": {"status": "deferred", "agreed": False},
        }
        packet["items"] = [line]
        packet["verification_progress"] = {"status": "running", "total": 1, "completed": 0}

        async def verify(raw, books, items, ref):
            items[0].update(
                category_verified=True,
                reader_category="television",
                crop_verification={"agreed": True, "status": "agreed"},
            )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            frames = root / "data/frames"
            frames.mkdir(parents=True)
            (frames / "one.jpg").write_bytes(b"fixture")
            with (
                patch.object(state, "ROOT", root),
                patch.object(state, "FRAME_DIR", frames),
                patch.object(state, "SWEEPS", {"worker": {"packet": packet}}),
                patch.object(background, "refresh_workflow"),
                patch.object(state, "save_active_sweep"),
                patch.object(state, "finish_sweep") as finish,
                patch("app.logic.crop_verifier.verify_candidates", side_effect=verify) as model,
            ):
                await background.verify_deferred("worker", [line])
                model.assert_awaited_once()
                finish.assert_called_once_with("worker")
        self.assertTrue(line["category_verified"])
        self.assertEqual(
            packet["verification_progress"], {"status": "complete", "total": 1, "completed": 1}
        )

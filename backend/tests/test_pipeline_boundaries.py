"""Stage contracts must hold without loading models or calling retailers."""

import unittest

from app.logic.claim import empty_packet, validate_packet
from app.logic.identification import identify_observation
from app.logic.providers import research_inventory


class PipelineTests(unittest.TestCase):
    def test_generated_title_and_unverified_proposals_cannot_establish_identity(self):
        cases = [
            ({"title": "Invented title", "confidence": 0.99}, "Unreadable", {"agrees": True}),
            (
                {"title": "Visible title", "confidence": 0.99, "identity_verified": False},
                "Visible title",
                {"agrees": True},
            ),
            ({"title": "Visible title", "confidence": 0.99}, "Visible title", {"agrees": False}),
        ]
        for observation, text, check in cases:
            self.assertEqual(
                identify_observation(observation, text, check)["status"], "unidentified"
            )
        result = identify_observation(
            {"title": "Visible title", "confidence": 0.9}, "Visible title", {"agrees": True}
        )
        self.assertEqual(result["status"], "identified")
        self.assertEqual(result["isbn"], "")
        self.assertEqual(result["edition"], "")

    def test_validation_is_idempotent_and_preserves_perception_findings(self):
        packet = empty_packet("test", "United Kingdom", "GBP")
        packet["review_queue"] = [{"ref_id": "frame", "reason": "Count disagreement"}]
        first = list(validate_packet(packet))
        self.assertEqual(validate_packet(packet), first)
        self.assertIn({"ref_id": "frame", "reason": "Count disagreement"}, first)
        self.assertTrue(any(row["ref_id"] == "room" for row in first))


class FakeProvider:
    name = "fake"

    def __init__(self):
        self.calls = []

    async def get_book_quotes(self, book, locale, client):
        self.calls.append((book["id"], locale.country, locale.currency))
        return {"status": "not_found", "quotes": []}

    async def get_item_quotes(self, item, locale, client):
        self.calls.append((item["id"], locale.country, locale.currency))
        return {"status": "not_found", "quotes": []}


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_injected_provider_receives_locale_and_skips_appraisal_and_unidentified(self):
        provider = FakeProvider()
        packet = empty_packet("test", "United Kingdom", "GBP")
        packet["sweep"]["country_code"] = "GB"
        packet["books"] = [
            {"id": "book", "title": "Observed work"},
            {"id": "rare", "title": "Rare work", "edition": "signed"},
            {"id": "blank", "title": ""},
        ]
        packet["items"] = [{"id": "lamp", "category": "lamp"}]
        result = await research_inventory(packet, [provider])
        self.assertEqual(
            provider.calls, [("book", "United Kingdom", "GBP"), ("lamp", "United Kingdom", "GBP")]
        )
        self.assertEqual(len(result), 2)
        # Research records candidates; it never writes a price onto a line.
        self.assertNotIn("replacement_cost", packet["books"][0])
        self.assertEqual(packet["quotes"], [])
        self.assertEqual(packet["cost"]["provider_calls"]["fake"]["calls"], 2)


class StageTimingTests(unittest.IsolatedAsyncioTestCase):
    async def test_failure_keeps_duration_and_does_not_claim_completion(self):
        from app.logic.utils import pipeline_stage

        result = {}
        with self.assertRaises(RuntimeError):
            async with pipeline_stage(result, "ocr_spine_reading"):
                raise RuntimeError("reader unavailable")
        record = result["pipeline_stages"][0]
        self.assertEqual(record["status"], "failed")
        self.assertGreaterEqual(record["elapsed_s"], 0)
        self.assertEqual(record["error_type"], "RuntimeError")

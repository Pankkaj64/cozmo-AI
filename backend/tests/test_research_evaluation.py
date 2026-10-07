import unittest
from unittest.mock import patch

import httpx

from app.logic.evaluation import evaluate
from app.logic.identification import resolve_work
from app.logic.pricing import resolve_locale
from app.logic.providers import EbayProvider


class ResearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_work_match_does_not_invent_edition(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "docs": [
                        {"title": "Test Book", "key": "/works/TEST", "author_name": ["Reader"]}
                    ]
                },
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            result = await resolve_work({"title": "Test Book", "author": "Reader"}, client)
        self.assertEqual(result["status"], "work_match")
        self.assertFalse(result["edition_resolved"])
        self.assertNotIn("isbn", result)

    async def test_market_lookup_uses_locale_and_keeps_candidate_unverified(self):
        def mock(request):
            self.assertIn("deliveryCountry:AE", request.url.params["filter"])
            self.assertEqual(request.headers["x-ebay-c-marketplace-id"], "EBAY_GB")
            return httpx.Response(
                200,
                json={
                    "itemSummaries": [
                        {
                            "title": "Test Book by Reader",
                            "itemWebUrl": "https://www.ebay.com/itm/123",
                            "conditionId": "1000",
                            "condition": "New",
                            "price": {"value": "12.50", "currency": "GBP"},
                            "itemLocation": {"country": "GB"},
                        }
                    ]
                },
            )

        with patch.dict(
            "os.environ", {"EBAY_ACCESS_TOKEN": "test-token", "EBAY_MARKETPLACE_ID": "EBAY_GB"}
        ):
            async with httpx.AsyncClient(transport=httpx.MockTransport(mock)) as client:
                result = await EbayProvider().get_book_quotes(
                    {"id": "1", "title": "Test Book", "author": "Reader"},
                    resolve_locale(country_code="AE"),
                    client,
                )
        quote = result["quotes"][0]
        self.assertEqual(quote["verification"], "provider_matched")
        self.assertEqual(quote["currency"], "GBP")
        self.assertEqual(quote["seller_country"], "GB")
        self.assertEqual(quote["country"], "United Arab Emirates")

    def test_missing_truth_is_not_a_pass(self):
        packet = {"books": [], "items": [], "room": {}, "sweep": {}}
        result = evaluate(packet, {})
        self.assertFalse(result["all_pass"])
        self.assertFalse(result["dataset_meets_minimum"])

    def test_duplicate_ground_truth_mapping_rejected(self):
        packet = {"books": [], "items": [], "room": {}, "sweep": {}}
        with self.assertRaises(ValueError):
            evaluate(packet, {"books": [{"system_id": "x"}, {"system_id": "x"}]})

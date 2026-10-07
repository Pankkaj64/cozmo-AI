"""Price-details and locale-comparison endpoints."""

import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import routes as pricing_routes
from app.logic import state
from app.main import app

TODAY = date.today().isoformat()


class FakeProvider:
    name = "fake"

    async def get_book_quotes(self, book, locale, client):
        return {
            "status": "candidates",
            "quotes": [
                {
                    "ref_id": book["id"],
                    "kind": "replacement",
                    "amount": 30 if locale.currency == "GBP" else 120,
                    "currency": locale.currency,
                    "country": locale.country,
                    "country_code": locale.country_code,
                    "source": "Fake provider",
                    "url": f"https://example.com/{locale.country_code}",
                    "retrieved_at": TODAY,
                    "condition_assumed": "New",
                    "match_basis": "Title similarity",
                    "match_score": 0.97,
                    "verification": "provider_matched",
                    "provider": "fake",
                    "listing_title": book["title"],
                }
            ],
        }

    async def get_item_quotes(self, item, locale, client):
        return {"status": "not_found", "quotes": []}


class PricingApiTests(unittest.TestCase):
    def test_price_details_and_second_country_comparison(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "data/packets").mkdir(parents=True)
            with (
                patch.object(state, "ROOT", root),
                patch.object(state, "PACKET_DIR", root / "data/packets"),
                patch.object(state, "SWEEPS", {}),
                patch("app.logic.providers.configured_providers", return_value=[FakeProvider()]),
                patch("app.logic.providers.ensure_rates", return_value=0),
                patch.object(pricing_routes, "configured_providers", return_value=[FakeProvider()]),
                TestClient(app) as client,
            ):
                sid = client.post(
                    "/api/sweeps",
                    json={
                        "country": "United Arab Emirates",
                        "currency": "AED",
                        "country_code": "AE",
                    },
                ).json()["sweep_id"]
                packet = state.SWEEPS[sid]["packet"]
                packet["frames"] = [{"frame_ref": "data/frames/a.jpg", "shelf": "Shelf 1"}]
                packet["books"] = [
                    {
                        "id": "b1",
                        "title": "Observed title",
                        "author": "",
                        "frame_ref": "data/frames/a.jpg",
                        "status": "identified",
                        "id_confidence": 0.9,
                        "shelf": "Shelf 1",
                    }
                ]
                base = f"/api/sweeps/{sid}"
                self.assertEqual(client.post(base + "/research").status_code, 200)
                details = client.get(base + "/prices").json()
                self.assertEqual(details["providers"], ["fake"])
                self.assertEqual(details["details"]["b1"]["replacement"]["selected"]["amount"], 120)
                self.assertEqual(details["details"]["b1"]["used"]["status"], "no_source")
                self.assertEqual(
                    client.post(base + "/compare-locale", json={"country_code": "AE"}).status_code,
                    422,
                )
                comparison = client.post(
                    base + "/compare-locale", json={"country_code": "GB"}
                ).json()
                self.assertEqual(comparison["comparison"]["currency"], "GBP")
                self.assertEqual(comparison["rows"][0]["GBP"]["replacement_cost"]["amount"], 30)
                self.assertEqual(comparison["rows"][0]["AED"]["replacement_cost"]["amount"], 120)
                claim = client.get(base + "/claim-packet").json()
                self.assertEqual(claim["books"][0]["replacement_cost"]["amount"], 120)
                self.assertEqual(claim["locale_comparison"]["comparison"]["country_code"], "GB")
                self.assertEqual(claim["totals"]["books_replacement_cost"], 120)
                self.assertIn("provider_calls", claim["cost"])

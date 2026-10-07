"""Price details: providers return candidates, valuation decides deterministically."""

import unittest
from datetime import date
from unittest.mock import patch

import httpx

from app.logic.claim import empty_packet
from app.logic.pricing import apply_prices, match_score, resolve_locale, title_similarity
from app.logic.providers import (
    EbayAuth,
    EbayProvider,
    GoogleBooksProvider,
    compare_locale,
    ensure_rates,
    fetch_rate,
)

TODAY = date.today().isoformat()


def quote(**overrides):
    base = {
        "ref_id": "b1",
        "kind": "replacement",
        "amount": 10.0,
        "currency": "GBP",
        "country": "United Kingdom",
        "country_code": "GB",
        "source": "Test provider",
        "url": "https://example.com/listing",
        "retrieved_at": TODAY,
        "condition_assumed": "New",
        "match_basis": "Title similarity",
        "match_score": 0.95,
        "verification": "provider_matched",
        "provider": "test",
        "listing_title": "Observed title",
    }
    return {**base, **overrides}


def packet(country="United Arab Emirates", currency="AED", code="AE"):
    p = empty_packet("test", country, currency)
    p["sweep"]["country_code"] = code
    p["books"] = [
        {
            "id": "b1",
            "title": "Observed title",
            "author": "Jane Author",
            "frame_ref": "f",
            "status": "identified",
            "id_confidence": 0.9,
        }
    ]
    p["items"] = [{"id": "i1", "category": "lamp", "brand_model": "", "frame_ref": "f"}]
    return p


class MatchingTests(unittest.TestCase):
    def test_title_similarity_and_author_weighting(self):
        self.assertGreaterEqual(
            title_similarity("Observed title", "Observed Title (Paperback) by Jane Author"), 0.9
        )
        self.assertLess(title_similarity("Observed title", "Completely different work"), 0.5)
        strong, _ = match_score(
            {"title": "Observed title", "author": "Jane Author"}, "Observed title by Jane Author"
        )
        weak, basis = match_score(
            {"title": "Observed title", "author": "Jane Author"}, "Observed title by Someone Else"
        )
        self.assertGreater(strong, weak)
        self.assertIn("surname not found", basis)
        self.assertEqual(match_score({"title": "x"}, "y", isbn_match=True)[0], 1.0)


class ValuationTests(unittest.TestCase):
    def test_provider_quote_in_claim_currency_is_applied_with_full_provenance(self):
        p = packet("United Kingdom", "GBP", "GB")
        p["quotes"] = [quote(), quote(kind="used", amount=4, condition_assumed="Good")]
        apply_prices(p)
        book = p["books"][0]
        self.assertEqual(book["replacement_cost"]["amount"], 10)
        self.assertEqual(book["replacement_cost"]["verification"], "provider_matched")
        self.assertEqual(book["replacement_cost"]["retrieved_at"], TODAY)
        self.assertFalse(book["replacement_cost"]["converted"])
        self.assertEqual(book["used_value"]["condition_assumed"], "Good")
        detail = p["price_details"]["b1"]
        self.assertEqual(detail["replacement"]["status"], "priced")
        self.assertEqual(len(detail["replacement"]["candidates"]), 1)

    def test_weak_match_and_ebook_quotes_are_rejected_with_reasons(self):
        p = packet("United Kingdom", "GBP", "GB")
        p["quotes"] = [
            quote(match_score=0.5),
            quote(is_ebook=True, url="https://example.com/ebook"),
        ]
        apply_prices(p)
        self.assertIsNone(p["books"][0]["replacement_cost"]["amount"])
        reasons = [r["reason"] for r in p["price_details"]["b1"]["replacement"]["rejected"]]
        self.assertTrue(any("does not match" in r for r in reasons))
        self.assertTrue(any("Ebook" in r for r in reasons))
        self.assertEqual(p["price_details"]["b1"]["replacement"]["status"], "no_source")

    def test_foreign_quote_needs_recorded_fx_and_is_labelled_converted(self):
        p = packet()
        p["quotes"] = [quote()]
        apply_prices(p)
        self.assertIsNone(p["books"][0]["replacement_cost"]["amount"])
        self.assertIn(
            "No dated FX evidence", p["price_details"]["b1"]["replacement"]["rejected"][0]["reason"]
        )
        p["fx_rates"] = {
            "GBP->AED": {
                "rate": 4.8,
                "fx_url": "https://api.frankfurter.app/latest?from=GBP&to=AED",
                "fx_date": "2026-10-06",
                "fx_source": "Frankfurter",
            }
        }
        apply_prices(p)
        price = p["books"][0]["replacement_cost"]
        self.assertEqual(price["amount"], 48)
        self.assertTrue(price["converted"])
        self.assertEqual(price["original_currency"], "GBP")
        self.assertEqual(price["currency"], "AED")
        self.assertEqual(price["fx_rate"], 4.8)

    def test_operator_offer_outranks_provider_and_local_outranks_converted(self):
        p = packet()
        p["fx_rates"] = {
            "GBP->AED": {
                "rate": 4.8,
                "fx_url": "https://example.com/fx",
                "fx_date": TODAY,
                "fx_source": "fx",
            }
        }
        p["quotes"] = [
            quote(),
            quote(
                currency="AED",
                country="United Arab Emirates",
                country_code="AE",
                amount=40,
                url="https://example.com/local",
            ),
        ]
        apply_prices(p)
        self.assertEqual(p["books"][0]["replacement_cost"]["url"], "https://example.com/local")
        p["offers"] = [
            {
                "ref_id": "b1",
                "kind": "replacement",
                "country": "United Arab Emirates",
                "currency": "AED",
                "amount": 42,
                "source": "Checked retailer",
                "url": "https://example.com/checked",
                "retrieved_at": TODAY,
                "condition_assumed": "New",
                "match_basis": "Exact edition",
                "verified": True,
            }
        ]
        apply_prices(p)
        self.assertEqual(p["books"][0]["replacement_cost"]["amount"], 42)
        self.assertEqual(p["books"][0]["replacement_cost"]["verification"], "operator_checked")

    def test_item_range_from_recorded_comparables_and_threshold_routes_to_appraisal(self):
        p = packet("United Kingdom", "GBP", "GB")
        comparables = [
            {"url": "https://example.com/1", "amount": 15, "currency": "GBP"},
            {"url": "https://example.com/2", "amount": 25, "currency": "GBP"},
        ]
        p["quotes"] = [quote(ref_id="i1", kind="item", amount=20, comparables=comparables)]
        apply_prices(p)
        self.assertEqual(p["items"][0]["replacement_cost"]["low"], 15)
        self.assertEqual(p["items"][0]["replacement_cost"]["high"], 25)
        self.assertEqual(p["items"][0]["status"], "range")
        p["quotes"] = [quote(amount=5000)]
        apply_prices(p)
        self.assertEqual(p["books"][0]["status"], "needs_appraisal")
        self.assertIsNone(p["books"][0]["replacement_cost"]["amount"])
        self.assertIn("threshold", p["price_details"]["b1"]["appraisal_reason"])

    def test_unidentified_books_are_never_priced(self):
        p = packet("United Kingdom", "GBP", "GB")
        p["books"][0]["title"] = ""
        p["quotes"] = [quote()]
        apply_prices(p)
        self.assertIsNone(p["books"][0]["replacement_cost"]["amount"])
        self.assertEqual(p["price_details"]["b1"]["replacement"]["status"], "not_identified")


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_google_books_marks_ebooks_and_records_country(self):
        def reply(request):
            self.assertEqual(request.url.params["country"], "GB")
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "volumeInfo": {
                                "title": "Observed title",
                                "authors": ["Jane Author"],
                                "publisher": "Press",
                                "infoLink": "https://books.google.com/x",
                                "industryIdentifiers": [
                                    {"type": "ISBN_13", "identifier": "9780306406157"}
                                ],
                            },
                            "saleInfo": {
                                "saleability": "FOR_SALE",
                                "isEbook": True,
                                "listPrice": {"amount": 7.99, "currencyCode": "GBP"},
                                "buyLink": "https://play.google.com/x",
                            },
                        }
                    ]
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
            result = await GoogleBooksProvider().get_book_quotes(
                {"id": "b1", "title": "Observed title", "author": "Jane Author"},
                resolve_locale(country_code="GB"),
                client,
            )
        q = result["quotes"][0]
        self.assertTrue(q["is_ebook"])
        self.assertEqual(q["currency"], "GBP")
        self.assertEqual(q["url"], "https://play.google.com/x")
        self.assertGreaterEqual(q["match_score"], 0.9)
        self.assertEqual(result["catalogue"][0]["isbn13"], "9780306406157")

    async def test_ebay_median_quote_keeps_every_comparable_and_fetches_token(self):
        calls = []

        def reply(request):
            calls.append(request.url.path)
            if request.url.path.endswith("/oauth2/token"):
                return httpx.Response(
                    200, json={"access_token": "fixture-token", "expires_in": 7200}
                )
            summaries = [
                {
                    "title": f"Observed title by Jane Author {i}",
                    "itemWebUrl": f"https://www.ebay.co.uk/itm/{i}",
                    "condition": "Good",
                    "price": {"value": str(v), "currency": "GBP"},
                }
                for i, v in enumerate((9, 5, 7))
            ]
            return httpx.Response(200, json={"itemSummaries": summaries})

        with patch.dict(
            "os.environ",
            {"EBAY_CLIENT_ID": "id", "EBAY_CLIENT_SECRET": "secret", "EBAY_ACCESS_TOKEN": ""},
        ):
            async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
                result = await EbayProvider(EbayAuth()).get_book_quotes(
                    {"id": "b1", "title": "Observed title", "author": "Jane Author"},
                    resolve_locale(country_code="GB"),
                    client,
                )
        self.assertEqual(calls.count("/identity/v1/oauth2/token"), 1)
        used = next(q for q in result["quotes"] if q["kind"] == "used")
        self.assertEqual(used["amount"], 7)
        self.assertEqual([c["amount"] for c in used["comparables"]], [5, 7, 9])
        self.assertIn("median of 3", used["source"])

    async def test_fx_falls_back_and_records_evidence(self):
        def reply(request):
            if "frankfurter" in request.url.host:
                return httpx.Response(404, json={"message": "not found"})
            return httpx.Response(
                200,
                json={
                    "rates": {"AED": 4.6},
                    "time_last_update_utc": "Mon, 06 Oct 2026 00:00:01 +0000",
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
            evidence = await fetch_rate("GBP", "AED", client)
            p = {"fx_rates": {}}
            calls = await ensure_rates(p, {"GBP", "AED"}, "AED", client)
        self.assertEqual(evidence["rate"], 4.6)
        self.assertIn("open.er-api.com", evidence["fx_url"])
        self.assertEqual(calls, 1)
        self.assertIn("GBP->AED", p["fx_rates"])


class ComparisonProvider:
    name = "comparison"

    async def get_book_quotes(self, book, locale, client):
        return {
            "status": "candidates",
            "quotes": [
                quote(
                    ref_id=book["id"],
                    currency=locale.currency,
                    country=locale.country,
                    country_code=locale.country_code,
                    amount=20 if locale.currency == "GBP" else 90,
                    url=f"https://example.com/{locale.country_code}/{book['id']}",
                )
            ],
        }

    async def get_item_quotes(self, item, locale, client):
        return {"status": "not_found", "quotes": []}


class ComparisonTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_ten_books_are_priced_in_a_second_country(self):
        p = packet()
        p["books"] = [
            dict(p["books"][0], id=f"b{i}", title=f"Work {i}", id_confidence=1 - i / 100)
            for i in range(12)
        ]
        p["books"][0]["edition"] = "signed"
        p["quotes"] = [
            quote(
                ref_id=f"b{i}",
                currency="AED",
                country="United Arab Emirates",
                country_code="AE",
                amount=90,
            )
            for i in range(12)
        ]
        apply_prices(p)
        with patch("app.logic.providers.ensure_rates", return_value=0):
            result = await compare_locale(
                p, resolve_locale(country_code="GB"), [ComparisonProvider()]
            )
        self.assertEqual(len(result["rows"]), 10)
        self.assertNotIn("b0", result["book_ids"])
        row = result["rows"][0]
        self.assertEqual(row["AED"]["replacement_cost"]["amount"], 90)
        self.assertEqual(row["GBP"]["replacement_cost"]["amount"], 20)
        self.assertEqual(
            row["GBP"]["replacement_cost"]["url"], f"https://example.com/GB/{row['ref_id']}"
        )
        # The claim-locale prices on the packet are untouched by the comparison.
        self.assertEqual(p["books"][1]["replacement_cost"]["amount"], 90)
        self.assertEqual(p["locale_comparison"]["comparison"]["currency"], "GBP")

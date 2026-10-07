"""The exported claim_packet.json must follow the brief's output contract exactly."""

import unittest
from datetime import date

from app.logic.claim import build_claim_packet, empty_packet
from app.logic.report import report_html
from app.logic.workflow import refresh_workflow

CONTRACT_SWEEP = ["id", "captured_at", "device", "duration_s", "country", "currency"]
CONTRACT_ROOM = [
    "length_m",
    "width_m",
    "height_m",
    "floor_area_m2",
    "wall_area_m2",
    "shelved_wall_area_m2",
    "scale_method",
    "confidence",
]
CONTRACT_BOOK = [
    "id",
    "shelf",
    "position",
    "frame_ref",
    "status",
    "title",
    "author",
    "edition",
    "isbn",
    "spine_height_cm",
    "spine_thickness_cm",
    "id_confidence",
    "replacement_cost",
    "used_value",
]
CONTRACT_ITEM = [
    "id",
    "category",
    "description",
    "brand_model",
    "frame_ref",
    "dimensions_cm",
    "status",
    "replacement_cost",
    "confidence",
]
CONTRACT_TOTALS = [
    "book_count",
    "books_identified",
    "books_unidentified",
    "shelf_run_m",
    "books_replacement_cost",
    "books_used_value",
    "items_replacement_cost_low",
    "items_replacement_cost_high",
    "excluded_from_totals",
]


def packet():
    p = empty_packet("test", "United Kingdom", "GBP")
    p["frames"] = [
        {
            "frame_ref": "data/frames/a.jpg",
            "shelf": "Shelf 1",
            "quality": {"width": 1000, "height": 500},
        }
    ]
    p["books"] = [
        {
            "id": "b1",
            "shelf": "Shelf 1",
            "position": 1,
            "frame_ref": "data/frames/a.jpg",
            "title": "Observed title",
            "author": "Observed author",
            "publisher": "Press",
            "status": "identified",
            "id_confidence": 0.9,
            "spine_height_cm": 20.0,
            "spine_thickness_cm": 3.0,
        },
        {
            "id": "b2",
            "shelf": "Shelf 1",
            "position": 2,
            "frame_ref": "data/frames/a.jpg",
            "title": "",
            "proposed_title": "Guessed",
            "status": "unidentified",
            "spine_height_cm": None,
            "spine_thickness_cm": None,
        },
        {
            "id": "notabook",
            "shelf": "Shelf 1",
            "frame_ref": "data/frames/a.jpg",
            "title": "",
            "reader_evidence": {"is_book": False},
        },
    ]
    p["items"] = [
        {
            "id": "i1",
            "category": "lamp",
            "description": "Brass lamp",
            "brand_model": "",
            "frame_ref": "data/frames/a.jpg",
            "dimensions_cm": {"w": None, "h": None, "d": None},
            "confidence": 0.8,
        },
        {"id": "wall", "category": "wall", "frame_ref": "data/frames/a.jpg"},
    ]
    p["offers"] = [
        {
            "ref_id": "b1",
            "kind": "replacement",
            "country": "United Kingdom",
            "currency": "GBP",
            "amount": 12.5,
            "source": "Retailer",
            "url": "https://example.com/new",
            "retrieved_at": date.today().isoformat(),
            "condition_assumed": "New paperback",
            "match_basis": "Exact edition",
            "verified": True,
        },
        {
            "ref_id": "b1",
            "kind": "used",
            "country": "United Kingdom",
            "currency": "GBP",
            "amount": 4,
            "source": "Marketplace",
            "url": "https://example.com/used",
            "retrieved_at": date.today().isoformat(),
            "condition_assumed": "Good",
            "match_basis": "Same work",
            "verified": True,
        },
        {
            "ref_id": "i1",
            "kind": "item",
            "country": "United Kingdom",
            "currency": "GBP",
            "amount": 20,
            "high": 40,
            "source": "Retailer",
            "url": "https://example.com/lamp",
            "retrieved_at": date.today().isoformat(),
            "condition_assumed": "New",
            "match_basis": "Comparable brass lamp",
            "verified": True,
        },
    ]
    refresh_workflow(p)
    return p


class ContractTests(unittest.TestCase):
    def test_required_keys_present_and_in_contract_order(self):
        claim = build_claim_packet(packet())
        self.assertEqual(
            list(claim)[:6], ["sweep", "room", "books", "items", "totals", "review_queue"]
        )
        self.assertEqual(list(claim["sweep"])[:6], CONTRACT_SWEEP)
        self.assertEqual(list(claim["room"])[:8], CONTRACT_ROOM)
        for book in claim["books"]:
            self.assertEqual(list(book)[:14], CONTRACT_BOOK)
            self.assertEqual(
                list(book["replacement_cost"])[:5],
                ["amount", "source", "url", "retrieved_at", "converted"],
            )
            self.assertEqual(
                list(book["used_value"])[:5],
                ["amount", "source", "url", "retrieved_at", "condition_assumed"],
            )
            self.assertIn(book["status"], {"identified", "unidentified", "needs_appraisal"})
        for item in claim["items"]:
            self.assertEqual(list(item)[:9], CONTRACT_ITEM)
            self.assertEqual(list(item["dimensions_cm"]), ["w", "h", "d"])
            self.assertEqual(
                list(item["replacement_cost"])[:5], ["low", "high", "source", "url", "retrieved_at"]
            )
            self.assertIn(item["status"], {"priced", "range", "needs_appraisal"})
        self.assertEqual(list(claim["totals"])[:9], CONTRACT_TOTALS)
        for finding in claim["review_queue"]:
            self.assertEqual(list(finding), ["ref_id", "reason"])

    def test_unknowns_are_blank_and_evidence_excluded_lines_are_dropped(self):
        claim = build_claim_packet(packet())
        self.assertEqual([b["id"] for b in claim["books"]], ["b1", "b2"])
        self.assertEqual([i["id"] for i in claim["items"]], ["i1"])
        unidentified = claim["books"][1]
        self.assertEqual(unidentified["title"], "")
        self.assertIsNone(unidentified["spine_height_cm"])
        self.assertIsNone(unidentified["replacement_cost"]["amount"])
        self.assertEqual(unidentified["replacement_cost"]["source"], "")
        self.assertIsNone(claim["room"]["floor_area_m2"])
        self.assertIsNone(claim["items"][0]["dimensions_cm"]["w"])
        self.assertTrue(any(f["ref_id"] == "b2" for f in claim["review_queue"]))

    def test_totals_are_recomputed_from_exported_lines(self):
        claim = build_claim_packet(packet())
        totals = claim["totals"]
        self.assertEqual(totals["book_count"], 2)
        self.assertEqual(totals["books_identified"], 1)
        self.assertEqual(totals["books_unidentified"], 1)
        self.assertEqual(totals["shelf_run_m"], 0.03)
        self.assertEqual(totals["books_replacement_cost"], 12.5)
        self.assertEqual(totals["books_used_value"], 4)
        self.assertEqual(totals["items_replacement_cost_low"], 20)
        self.assertEqual(totals["items_replacement_cost_high"], 40)
        self.assertEqual(totals["excluded_from_totals"], 1)
        self.assertEqual(claim["items"][0]["status"], "range")

    def test_every_price_carries_source_date_and_condition(self):
        claim = build_claim_packet(packet())
        book = claim["books"][0]
        for key in ("source", "url", "retrieved_at"):
            self.assertTrue(book["replacement_cost"][key])
            self.assertTrue(book["used_value"][key])
        self.assertEqual(book["used_value"]["condition_assumed"], "Good")
        self.assertFalse(book["replacement_cost"]["converted"])
        self.assertIn("b1", claim["price_details"])
        self.assertEqual(
            claim["price_details"]["b1"]["replacement"]["selected"]["url"],
            "https://example.com/new",
        )

    def test_report_is_built_from_the_contract_and_links_evidence(self):
        html = report_html(packet())
        for phrase in (
            "Observed title",
            "https://example.com/new",
            "Retailer",
            "12.5 GBP",
            "20 × 3 cm",
            "Brass lamp",
            "20–40 GBP",
            "Review queue",
            "Methodology",
            "../../frames/a.jpg",
        ):
            self.assertIn(phrase, html)
        self.assertNotIn("Guessed", html)

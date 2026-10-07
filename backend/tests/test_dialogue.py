import unittest
from unittest.mock import AsyncMock, patch

from app.logic.claim import empty_packet
from app.logic.conversation import Turn, context, converse


class DialogueTests(unittest.IsolatedAsyncioTestCase):
    def test_context_separates_guesses_from_confirmed_materials(self):
        packet = empty_packet("context")
        packet["books"] = [{"id": "b", "title": "Unverified old title", "status": "identified"}]
        packet["items"] = [
            {
                "id": "i",
                "category": "fan",
                "category_verified": False,
                "crop_verification": {"agreed": False, "status": "conflict", "category": "decor"},
            }
        ]
        state = context(packet, Turn(text="What did you identify?"))
        self.assertEqual(state["identified_book_count"], 0)
        self.assertEqual(state["identified_materials"], [])
        self.assertFalse(state["room_items"][0]["category_verified"])

    async def test_question_uses_history_and_does_not_become_review_finding(self):
        packet = empty_packet("dialogue", "United Kingdom", "GBP")
        packet["transcript"] = [
            {"role": "claimant", "text": "Can you explain unreadable spines?"},
            {"role": "agent", "text": "Small or blurred text can be hard to read."},
        ]
        packet["books"] = [{"id": "b", "title": "", "frame_ref": "frame", "shelf": "Shelf 1"}]

        async def answer(current, turn):
            self.assertEqual(
                current["transcript"][-1]["text"], "Small or blurred text can be hard to read."
            )
            return "Yes. Move a little closer and hold still so I can read that text."

        turn = Turn(text="Would moving closer help?", turn_id="follow-up")
        with patch("app.logic.conversation.generate_reply", side_effect=answer):
            result = await converse(packet, turn)
        self.assertIn("Move a little closer", result["reply"])
        self.assertFalse(any("Claimant statement" in q["reason"] for q in packet["review_queue"]))
        self.assertEqual(packet["books"][0]["title"], "")
        with patch("app.logic.conversation.generate_reply", new=AsyncMock()) as model:
            duplicate = await converse(packet, turn)
            model.assert_not_awaited()
        self.assertEqual(duplicate["reply"], result["reply"])

    async def test_natural_command_is_immediate_without_model(self):
        packet = empty_packet("tools", "United Kingdom", "GBP")
        with patch("app.logic.conversation.generate_reply", new=AsyncMock()) as model:
            result = await converse(packet, Turn(text="Could you move to the next shelf?"))
            model.assert_not_awaited()
        self.assertEqual(result["action"], "next_shelf")

    async def test_unavailable_model_has_honest_fallback(self):
        packet = empty_packet("offline", "United Kingdom", "GBP")
        with patch(
            "app.logic.conversation.generate_reply",
            new=AsyncMock(side_effect=ValueError("unavailable")),
        ):
            result = await converse(packet, Turn(text="Can you explain the process?"))
        self.assertIn("temporarily unavailable", result["reply"])
        self.assertFalse(any("Claimant statement" in q["reason"] for q in packet["review_queue"]))

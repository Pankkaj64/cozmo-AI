"""Small, explicit conversation tools. Ambiguous references ask for a selection."""

import re
import uuid
from pydantic import BaseModel, Field
from .agent import audit, now, refresh_workflow


class Turn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    shelf: str = Field(default="Shelf 1", max_length=100)
    ref_id: str = ""
    turn_id: str = Field(default_factory=lambda: uuid.uuid4().hex, max_length=100)


def command_text(value):
    text = value.strip().lower().rstrip(".!?")
    text = re.sub(r"^(?:can you|could you|would you)\s+", "", text)
    text = re.sub(r"^(?:okay[, ]+|ok[, ]+|let'?s\s+)", "", text)
    text = re.sub(r"^it[’']s\s+", "it is ", text)
    text = re.sub(r"\bactually\s+", "", text)
    aliases = {"move on": "next shelf", "move to another shelf": "next shelf",
               "go to the next shelf": "next shelf", "scan this shelf": "capture now",
               "stop speaking": "stop talking", "take a look at this shelf": "capture now"}
    return aliases.get(text, text)


def is_explicit_command(value):
    text = command_text(value)
    return text in {"those are not mine", "these are not mine", "next shelf", "move to the next shelf", "next section", "capture", "capture now", "capture this shelf", "take a picture", "all shelves captured", "all shelves walls and floor captured", "coverage complete", "stop talking", "quiet", "pause voice", "what is missing", "status", "what next"} or bool(
        re.fullmatch(r"(?:please )?(?:skip|exclude)(?: that| this| the)? shelf(?:,? (?:those|these) are not mine)?", text)
        or re.fullmatch(r"(?:that |this |it )?(?:is |is a |is an )?(?:a |an )?(?:first edition|signed copy|rare edition|antiquarian book|print|original)", text)
        or re.match(r"(?:the )?title is ", text))


def respond(packet, turn: Turn, conversational_reply=None):
    transcript = packet.setdefault("transcript", [])
    prior = next((t for t in transcript if t.get("reply_to") == turn.turn_id), None)
    if prior:
        return {
            "reply": prior["text"],
            "action": prior.get("action", "none"),
            "packet": packet,
        }
    transcript.append(
        {"id": turn.turn_id, "role": "claimant", "text": turn.text, "time": now()}
    )
    text = command_text(turn.text)
    selected = next(
        (
            line
            for line in packet["books"] + packet["items"]
            if line["id"] == turn.ref_id
        ),
        None,
    )
    if selected is None and packet.get("guidance", {}).get("code") == "art" and text in {"it is a print", "it is an original", "a print", "an original", "print", "original"}:
        selected = next((line for line in packet["items"] if line["id"] == packet["guidance"].get("ref_id")), None)
    action = "none"
    if re.fullmatch(
        r"(?:please )?(?:skip|exclude)(?: that| this| the)? shelf(?:,? (?:those|these) are not mine)?",
        text,
    ) or text in {"those are not mine", "these are not mine"}:
        packet.setdefault("excluded_shelves", [])
        if turn.shelf not in packet["excluded_shelves"]:
            packet["excluded_shelves"].append(turn.shelf)
        removed = [
            line
            for line in packet["books"] + packet["items"]
            if line.get("shelf") == turn.shelf
        ]
        packet.setdefault("excluded_inventory", []).extend(removed)
        ids = {line["id"] for line in removed}
        for key in ("books", "items"):
            packet[key] = [
                line for line in packet[key] if line.get("shelf") != turn.shelf
            ]
        packet["review_queue"] = [
            q for q in packet["review_queue"] if q["ref_id"] not in ids
        ]
        reply = f"Excluded {turn.shelf} from the claim. Its capture remains in the evidence history."
        action = "next_shelf"
    elif text in {"next shelf", "move to the next shelf", "next section"}:
        reply, action = (
            "Move to the next shelf. Hold still with the complete spines in view.",
            "next_shelf",
        )
    elif text in {"capture", "capture now", "capture this shelf", "take a picture"}:
        reply, action = "Hold still. I am capturing this shelf.", "capture"
    elif text in {
        "all shelves captured",
        "all shelves walls and floor captured",
        "coverage complete",
    }:
        packet["coverage_confirmed"] = {
            "turn_id": turn.turn_id,
            "time": now(),
            "source": "claimant declaration",
        }
        reply = "Recorded your confirmation of room coverage. Unreadable or uncertain views still need review."
    elif text in {"stop talking", "quiet", "pause voice"}:
        reply, action = "Voice output paused.", "mute"
    elif re.fullmatch(
        r"(?:that |this |it )?(?:is |is a |is an )?(?:a |an )?(?:first edition|signed copy|rare edition|antiquarian book|print|original)",
        text,
    ):
        if selected is None:
            reply = "Select the book or item you mean, then repeat the correction. I will not apply it to an uncertain match."
        else:
            selected["claimant_notes"] = turn.text
            selected.setdefault("fact_sources", []).append(
                {"turn_id": turn.turn_id, "source": "claimant", "text": turn.text}
            )
            if "first edition" in text:
                selected["edition"], selected["appraisal_required"] = (
                    "First edition (claimant stated)",
                    True,
                )
            elif text.endswith("print") and "category" in selected:
                selected["is_print"], selected["appraisal_required"] = True, False
            elif text.endswith("original") and "category" in selected:
                selected["is_print"], selected["appraisal_required"] = False, True
            else:
                selected["appraisal_required"] = True
            reply = "Applied your correction to the selected item and recorded the source. Special editions and originals require appraisal."
    elif re.match(r"(?:the )?title is ", text):
        if selected is None or "title" not in selected:
            reply = (
                "Select a book first so I can attach the title to the right evidence."
            )
        else:
            selected["title"] = re.search(r"(?:the )?title is (.+)", turn.text, flags=re.I).group(1).strip()
            selected["identity_source"] = {"type": "claimant", "turn_id": turn.turn_id}
            selected["id_confidence"] = 0.5
            packet["review_queue"].append(
                {
                    "ref_id": selected["id"],
                    "reason": "Claimant supplied title; independently verify against the saved frame.",
                }
            )
            reply = "Recorded that title as claimant supplied. It still needs an evidence check."
    elif text in {"what is missing", "status", "what next"}:
        reply = packet.get("guidance", {}).get(
            "text", "Capture the first shelf to begin."
        )
    elif conversational_reply is not None:
        reply = conversational_reply
    else:
        packet["review_queue"].append(
            {
                "ref_id": turn.ref_id or turn.turn_id,
                "reason": f"Claimant statement needs review: {turn.text}",
                "turn_id": turn.turn_id,
            }
        )
        reply = "I saved that statement for review. You can say next shelf, skip this shelf, capture now, or select an item to correct its title or edition."
    refresh_workflow(packet)
    transcript.append(
        {
            "id": uuid.uuid4().hex,
            "role": "agent",
            "text": reply,
            "reply_to": turn.turn_id,
            "time": now(),
            "action": action,
        }
    )
    audit(
        packet,
        "conversation.tool",
        action=action,
        turn_id=turn.turn_id,
        ref_id=turn.ref_id,
    )
    return {"reply": reply, "action": action, "packet": packet}

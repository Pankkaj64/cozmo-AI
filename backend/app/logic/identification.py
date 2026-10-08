"""Evidence rules for accepting a title, plus Open Library work/edition lookup."""

from __future__ import annotations

from datetime import UTC, datetime
from difflib import SequenceMatcher

import httpx

from ..config import (
    HTTP_USER_AGENT,
    OPEN_LIBRARY_AUTHORS_URL,
    OPEN_LIBRARY_BASE_URL,
    OPEN_LIBRARY_BOOKS_URL,
    OPEN_LIBRARY_SEARCH_URL,
)
from .utils import normalized


def identify_observation(observation, source_text, count_validation, threshold=0.75):
    title = str(observation.get("title") or "").strip()
    confidence = float(observation.get("confidence") or 0)
    ocr_match = bool(title and title.casefold() in source_text.casefold())
    # A second detector must have drawn this book's box; the frame-wide count is the fallback.
    counted = observation.get("count_verified")
    if counted is None:
        counted = count_validation.get("agrees") is True
    accepted = bool(
        ocr_match
        and confidence >= threshold
        and counted
        and observation.get("identity_verified", True)
    )
    return {
        "status": "identified" if accepted else "unidentified",
        "title": title if accepted else "",
        "id_confidence": confidence,
        "ocr_match": ocr_match,
        "edition": visible_edition(source_text),
        "isbn": visible_isbn(source_text),
    }


def valid_isbn(value):
    """Return the ISBN as digits (X allowed last for ISBN-10) when its checksum is valid."""
    import re

    value = re.sub(r"[^0-9Xx]", "", str(value or "")).upper()
    if len(value) == 13 and value.isdigit() and value.startswith(("978", "979")):
        if sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(value)) % 10 == 0:
            return value
    if len(value) == 10 and value[:9].isdigit() and (value[-1].isdigit() or value[-1] == "X"):
        if sum((10 - i) * (10 if c == "X" else int(c)) for i, c in enumerate(value)) % 11 == 0:
            return value
    return ""


def visible_isbn(text):
    """Accept only a visible ISBN label and a valid ISBN-10/13 checksum."""
    import re

    for match in re.finditer(r"ISBN(?:-1[03])?\s*[: ]\s*([0-9Xx][0-9Xx -]{8,24})", text, re.I):
        value = re.sub(r"[^0-9Xx]", "", match.group(1)).upper()
        if len(value) == 13 and value.isdigit() and value.startswith(("978", "979")):
            if sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(value)) % 10 == 0:
                return value
        if len(value) == 10 and value[:9].isdigit() and (value[-1].isdigit() or value[-1] == "X"):
            if sum((10 - i) * (10 if c == "X" else int(c)) for i, c in enumerate(value)) % 11 == 0:
                return value
    return ""


def ocr_coverage(title, lines):
    """Fraction of the title's letters matched, in order, by the pooled OCR fragments."""
    target = normalized(title)
    pool = normalized(" ".join(line["text"] for line in lines))
    if not target or not pool:
        return 0.0
    blocks = [
        b
        for b in SequenceMatcher(None, target, pool, autojunk=False).get_matching_blocks()
        if b.size >= 3
    ]
    if not blocks or max(b.size for b in blocks) < 4:
        return 0.0  # no four-letter run of the title is on the spine: nothing to anchor on
    return min(1.0, sum(b.size for b in blocks) / len(target))


def misread_word(title, seen_text):
    """A title word absent from the blind check's text but near one of its words (ratio >= 0.8)."""
    seen = seen_text.split()
    if not seen:
        return None
    for word in normalized(title).split():
        if len(word) < 4 or word in seen:
            continue
        near = max(seen, key=lambda w: SequenceMatcher(None, word, w).ratio())
        if SequenceMatcher(None, word, near).ratio() >= 0.8:
            return word, near
    return None


def reading_agreement(book):
    """Conservative agreement gate for automatic live identification.

    Two ways in, both evidence-bound. `ocr_exact`: the proposed title is literally in the OCR
    text at >= 0.9 confidence. `two_models`: the title reader and the blind check, two models
    with no shared hint, transcribed the same title and the OCR fragments anchor at least a
    third of its letters in order (one run of four or more). Neither way can accept letters
    that are not on the spine.
    """
    reading = book.get("reader_evidence", {})
    check = book.get("crop_verification") or {}
    title = str(book.get("title") or "").strip()
    lines = book.get("ocr_lines", [])
    text = " ".join(line["text"] for line in lines)
    supported = [line for line in lines if line["text"].casefold() in title.casefold()]
    confidence = min((line.get("confidence", 0) for line in supported), default=0)
    author = str(reading.get("author") or book.get("author") or "").strip()
    base = bool(
        not book.get("partial")
        and not book.get("fallback")
        and reading.get("is_book")
        and reading.get("title") == title
        and len(title) >= 8
        # The writer's credit line is never the title, even when it is the largest text.
        and not (author and title.casefold() == author.casefold())
    )
    seen_text = normalized(check.get("visible_text") or "")
    misread = misread_word(title, seen_text)
    if misread:
        # OCR read a wrong letter cleanly ("MASTERPIEG"); the blind check read the word
        # differently ("masterpiece"). Two readings that disagree cannot identify a book.
        book["title_rejected"] = (
            f"OCR read '{misread[0]}' but the blind check read '{misread[1]}'; probable misread."
        )
        base = False
    exact = base and title.casefold() in text.casefold() and confidence >= 0.9
    coverage = ocr_coverage(title, lines)
    two_models = bool(
        base
        and check.get("agreed")
        and seen_text
        and normalized(title) in seen_text
        and coverage >= 0.3  # OCR must anchor at least a third of the letters, in order
    )
    book["identity_basis"] = "ocr_exact" if exact else "two_models" if two_models else ""
    book["ocr_coverage"] = round(coverage, 3)
    if exact:
        return True, confidence
    if two_models:
        return True, round(0.75 + 0.25 * coverage, 3)
    return False, confidence


def promote_verified_book(line, count_validation):
    """Apply the existing OCR/title gate when a deferred category check finishes."""
    if line.get("identity_source") or not (line.get("crop_verification") or {}).get("agreed"):
        return
    candidate = dict(line, title=line.get("proposed_title") or line.get("title") or "")
    agreed, confidence = reading_agreement(candidate)
    candidate.update(identity_verified=agreed, confidence=confidence)
    for key in ("identity_basis", "ocr_coverage", "title_rejected"):
        if key in candidate:
            line[key] = candidate[key]
    source_text = " ".join(x["text"] for x in candidate.get("ocr_lines", []))
    identity = identify_observation(candidate, source_text, count_validation)
    if identity["status"] == "identified":
        line.update(identity)
        for field in ("author", "publisher"):
            value = line.get(field) or line.get("proposed_" + field) or ""
            line[field] = value if value and value.casefold() in source_text.casefold() else ""


def visible_edition(text):
    import re

    match = re.search(
        r"\b(?:first|second|third|fourth|fifth|revised|[1-9][0-9]*(?:st|nd|rd|th))\s+edition\b",
        text,
        re.I,
    )
    return match.group(0) if match else ""


KNOWN_AUTHOR_MIN_WORKS = 20


async def known_author(name: str, client: httpx.AsyncClient) -> dict | None:
    """Open Library author record whose name equals `name`, if that person has many works."""
    response = await client.get(
        OPEN_LIBRARY_AUTHORS_URL,
        params={"q": name, "limit": 3},
        headers={"User-Agent": HTTP_USER_AGENT},
    )
    response.raise_for_status()
    for doc in response.json().get("docs", []):
        if (
            normalized(doc.get("name", "")) == normalized(name)
            and int(doc.get("work_count") or 0) >= KNOWN_AUTHOR_MIN_WORKS
        ):
            return doc
    return None


async def catalogue_witness(book: dict, client: httpx.AsyncClient) -> dict:
    """Ask Open Library whether the accepted title is a known work or an OCR misread.

    confirmed: every word of the title appears in a catalogue title (order-free).
    misread: a title word is absent but a catalogue word is within edit similarity 0.8 of it
    ("masterpieg" vs "masterpiece"), which is the signature of a cleanly read wrong letter.
    """
    author = book.get("author") or book.get("proposed_author") or ""
    response = await client.get(
        OPEN_LIBRARY_SEARCH_URL,
        params={
            "q": f"{book['title']} {author}".strip(),
            "limit": 5,
            "fields": "key,title,author_name",
        },
        headers={"User-Agent": HTTP_USER_AGENT},
    )
    response.raise_for_status()
    docs = response.json().get("docs", [])
    words = normalized(book["title"]).split()
    for doc in docs:
        if all(w in normalized(doc.get("title", "")).split() for w in words):
            return {"status": "confirmed", "doc": doc}
    for doc in docs:
        catalogue = normalized(doc.get("title", "")).split()
        for word in words:
            if word in catalogue or len(word) < 4:
                continue
            near = max(catalogue, key=lambda c: SequenceMatcher(None, word, c).ratio(), default="")
            if near and SequenceMatcher(None, word, near).ratio() >= 0.8:
                return {"status": "misread", "word": word, "suggested_word": near, "doc": doc}
    return {"status": "not_found"}


async def resolve_work(book: dict, client: httpx.AsyncClient) -> dict:
    # A "title" that is a well-known author's name is the writer's credit line, not a work.
    author = await known_author(book["title"], client)
    if author:
        return {
            "status": "author_as_title",
            "author": author["name"],
            "work_count": author.get("work_count"),
            "source": "Open Library authors",
            "url": OPEN_LIBRARY_BASE_URL + "authors/" + str(author.get("key", "")),
            "retrieved_at": datetime.now(UTC).isoformat(),
            "edition_resolved": False,
        }
    witness = await catalogue_witness(book, client)
    if witness["status"] == "misread":
        doc = witness["doc"]
        return {
            "status": "catalogue_misread",
            "suggested_title": doc.get("title", ""),
            "authors": doc.get("author_name", []),
            "misread_word": witness["word"],
            "suggested_word": witness["suggested_word"],
            "source": "Open Library",
            "url": OPEN_LIBRARY_BASE_URL + doc.get("key", "").lstrip("/"),
            "retrieved_at": datetime.now(UTC).isoformat(),
            "edition_resolved": False,
        }
    response = await client.get(
        OPEN_LIBRARY_SEARCH_URL,
        params={"title": book["title"], "limit": 5, "fields": "key,title,author_name"},
        headers={"User-Agent": HTTP_USER_AGENT},
    )
    response.raise_for_status()
    candidates = [
        doc
        for doc in response.json().get("docs", [])
        if normalized(doc.get("title", "")) == normalized(book["title"])
    ]
    if book.get("author"):
        wanted = normalized(book["author"])
        candidates = [
            doc
            for doc in candidates
            if wanted in {normalized(a) for a in doc.get("author_name", [])}
        ]
    if len(candidates) == 1:
        doc = candidates[0]
        return {
            "status": "work_match",
            "title": doc["title"],
            "authors": doc.get("author_name", []),
            "source": "Open Library",
            "url": OPEN_LIBRARY_BASE_URL + doc["key"].lstrip("/"),
            "retrieved_at": datetime.now(UTC).isoformat(),
            "edition_resolved": False,
        }
    return {
        "status": "ambiguous" if candidates else "not_found",
        "candidates": candidates,
        "edition_resolved": False,
    }


async def resolve_isbn(isbn: str, client: httpx.AsyncClient) -> dict | None:
    if visible_isbn("ISBN: " + isbn) != isbn:
        raise ValueError("A checksum-valid observed ISBN is required")
    response = await client.get(
        OPEN_LIBRARY_BOOKS_URL,
        params={"bibkeys": "ISBN:" + isbn, "format": "json", "jscmd": "data"},
        headers={"User-Agent": HTTP_USER_AGENT},
    )
    response.raise_for_status()
    record = response.json().get("ISBN:" + isbn)
    if not record:
        return None
    return {
        "title": record.get("title", ""),
        "author": ", ".join(a["name"] for a in record.get("authors", []) if a.get("name")),
        "publisher": ", ".join(p["name"] for p in record.get("publishers", []) if p.get("name")),
        "isbn": isbn,
        "publication_date": record.get("publish_date", ""),
        "source": "Open Library ISBN edition record",
        "url": record.get("url", OPEN_LIBRARY_BASE_URL + "isbn/" + isbn),
        "retrieved_at": datetime.now(UTC).isoformat(),
        "basis": "Checksum-valid ISBN visible in saved crop OCR",
    }

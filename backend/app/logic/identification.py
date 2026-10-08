"""Evidence rules for accepting a title, plus Open Library work/edition lookup."""

from __future__ import annotations

from datetime import UTC, datetime

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


def reading_agreement(book):
    """Conservative agreement gate for automatic live identification.

    Requires exact visible text, strong crop OCR and a separately read image title.
    This is an evidence gate, not a claim of independently measured accuracy.
    """
    reading = book.get("reader_evidence", {})
    title = str(book.get("title") or "").strip()
    lines = book.get("ocr_lines", [])
    text = " ".join(line["text"] for line in lines)
    supported = [line for line in lines if line["text"].casefold() in title.casefold()]
    confidence = min((line.get("confidence", 0) for line in supported), default=0)
    author = str(reading.get("author") or book.get("author") or "").strip()
    accepted = bool(
        not book.get("partial")
        and not book.get("fallback")
        and reading.get("is_book")
        and reading.get("title") == title
        and len(title) >= 8
        and title.casefold() in text.casefold()
        and confidence >= 0.9
        # The writer's credit line is never the title, even when it is the largest text.
        and not (author and title.casefold() == author.casefold())
    )
    return accepted, confidence


def promote_verified_book(line, count_validation):
    """Apply the existing OCR/title gate when a deferred category check finishes."""
    if line.get("identity_source") or not (line.get("crop_verification") or {}).get("agreed"):
        return
    candidate = dict(line, title=line.get("proposed_title") or line.get("title") or "")
    agreed, confidence = reading_agreement(candidate)
    candidate.update(identity_verified=agreed, confidence=confidence)
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

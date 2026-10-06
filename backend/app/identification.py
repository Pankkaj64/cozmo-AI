"""Resolve OCR observations without allowing generated metadata to become fact.

Work catalogue lookup lives in research.resolve_work; it never establishes edition.
This gate is pure, so it can be tested without detectors, OCR or external services.
"""


def identify_observation(observation, source_text, count_validation, threshold=0.75):
    title = str(observation.get("title") or "").strip()
    confidence = float(observation.get("confidence") or 0)
    ocr_match = bool(title and title.casefold() in source_text.casefold())
    accepted = bool(ocr_match and confidence >= threshold
                    and count_validation.get("agrees") is True
                    and observation.get("identity_verified", True))
    return {"status": "identified" if accepted else "unidentified",
            "title": title if accepted else "", "id_confidence": confidence,
            "ocr_match": ocr_match, "edition": visible_edition(source_text), "isbn": visible_isbn(source_text)}


def visible_isbn(text):
    """Accept only a visible ISBN label and a valid ISBN-10/13 checksum."""
    import re
    for match in re.finditer(r'ISBN(?:-1[03])?\s*[: ]\s*([0-9Xx][0-9Xx -]{8,24})', text, re.I):
        value = re.sub(r'[^0-9Xx]', '', match.group(1)).upper()
        if len(value) == 13 and value.isdigit() and value.startswith(('978', '979')):
            if sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(value)) % 10 == 0:
                return value
        if len(value) == 10 and value[:9].isdigit() and (value[-1].isdigit() or value[-1] == 'X'):
            if sum((10-i) * (10 if c == 'X' else int(c)) for i, c in enumerate(value)) % 11 == 0:
                return value
    return ''


def reading_agreement(book):
    """Conservative agreement gate for automatic live identification.

Requires exact visible text, strong crop OCR and a separately read image title.
This is an evidence gate, not a claim of independently measured accuracy.
"""
    reading = book.get('reader_evidence', {})
    title = str(book.get('title') or '').strip()
    lines = book.get('ocr_lines', [])
    text = ' '.join(line['text'] for line in lines)
    supported = [line for line in lines if line['text'].casefold() in title.casefold()]
    confidence = min((line.get('confidence', 0) for line in supported), default=0)
    accepted = bool(not book.get('partial') and not book.get('fallback')
                    and reading.get('is_book') and reading.get('title') == title
                    and len(title) >= 8 and title.casefold() in text.casefold()
                    and confidence >= .9)
    return accepted, confidence


def promote_verified_book(line, count_validation):
    """Apply the existing OCR/title gate when a deferred category check finishes."""
    if line.get('identity_source') or not (line.get('crop_verification') or {}).get('agreed'):
        return
    candidate = dict(line, title=line.get('proposed_title') or line.get('title') or '')
    agreed, confidence = reading_agreement(candidate)
    candidate.update(identity_verified=agreed, confidence=confidence)
    source_text = ' '.join(x['text'] for x in candidate.get('ocr_lines', []))
    identity = identify_observation(candidate, source_text, count_validation)
    if identity['status'] == 'identified':
        line.update(identity)
        for field in ('author','publisher'):
            value = line.get(field) or line.get('proposed_' + field) or ''
            line[field] = value if value and value.casefold() in source_text.casefold() else ''


def visible_edition(text):
    import re
    match = re.search(r'\b(?:first|second|third|fourth|fifth|revised|[1-9][0-9]*(?:st|nd|rd|th))\s+edition\b', text, re.I)
    return match.group(0) if match else ''

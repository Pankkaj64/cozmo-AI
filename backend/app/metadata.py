"""Edition metadata backed by an ISBN actually read in sweep evidence."""
from datetime import datetime, timezone
from .identification import visible_isbn


async def resolve_isbn(isbn, client):
    if visible_isbn('ISBN: ' + isbn) != isbn:
        raise ValueError('A checksum-valid observed ISBN is required')
    response = await client.get('https://openlibrary.org/api/books', params={
        'bibkeys': 'ISBN:' + isbn, 'format': 'json', 'jscmd': 'data'})
    response.raise_for_status()
    record = response.json().get('ISBN:' + isbn)
    if not record:
        return None
    return {'title': record.get('title', ''),
            'author': ', '.join(a['name'] for a in record.get('authors', []) if a.get('name')),
            'publisher': ', '.join(a['name'] for a in record.get('publishers', []) if a.get('name')),
            'isbn': isbn, 'publication_date': record.get('publish_date', ''),
            'source': 'Open Library ISBN edition record',
            'url': record.get('url', 'https://openlibrary.org/isbn/' + isbn),
            'retrieved_at': datetime.now(timezone.utc).isoformat(),
            'basis': 'Checksum-valid ISBN visible in saved crop OCR'}

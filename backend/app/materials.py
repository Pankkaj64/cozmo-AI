"""Concise named contents for user-facing exports; audit state stays internal."""
from .room_detector import IGNORED_CLASSES


def observed_field(line, field):
    return (line.get(field) or line.get('proposed_' + field)
            or (line.get('reader_evidence') or {}).get(field) or '')


def identified_materials(packet):
    materials = []
    for book in packet.get('books', []):
        if (book.get('reader_evidence') or {}).get('is_book') is False:
            continue
        source = book.get('identity_source') or {}
        reviewed = source.get('kind') == 'claimant_review' or bool(source.get('reviewed_at'))
        verified = (book.get('crop_verification') or {}).get('agreed') is True
        if not reviewed and not (verified and book.get('status') != 'unidentified'):
            continue
        name = book.get('title', '')
        if not name:
            continue
        row = {'id': book['id'], 'type': 'book', 'name': name,
               'shelf': book.get('shelf', ''),
               'status': book.get('status', 'identified'),
               'evidence': (book.get('crop_verification') or {}).get('frame_ref') or book.get('frame_ref', '')}
        for field in ('author', 'publisher', 'edition', 'isbn'):
            value = book.get(field)
            if value: row[field] = value
        if book.get('spine_height_cm') is not None and book.get('spine_thickness_cm') is not None:
            row['spine_cm'] = {'height': book['spine_height_cm'], 'thickness': book['spine_thickness_cm']}
        materials.append(row)
    for item in packet.get('items', []):
        source = item.get('identity_source') or {}
        reviewed = source.get('kind') == 'claimant_review' or bool(source.get('reviewed_at'))
        if not item.get('category_verified') or not (reviewed or (item.get('crop_verification') or {}).get('agreed') is True):
            continue
        name = str(item.get('category') or '').strip()
        if not name or name.lower() in IGNORED_CLASSES | {'unknown', 'book'} or item.get('dismissed_by_reader'):
            continue
        row = {'id': item['id'], 'type': 'object', 'name': name,
               'status': 'identified',
               'evidence': (item.get('crop_verification') or {}).get('frame_ref') or item.get('frame_ref', '')}
        for field in ('material', 'brand_model'):
            value = item.get(field)
            if value: row[field] = value
        dims = {k: v for k, v in item.get('dimensions_cm', {}).items() if v is not None}
        if dims: row['dimensions_cm'] = dims
        materials.append(row)
    return {'materials': materials}

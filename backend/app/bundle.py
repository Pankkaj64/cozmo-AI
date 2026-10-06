"""Portable claim delivery: one canonical packet, report and original evidence."""
import hashlib
import io
import json
import zipfile
from pathlib import Path
from .packet import report_html
from .materials import identified_materials


def claim_bundle(packet, root):
    root = Path(root).resolve()
    output = io.BytesIO()
    missing, manifest = [], []
    refs = {f['frame_ref'] for f in packet.get('frames', [])}
    refs.update(line.get('frame_ref', '') for line in packet['books'] + packet['items'])
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('data/packets/claim_packet.json', json.dumps(identified_materials(packet), indent=2))
        archive.writestr('data/packets/report.html', report_html(packet))
        for ref in sorted(refs - {''}):
            path = (root / ref).resolve()
            if not ref.startswith('data/frames/') or not path.is_relative_to(root / 'data' / 'frames'):
                raise ValueError('Evidence must be inside data/frames')
            if not path.is_file():
                missing.append(ref)
                continue
            raw = path.read_bytes()
            archive.writestr(ref, raw)
            manifest.append({'path': ref, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()})
        for suffix, name in [('ground_truth', 'ground_truth.json'), ('results', 'results.json')]:
            path = root / 'data' / 'packets' / (packet['sweep']['id'] + '.' + suffix + '.json')
            if path.is_file() and path.resolve().is_relative_to(root / 'data' / 'packets'):
                archive.writestr(name, path.read_bytes())
        archive.writestr('manifest.json', json.dumps({'sweep_id': packet['sweep']['id'], 'evidence': manifest, 'missing': missing}, indent=2))
    return output.getvalue()

"""Verify saved inventory crops without rerunning localization or changing reviewed facts."""
import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
load_dotenv(ROOT / 'backend/.env')
load_dotenv(ROOT / 'backend/model_choices.env')
from app.crop_verifier import verify_candidates
from app.agent import refresh_workflow
from app.materials import identified_materials
from app.packet import report_html
from app.identification import promote_verified_book


async def run(args):
    import uuid
    sweep_id = str(uuid.UUID(args.sweep_id))
    os.environ['CROP_VERIFIER_MODEL'] = args.model
    packet_path = ROOT / 'data/packets' / (sweep_id + '.json')
    packet = json.loads(packet_path.read_text())
    backup = ROOT / 'data/diagnostics/crop-verification' / (sweep_id + '.before.json')
    backup.parent.mkdir(parents=True, exist_ok=True)
    if not backup.exists(): backup.write_text(json.dumps(packet, indent=2))
    lines = [(line, kind) for kind in ('books', 'items') for line in packet[kind]]
    for index, (line, kind) in enumerate(lines):
        if line.get('identity_source'): continue
        ref = line.get('frame_ref', '')
        path = (ROOT / ref).resolve()
        if not path.is_relative_to(ROOT / 'data/frames') or not path.is_file():
            line['crop_verification'] = {'agreed':False, 'status':'unavailable', 'reason':'Saved evidence missing'}
        else:
            candidate = dict(line, bbox=line.get('object_bbox') or line.get('bbox'))
            await verify_candidates(path.read_bytes(), [candidate] if kind == 'books' else [], [candidate] if kind == 'items' else [], ref)
            line['crop_verification'] = candidate['crop_verification']
        if kind == 'items':
            line['category_verified'] = line['crop_verification']['agreed']
            line['reader_category'] = line['crop_verification'].get('category', '')
        else:
            frame = next((frame for frame in packet.get('frames',[]) if frame['frame_ref'] == ref), {})
            promote_verified_book(line, frame.get('validation',{}))
        print(index + 1, '/', len(lines), line['id'], line['crop_verification']['status'], flush=True)
    refresh_workflow(packet)
    packet_path.write_text(json.dumps(packet, indent=2))
    (packet_path.with_suffix('.html')).write_text(report_html(packet))
    claim_dir = ROOT / 'data/claims' / sweep_id
    claim_dir.mkdir(parents=True, exist_ok=True)
    (claim_dir / 'claim_packet.json').write_text(json.dumps(identified_materials(packet), indent=2))
    (claim_dir / 'report.html').write_text(report_html(packet).replace('href="../frames/', 'href="../../frames/').replace('href="../videos/', 'href="../../videos/'))
    active_path = packet_path.with_suffix('.active.json')
    if active_path.exists():
        active = json.loads(active_path.read_text()); active['packet'] = packet
        active_path.write_text(json.dumps(active))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('sweep_id')
    parser.add_argument('--model', required=True)
    asyncio.run(run(parser.parse_args()))

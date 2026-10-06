"""Replay saved frames into a NEW packet; never overwrites original evidence/results."""
import argparse
import asyncio
import copy
import json
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
from app.agent import merge_observations, refresh_workflow, capture_quality
from app.schemas import empty_packet
from app.packet import report_html
from app.vision import inspect_frame

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('packet')
    parser.add_argument('--indices', default='')
    parser.add_argument('--reuse-observations', action='store_true', help='Re-associate saved model observations, recomputing scene geometry and primary boxes without calling OCR/VLM again')
    args = parser.parse_args()
    original = json.loads(Path(args.packet).read_text())
    identifier = str(uuid.uuid4())
    packet = empty_packet(identifier, original['sweep']['country'], original['sweep']['currency'], 'Saved-evidence reprocessing')
    packet['sweep'].update(source_sweep=original['sweep'].get('source_sweep', original['sweep']['id']), observation_source=original['sweep']['id'], source_captured_at=original['sweep']['captured_at'], replay=True)
    packet['videos'] = copy.deepcopy(original.get('videos', []))
    state = {'packet': packet, 'position': 0}
    selected = {int(i) for i in args.indices.split(',')} if args.indices else set(range(len(original['frames'])))
    start = time.monotonic()
    output = ROOT / 'data' / 'packets' / f'{identifier}.json'
    for n, frame in enumerate(original['frames']):
        if n not in selected:
            continue
        raw = (ROOT / frame['frame_ref']).read_bytes()
        if args.reuse_observations:
            from app.tracking import scene_signature
            from app.local_detector import detect_objects, select_objects, compare_boxes
            result = copy.deepcopy(frame)
            result['scene'] = scene_signature(raw)
            primary, _ = select_objects(detect_objects(raw))
            secondary = [{'bbox':box, 'confidence':1} for box in frame.get('validation', {}).get('boxes', [])]
            result['primary_count'] = len(primary)
            result['candidate_count'] = len(result.get('books', []))
            if frame.get('validation', {}).get('count') is not None:
                result['validation'] = compare_boxes(primary, secondary)
            result['count_status'] = 'agreed' if result['validation'].get('agrees') is True and len(primary) == result['candidate_count'] and not any(b.get('partial') for b in result['books']) else 'needs_review'
            result['observations_reused'] = True
        else:
            result = await inspect_frame(raw, frame['frame_ref'])
        result['quality'] = capture_quality(raw)
        result['captured_at'] = frame.get('captured_at')
        merge_observations(state, result, frame['shelf'], frame['frame_ref'])
        refresh_workflow(packet)
        packet['sweep']['reprocessing_seconds'] = round(time.monotonic()-start, 2)
        packet['sweep']['status'] = 'needs_review'
        output.write_text(json.dumps(packet, ensure_ascii=False, indent=2))
        output.with_suffix('.html').write_text(report_html(packet))
        print(json.dumps({'frame':n, 'seconds': result['elapsed_s'], 'books':[b.get('title') for b in result['books']], 'items':[i['category'] for i in result['items']], 'retained_books':len(packet['books']), 'retained_items':len(packet['items']), 'packet': str(output)}), flush=True)
    print('REPLAY_COMPLETE', output, flush=True)

asyncio.run(main())

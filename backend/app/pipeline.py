"""Named, evidence-producing boundaries for the assignment pipeline.

Stages measure actual runtime; no model generates metrics or final totals.
"""
import time
from contextlib import asynccontextmanager

STAGES = (
    'camera', 'frame_extraction', 'vision_book_detection', 'ocr_spine_reading',
    'crop_verification', 'identification', 'measurement', 'pricing', 'validation', 'claim_packet',
)


@asynccontextmanager
async def pipeline_stage(result, name):
    if name not in STAGES:
        raise ValueError(f'Unknown pipeline stage: {name}')
    started = time.perf_counter()
    record = {'name': name, 'status': 'running'}
    result.setdefault('pipeline_stages', []).append(record)
    try:
        yield record
        record['status'] = 'complete'
    except Exception as exc:
        record.update(status='failed', error_type=type(exc).__name__)
        raise
    finally:
        record['elapsed_s'] = round(time.perf_counter() - started, 6)

"""Reprocess saved evidence without overwriting an existing claim packet."""
import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('frame')
parser.add_argument('output')
parser.add_argument('--model')
args = parser.parse_args()
if args.model:
    os.environ['OLLAMA_VISION_MODEL'] = args.model
    os.environ['VISION_PROVIDER'] = 'ollama'
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.vision import inspect_frame

async def main():
    frame = Path(args.frame)
    result = await inspect_frame(frame.read_bytes(), str(frame))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2))
    print(json.dumps({k: v for k, v in result.items() if k != 'model_response'}, indent=2))

asyncio.run(main())

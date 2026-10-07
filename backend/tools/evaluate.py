"""CLI: score a saved packet against a ground-truth JSON file (format described in the README)."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.logic.evaluation import evaluate  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("packet", help="data/packets/<sweep-id>.json (internal packet)")
    parser.add_argument("ground_truth", help="ground truth JSON")
    parser.add_argument("--output", help="write results JSON here")
    args = parser.parse_args()
    result = evaluate(
        json.loads(Path(args.packet).read_text()),
        json.loads(Path(args.ground_truth).read_text()),
    )
    text = json.dumps(result, indent=2)
    print(text)
    if args.output:
        Path(args.output).write_text(text)


if __name__ == "__main__":
    main()

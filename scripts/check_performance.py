#!/usr/bin/env python3
"""Compare reliability results with the tracked fixed-runner baseline."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("result", type=Path)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("--max-regression", type=float, default=20.0)
    args = parser.parse_args()
    result = json.loads(args.result.read_text(encoding="utf-8"))
    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    actual = float(result["stress_runs_per_second"])
    expected = float(baseline["stress_runs_per_second"])
    minimum = expected * (1 - args.max_regression / 100)
    print(
        f"stress throughput: {actual:.2f} runs/s; baseline {expected:.2f}; "
        f"minimum {minimum:.2f} ({args.max_regression:.1f}% regression limit)"
    )
    if actual < minimum:
        print("performance regression gate failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

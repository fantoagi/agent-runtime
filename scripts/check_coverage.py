#!/usr/bin/env python3
"""Enforce coverage thresholds for the Agent Runtime reliability core."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

EXCLUDED_ADAPTERS = {
    "src/agent_runtime/__main__.py",
    "src/agent_runtime/cli.py",
    "src/agent_runtime/sdk.py",
}
EXCLUDED_PREFIXES = ("src/agent_runtime/api/", "src/agent_runtime/interactive/")


def normalized(path: str) -> str:
    return path.replace("\\", "/")


def is_core(path: str) -> bool:
    value = normalized(path)
    return (
        value.startswith("src/agent_runtime/")
        and value not in EXCLUDED_ADAPTERS
        and not value.startswith(EXCLUDED_PREFIXES)
    )


def percentage(covered: int, total: int) -> float:
    return 100.0 if total == 0 else covered * 100.0 / total


def aggregate(files: dict[str, Any]) -> tuple[int, int, int, int, list[str]]:
    covered_lines = statements = covered_branches = branches = 0
    included: list[str] = []
    for path, detail in files.items():
        if not is_core(path):
            continue
        summary = detail["summary"]
        covered_lines += int(summary["covered_lines"])
        statements += int(summary["num_statements"])
        covered_branches += int(summary["covered_branches"])
        branches += int(summary["num_branches"])
        included.append(normalized(path))
    return covered_lines, statements, covered_branches, branches, sorted(included)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path, nargs="?", default=Path("coverage.json"))
    parser.add_argument("--line", type=float, default=90.0, dest="line_threshold")
    parser.add_argument("--branch", type=float, default=80.0, dest="branch_threshold")
    args = parser.parse_args()

    if not args.report.is_file():
        print(f"coverage report not found: {args.report}", file=sys.stderr)
        return 2
    data = json.loads(args.report.read_text(encoding="utf-8"))
    files = data.get("files")
    if not isinstance(files, dict):
        print("coverage report has no files mapping", file=sys.stderr)
        return 2

    covered_lines, statements, covered_branches, branches, included = aggregate(files)
    if not included:
        print("coverage report contains no Agent Runtime core modules", file=sys.stderr)
        return 2

    line_rate = percentage(covered_lines, statements)
    branch_rate = percentage(covered_branches, branches)
    print(
        f"core line coverage: {line_rate:.2f}% "
        f"({covered_lines}/{statements}); required >= {args.line_threshold:.2f}%"
    )
    print(
        f"core branch coverage: {branch_rate:.2f}% "
        f"({covered_branches}/{branches}); required >= {args.branch_threshold:.2f}%"
    )
    print(f"core modules checked: {len(included)}")

    failed = False
    if line_rate + 1e-9 < args.line_threshold:
        print("line coverage gate failed", file=sys.stderr)
        failed = True
    if branch_rate + 1e-9 < args.branch_threshold:
        print("branch coverage gate failed", file=sys.stderr)
        failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from .sdk import create_local_runtime, demo_agent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-runtime", description="Durable single-agent runtime CLI")
    parser.add_argument("--workspace", default=".", help="Workspace available to built-in tools (default: current directory)")
    parser.add_argument("--state-dir", default=None, help="Runtime state directory (default: <workspace>/.agent-runtime)")
    subcommands = parser.add_subparsers(dest="command", required=True)

    demo = subcommands.add_parser("demo", help="Run the deterministic arithmetic demo")
    demo.add_argument("input", help="Arithmetic expression, e.g. '19 * 23'")

    runs = subcommands.add_parser("runs", help="Inspect or control existing runs")
    runs_subcommands = runs.add_subparsers(dest="runs_command", required=True)
    runs_subcommands.add_parser("list", help="List recent runs")
    get = runs_subcommands.add_parser("get", help="Show a run")
    get.add_argument("run_id")
    events = runs_subcommands.add_parser("events", help="Show stored events")
    events.add_argument("run_id")
    pause = runs_subcommands.add_parser("pause", help="Request a pause")
    pause.add_argument("run_id")
    resume = runs_subcommands.add_parser("resume", help="Resume a paused or approved run")
    resume.add_argument("run_id")
    cancel = runs_subcommands.add_parser("cancel", help="Cancel a run")
    cancel.add_argument("run_id")

    approve = subcommands.add_parser("approve", help="Resolve a pending tool approval")
    approve.add_argument("approval_id")
    approve.add_argument("--reject", action="store_true", help="Reject instead of approving")
    approve.add_argument("--reason", default=None)
    return parser


def _print(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


async def async_main(arguments: argparse.Namespace) -> int:
    runtime = create_local_runtime(Path(arguments.workspace), arguments.state_dir)
    runtime.register_agent(demo_agent())

    if arguments.command == "demo":
        run = await runtime.run("demo", arguments.input)
        _print(run.to_dict())
        print("\nEvents:")
        async for event in runtime.stream(run.id):
            _print(event.to_dict())
        return 0 if run.status == "completed" else 1

    if arguments.command == "approve":
        approval = runtime.resolve_approval(arguments.approval_id, not arguments.reject, arguments.reason)
        _print({"id": approval.id, "run_id": approval.run_id, "status": approval.status})
        return 0

    if arguments.runs_command == "list":
        _print([run.to_dict() for run in runtime.store.list_runs()])
        return 0
    if arguments.runs_command == "get":
        _print(runtime.store.get_run(arguments.run_id).to_dict())
        return 0
    if arguments.runs_command == "events":
        _print([event.to_dict() for event in runtime.store.events_since(arguments.run_id)])
        return 0
    if arguments.runs_command == "pause":
        _print(runtime.pause(arguments.run_id).to_dict())
        return 0
    if arguments.runs_command == "cancel":
        _print(runtime.cancel(arguments.run_id).to_dict())
        return 0
    if arguments.runs_command == "resume":
        run = await runtime.resume(arguments.run_id)
        _print(run.to_dict())
        return 0 if run.status == "completed" else 1
    raise AssertionError("unreachable")


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        raise SystemExit(asyncio.run(async_main(args)))
    except KeyboardInterrupt:
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()

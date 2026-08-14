from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from .evals import EvalCase, EvalRunner, EvalSuite
from .observability import ObservabilityService
from .sdk import create_local_runtime, demo_agent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-runtime", description="Durable single-agent runtime CLI")
    parser.add_argument("--workspace", default=".", help="Workspace available to built-in tools (default: current directory)")
    parser.add_argument("--state-dir", default=None, help="Runtime state directory (default: <workspace>/.agent-runtime)")
    subcommands = parser.add_subparsers(dest="command", required=True)

    lab = subcommands.add_parser("lab", help="Launch the visual Agent Runtime Learning Console")
    lab.add_argument("--host", default="127.0.0.1")
    lab.add_argument("--port", type=int, default=8000)
    lab.add_argument("--no-browser", action="store_true", help="Do not open the browser automatically")

    demo = subcommands.add_parser("demo", help="Run the deterministic arithmetic demo")
    demo.add_argument("input", help="Arithmetic expression, e.g. '19 * 23'")

    observe = subcommands.add_parser("observe", help="Inspect derived metrics and run traces")
    observe_subcommands = observe.add_subparsers(dest="observe_command", required=True)
    metrics = observe_subcommands.add_parser("metrics", help="Show a JSON metrics snapshot")
    metrics.add_argument("--limit", type=int, default=1000)
    trace = observe_subcommands.add_parser("trace", help="Show the trace for one run")
    trace.add_argument("run_id")

    eval_command = subcommands.add_parser("eval", help="Run deterministic evaluation suites")
    eval_subcommands = eval_command.add_subparsers(dest="eval_command", required=True)
    eval_subcommands.add_parser("demo", help="Evaluate the built-in arithmetic agent")

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

    unknown = subcommands.add_parser(
        "resolve-unknown", help="Resolve a tool execution whose side effect is uncertain"
    )
    unknown.add_argument("execution_id")
    unknown.add_argument("outcome", choices=("completed", "retry", "failed"))
    unknown.add_argument("--result", default=None, help="Confirmed result for completed outcome")
    unknown.add_argument("--error", default=None, help="Failure reason for failed outcome")
    return parser


def _print(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


async def async_main(arguments: argparse.Namespace) -> int:
    if arguments.command == "lab":
        try:
            import uvicorn
        except ImportError as error:
            raise RuntimeError(
                "Learning Console requires API dependencies. Run `pip install -e .[api]`.",
            ) from error
        from .api.app import create_demo_app

        app = create_demo_app(
            arguments.workspace,
            arguments.state_dir,
            enable_learning_console=True,
        )
        url = f"http://{arguments.host}:{arguments.port}/lab"
        if not arguments.no_browser:
            async def open_browser() -> None:
                import webbrowser

                await asyncio.sleep(0.8)
                await asyncio.to_thread(webbrowser.open, url)

            asyncio.create_task(open_browser())
        print(f"Learning Console: {url}")
        server = uvicorn.Server(
            uvicorn.Config(app, host=arguments.host, port=arguments.port, log_level="info")
        )
        await server.serve()
        return 0

    runtime = create_local_runtime(Path(arguments.workspace), arguments.state_dir)
    runtime.register_agent(demo_agent())

    if arguments.command == "demo":
        run = await runtime.run("demo", arguments.input)
        _print(run.to_dict())
        print("\nEvents:")
        async for event in runtime.stream(run.id):
            _print(event.to_dict())
        return 0 if run.status == "completed" else 1

    if arguments.command == "observe":
        observability = ObservabilityService(runtime.store)
        if arguments.observe_command == "metrics":
            _print(observability.metrics(limit=arguments.limit).to_dict())
            return 0
        if arguments.observe_command == "trace":
            _print(observability.trace(arguments.run_id).to_dict())
            return 0

    if arguments.command == "eval":
        suite = EvalSuite(
            name="arithmetic-demo",
            cases=[
                EvalCase(name="multiply", input="19 * 23", expected_output="The result is 437."),
                EvalCase(name="addition", input="2 + 2", expected_output="The result is 4."),
            ],
        )
        report = await EvalRunner(runtime).run(suite, "demo")
        _print(report.to_dict())
        return 0 if report.failed_cases == 0 else 1

    if arguments.command == "approve":
        approval = runtime.resolve_approval(arguments.approval_id, not arguments.reject, arguments.reason)
        _print({"id": approval.id, "run_id": approval.run_id, "status": approval.status})
        return 0

    if arguments.command == "resolve-unknown":
        execution = runtime.resolve_unknown_tool(
            arguments.execution_id,
            arguments.outcome,
            result_content=arguments.result,
            error=arguments.error,
        )
        _print(
            {
                "id": execution.id,
                "run_id": execution.run_id,
                "status": execution.status,
                "result": execution.result_content,
                "error": execution.error,
            }
        )
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

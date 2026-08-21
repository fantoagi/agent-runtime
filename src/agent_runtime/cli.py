from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .acceptance import (
    AcceptanceSuiteError,
    RealModelAcceptanceRunner,
    load_acceptance_suite,
)
from .acceptance_compare import compare_acceptance_reports
from .backup import RuntimeBackupManager
from .doctor import RuntimeDoctor
from .domain import BackupError
from .evals import EvalCase, EvalRunner, EvalSuite
from .incident import IncidentDiagnosticsService
from .local_config import (
    LocalConfigError,
    LocalRuntimeSettings,
    load_local_settings,
    resolve_local_config_path,
    write_default_local_config,
)
from .local_runtime import (
    LocalRuntimeLock,
    LocalRuntimeLockError,
    create_configured_local_runtime,
    local_runtime_status,
)
from .observability import ObservabilityService
from .sdk import (
    create_local_runtime,
    create_memory_demo_runtime,
    create_multi_agent_demo_runtime,
    demo_agent,
    multi_agent_demo_workflow,
)
from .telemetry import configure_structured_logging


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-runtime",
        description="Durable single-agent and multi-agent runtime CLI",
    )
    parser.add_argument(
        "--workspace",
        default=None,
        help="Workspace available to built-in tools (default: current directory)",
    )
    parser.add_argument(
        "--state-dir",
        default=None,
        help="Runtime state directory (default: <workspace>/.agent-runtime)",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Local Runtime TOML configuration (default: <workspace>/agent-runtime.toml)",
    )
    parser.add_argument(
        "--json-logs",
        action="store_true",
        help="Emit bounded structured Runtime logs as JSON lines on stderr",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    init = subcommands.add_parser("init", help="Create a local single-user Runtime configuration")
    init.add_argument("--force", action="store_true", help="Replace an existing configuration")

    serve = subcommands.add_parser("serve", help="Run the configured local Runtime API")
    serve.add_argument("--host", default=None, help="Loopback host override")
    serve.add_argument("--port", type=int, default=None, help="HTTP port override")

    chat = subcommands.add_parser("chat", help="Open the interactive terminal Agent shell")
    chat.add_argument("prompt", nargs="?", help="Optional initial prompt")
    chat.add_argument(
        "-p",
        "--print",
        dest="print_only",
        action="store_true",
        help="Print one response and exit",
    )
    chat_sessions = chat.add_mutually_exclusive_group()
    chat_sessions.add_argument(
        "-c",
        "--continue",
        dest="continue_session",
        action="store_true",
        help="Continue the most recently used interactive session",
    )
    chat_sessions.add_argument(
        "-r",
        "--resume",
        dest="resume_session_id",
        help="Resume a persisted session by id",
    )
    chat_display = chat.add_mutually_exclusive_group()
    chat_display.add_argument(
        "--compact",
        dest="display_mode",
        action="store_const",
        const="compact",
        help="Show concise Tool calls and results (default)",
    )
    chat_display.add_argument(
        "--verbose",
        dest="display_mode",
        action="store_const",
        const="verbose",
        help="Show structured Tool arguments and bounded multi-line results",
    )
    chat.set_defaults(display_mode="compact")
    chat.add_argument("--no-color", action="store_true", help="Disable ANSI color output")

    subcommands.add_parser("status", help="Show local Runtime ownership and state health")

    lab = subcommands.add_parser("lab", help="Launch the visual Agent Runtime Learning Console")
    lab.add_argument("--host", default="127.0.0.1")
    lab.add_argument("--port", type=int, default=8000)
    lab.add_argument(
        "--no-browser", action="store_true", help="Do not open the browser automatically"
    )

    demo = subcommands.add_parser("demo", help="Run the deterministic arithmetic demo")
    demo.add_argument("input", help="Arithmetic expression, e.g. '19 * 23'")

    workflow = subcommands.add_parser("workflow", help="Run deterministic multi-agent workflows")
    workflow_subcommands = workflow.add_subparsers(dest="workflow_command", required=True)
    workflow_demo = workflow_subcommands.add_parser(
        "demo", help="Run Planner -> Worker -> Reviewer"
    )
    workflow_demo.add_argument("input", help="Task delegated through the three agents")

    memory = subcommands.add_parser("memory", help="Explore Session and scoped memory")
    memory_subcommands = memory.add_subparsers(dest="memory_command", required=True)
    memory_demo = memory_subcommands.add_parser(
        "demo", help="Create a Session, remember a fact, and retrieve it in a Run"
    )
    memory_demo.add_argument("input", help="Question answered with retrieved memory")
    memory_demo.add_argument(
        "--remember",
        default="The user prefers Python for Agent Runtime examples.",
        help="Session memory inserted before the demo Run",
    )

    observe = subcommands.add_parser("observe", help="Inspect derived metrics and run traces")
    observe_subcommands = observe.add_subparsers(dest="observe_command", required=True)
    metrics = observe_subcommands.add_parser("metrics", help="Show a JSON metrics snapshot")
    metrics.add_argument("--limit", type=int, default=1000)
    diagnostics = observe_subcommands.add_parser(
        "diagnostics", help="Show runtime, process, SQLite, metrics, and recent failures"
    )
    diagnostics.add_argument("--limit", type=int, default=1000)
    diagnostics.add_argument("--recent-failures", type=int, default=20)
    observe_subcommands.add_parser(
        "sandbox", help="Show tool capability policy and managed sandbox state"
    )
    incident_bundle = observe_subcommands.add_parser(
        "incident-bundle",
        help="Create a redacted support-safe diagnostic ZIP",
    )
    incident_bundle.add_argument("--output", default=None)
    incident_bundle.add_argument("--run-id", default=None)
    incident_bundle.add_argument("--limit", type=int, default=100)
    incident_bundle.add_argument("--recent-failures", type=int, default=20)
    incident_bundle.add_argument("--event-limit", type=int, default=5000)
    incident_bundle.add_argument("--overwrite", action="store_true")
    trace = observe_subcommands.add_parser("trace", help="Show the trace for one run")
    trace.add_argument("run_id")
    trace_tree = observe_subcommands.add_parser(
        "trace-tree", help="Show the Parent/Child trace tree for one run"
    )
    trace_tree.add_argument("run_id")

    eval_command = subcommands.add_parser(
        "eval", help="Run deterministic demos or isolated real-model acceptance suites"
    )
    eval_subcommands = eval_command.add_subparsers(dest="eval_command", required=True)
    eval_subcommands.add_parser("demo", help="Evaluate the built-in arithmetic agent")
    eval_run = eval_subcommands.add_parser(
        "run", help="Run an isolated acceptance suite with the configured model"
    )
    eval_run.add_argument("--suite", default="local-real-model")
    eval_run.add_argument(
        "--case",
        dest="case_names",
        action="append",
        default=[],
        help="Run one named case; repeat the option to select multiple cases",
    )
    eval_run.add_argument("--repeat", type=int, default=1)
    eval_run.add_argument("--output", default=None)
    eval_compare = eval_subcommands.add_parser(
        "compare", help="Compare two persisted acceptance reports without calling a model"
    )
    eval_compare.add_argument("baseline", help="Earlier acceptance-report.json")
    eval_compare.add_argument("candidate", help="New acceptance-report.json")
    eval_compare.add_argument(
        "--case",
        dest="case_names",
        action="append",
        default=[],
        help="Explicitly compare one or more Cases; repeat the option for partial comparison",
    )

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
    unknown.add_argument(
        "outcome",
        choices=("confirmed_succeeded", "confirmed_failed", "completed", "failed", "retry"),
    )
    unknown.add_argument("--result", default=None, help="Confirmed result for succeeded outcome")
    unknown.add_argument("--error", default=None, help="Failure detail for failed outcome")
    unknown.add_argument("--reason", required=True, help="Required human audit reason")
    unknown.add_argument("--resolved-by", default="local-user", help="Audit actor identity")

    doctor = subcommands.add_parser("doctor", help="Run read-only Runtime diagnostics")
    doctor.add_argument("--run-id", default=None, help="Limit diagnostics to one Run")
    doctor.add_argument("--json", action="store_true", help="Print the complete JSON report")

    backup = subcommands.add_parser(
        "backup", help="Create, verify, or restore a durable Runtime state archive"
    )
    backup_subcommands = backup.add_subparsers(dest="backup_command", required=True)
    backup_create = backup_subcommands.add_parser(
        "create", help="Create an online SQLite and Artifact backup"
    )
    backup_create.add_argument("--output", default=None, help="Destination .agent-backup file")
    backup_create.add_argument("--overwrite", action="store_true")
    backup_verify = backup_subcommands.add_parser(
        "verify", help="Verify archive checksums and SQLite consistency"
    )
    backup_verify.add_argument("archive")
    backup_restore = backup_subcommands.add_parser(
        "restore", help="Restore state after every Runtime using it has stopped"
    )
    backup_restore.add_argument("archive")
    backup_restore.add_argument(
        "--force", action="store_true", help="Replace existing state after offline checks"
    )
    backup_restore.add_argument(
        "--discard-previous",
        action="store_true",
        help="Delete the automatic pre-restore rollback copy after success",
    )
    return parser


def _print(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _runtime_state_paths(arguments: argparse.Namespace) -> tuple[Path, Path, Path]:
    workspace = Path(arguments.workspace or ".").resolve()
    state_dir = Path(arguments.state_dir or workspace / ".agent-runtime").resolve()
    return state_dir, state_dir / "runtime.sqlite3", state_dir / "artifacts"


def _local_settings(arguments: argparse.Namespace) -> LocalRuntimeSettings:
    config_path = resolve_local_config_path(arguments.config, arguments.workspace)
    return load_local_settings(
        config_path,
        workspace_override=arguments.workspace,
        state_dir_override=arguments.state_dir,
        host_override=getattr(arguments, "host", None),
        port_override=getattr(arguments, "port", None),
    )


async def _serve_local(arguments: argparse.Namespace) -> int:
    try:
        import uvicorn
    except ImportError as error:
        raise RuntimeError(
            "Local Runtime server requires API dependencies. Run `pip install -e .[api]`.",
        ) from error
    from .api.app import create_app

    settings = _local_settings(arguments)
    configure_structured_logging(
        level=settings.log_level,
        file_path=settings.log_file,
        max_bytes=settings.log_max_bytes,
        backup_count=settings.log_backup_count,
    )
    lock = LocalRuntimeLock(settings.lock_path)
    lock.acquire()
    runtime = None
    try:
        runtime = create_configured_local_runtime(settings)
        app = create_app(
            runtime,
            default_agent=settings.agent_name,
            shutdown_runtime=True,
        )
        print(f"Local Runtime: http://{settings.host}:{settings.port}")
        print(f"Configuration: {settings.config_path}")
        print(f"State: {settings.state_dir}")
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                host=settings.host,
                port=settings.port,
                log_level=settings.log_level.lower(),
            )
        )
        await server.serve()
        return 0
    finally:
        if runtime is not None:
            await runtime.shutdown(timeout_seconds=settings.shutdown_timeout_seconds)
        lock.release()


async def _chat_local(arguments: argparse.Namespace) -> int:
    from rich.console import Console

    from .interactive import ChatOptions, DisplayMode, InteractiveShell

    settings = _local_settings(arguments)
    configure_structured_logging(
        level=settings.log_level,
        file_path=settings.log_file,
        max_bytes=settings.log_max_bytes,
        backup_count=settings.log_backup_count,
        include_stream=False,
    )
    lock = LocalRuntimeLock(settings.lock_path)
    lock.acquire()
    runtime = None
    try:
        runtime = create_configured_local_runtime(settings)
        shell = InteractiveShell(
            runtime,
            settings,
            options=ChatOptions(
                initial_prompt=arguments.prompt,
                print_only=arguments.print_only,
                continue_session=arguments.continue_session,
                resume_session_id=arguments.resume_session_id,
                display_mode=DisplayMode(arguments.display_mode),
            ),
            console=Console(
                no_color=arguments.no_color,
                force_terminal=False if arguments.no_color else None,
            ),
        )
        return await shell.run()
    finally:
        if runtime is not None:
            await runtime.shutdown(timeout_seconds=settings.shutdown_timeout_seconds)
        lock.release()


async def _eval_local(arguments: argparse.Namespace) -> int:
    settings = _local_settings(arguments)
    configure_structured_logging(
        level=settings.log_level,
        file_path=settings.log_file,
        max_bytes=settings.log_max_bytes,
        backup_count=settings.log_backup_count,
        include_stream=False,
    )
    lock = LocalRuntimeLock(settings.lock_path)
    lock.acquire()
    try:
        suite = load_acceptance_suite(arguments.suite)
        output = Path(arguments.output).resolve() if arguments.output else None
        report = await RealModelAcceptanceRunner(settings).run(
            suite,
            case_names=arguments.case_names,
            repeat=arguments.repeat,
            output_path=output,
        )
        _print(report.to_dict())
        return 0 if report.failed_attempts == 0 else 1
    finally:
        lock.release()


async def async_main(arguments: argparse.Namespace) -> int:
    if arguments.json_logs:
        configure_structured_logging()

    if arguments.command == "init":
        try:
            workspace = Path(arguments.workspace or ".").resolve()
            config_path = resolve_local_config_path(arguments.config, workspace)
            created = write_default_local_config(
                config_path,
                workspace=workspace,
                state_dir=arguments.state_dir,
                force=arguments.force,
            )
            settings = load_local_settings(created)
            settings.state_dir.mkdir(parents=True, exist_ok=True)
            _print(
                {
                    "status": "initialized",
                    "configuration": str(created),
                    "state_dir": str(settings.state_dir),
                    "next": f"agent-runtime --config {created} serve",
                }
            )
            return 0
        except LocalConfigError as error:
            _print({"status": "error", "code": type(error).__name__, "detail": str(error)})
            return 2

    if arguments.command == "status":
        try:
            _print(local_runtime_status(_local_settings(arguments)))
            return 0
        except LocalConfigError as error:
            _print({"status": "error", "code": type(error).__name__, "detail": str(error)})
            return 2

    if arguments.command == "serve":
        try:
            return await _serve_local(arguments)
        except (LocalConfigError, LocalRuntimeLockError) as error:
            _print({"status": "error", "code": type(error).__name__, "detail": str(error)})
            return 2

    if arguments.command == "chat":
        try:
            return await _chat_local(arguments)
        except (LocalConfigError, LocalRuntimeLockError, KeyError) as error:
            _print({"status": "error", "code": type(error).__name__, "detail": str(error)})
            return 2

    if arguments.command == "eval" and arguments.eval_command == "run":
        try:
            return await _eval_local(arguments)
        except (AcceptanceSuiteError, LocalConfigError, LocalRuntimeLockError) as error:
            _print({"status": "error", "code": type(error).__name__, "detail": str(error)})
            return 2

    if arguments.command == "eval" and arguments.eval_command == "compare":
        try:
            comparison = compare_acceptance_reports(
                arguments.baseline,
                arguments.candidate,
                case_names=arguments.case_names,
            )
            _print(comparison.to_dict())
            return 0 if comparison.passed else (2 if comparison.status == "incompatible" else 1)
        except AcceptanceSuiteError as error:
            _print({"status": "error", "code": type(error).__name__, "detail": str(error)})
            return 2

    if arguments.command == "backup":
        state_dir, database_path, artifact_path = _runtime_state_paths(arguments)
        manager = RuntimeBackupManager(database_path, artifact_path)
        try:
            if arguments.backup_command == "create":
                output = (
                    Path(arguments.output).resolve()
                    if arguments.output
                    else state_dir
                    / "backups"
                    / f"runtime-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.agent-backup"
                )
                _print(manager.create(output, overwrite=arguments.overwrite).to_dict())
                return 0
            if arguments.backup_command == "verify":
                verification = RuntimeBackupManager.verify(arguments.archive)
                _print(verification.to_dict())
                return verification.exit_code
            if arguments.backup_command == "restore":
                restored = manager.restore(
                    arguments.archive,
                    overwrite=arguments.force,
                    keep_previous=not arguments.discard_previous,
                )
                _print(restored.to_dict())
                return 0
        except BackupError as error:
            _print(
                {
                    "status": "error",
                    "code": type(error).__name__,
                    "detail": str(error),
                }
            )
            return 2
        raise AssertionError("unreachable")

    if arguments.command == "lab":
        try:
            import uvicorn
        except ImportError as error:
            raise RuntimeError(
                "Learning Console requires API dependencies. Run `pip install -e .[api]`.",
            ) from error
        from .api.app import create_demo_app

        app = create_demo_app(
            arguments.workspace or ".",
            arguments.state_dir,
            enable_learning_console=True,
        )
        url = f"http://{arguments.host}:{arguments.port}/lab"
        if not arguments.no_browser:

            async def open_browser() -> None:
                import webbrowser

                await asyncio.sleep(0.8)
                await asyncio.to_thread(webbrowser.open, url)

            browser_task = asyncio.create_task(open_browser())
            del browser_task
        print(f"Learning Console: {url}")
        server = uvicorn.Server(
            uvicorn.Config(app, host=arguments.host, port=arguments.port, log_level="info")
        )
        await server.serve()
        return 0

    if arguments.command == "memory":
        runtime = create_memory_demo_runtime(Path(arguments.workspace or "."), arguments.state_dir)
        session = runtime.create_session({"demo": "memory"})
        memory = runtime.remember(
            arguments.remember,
            scope="session",
            scope_id=session.id,
        )
        run = await runtime.run(
            "memory-demo",
            arguments.input,
            session_id=session.id,
        )
        _print(
            {
                "session": session.to_dict(),
                "memory": memory.to_dict(),
                "run": run.to_dict(),
                "session_runs": [item.to_dict() for item in runtime.session_runs(session.id)],
                "memory_events": [
                    event.to_dict()
                    for event in runtime.store.events_since(run.id)
                    if event.type.startswith("memory.") or event.type.startswith("context.")
                ],
            }
        )
        return 0 if run.status == "completed" else 1

    if arguments.command == "workflow":
        runtime = create_multi_agent_demo_runtime(
            Path(arguments.workspace or "."), arguments.state_dir
        )
        execution = await multi_agent_demo_workflow().run(runtime, arguments.input)
        _print(
            {
                "execution": execution.to_dict(),
                "trace_tree": ObservabilityService(runtime.store)
                .trace_tree(execution.parent.id)
                .to_dict(),
            }
        )
        return 0 if execution.parent.status == "completed" else 1

    runtime = create_local_runtime(Path(arguments.workspace or "."), arguments.state_dir)
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
        if arguments.observe_command == "diagnostics":
            _print(
                observability.diagnostics(
                    runtime,
                    metrics_limit=arguments.limit,
                    recent_failure_limit=arguments.recent_failures,
                ).to_dict()
            )
            return 0
        if arguments.observe_command == "sandbox":
            _print(runtime.sandbox_snapshot())
            return 0
        if arguments.observe_command == "incident-bundle":
            incidents = IncidentDiagnosticsService(runtime)
            output = (
                Path(arguments.output).resolve()
                if arguments.output
                else Path.cwd() / incidents.suggested_filename()
            )
            bundle = incidents.create_bundle(
                output,
                run_id=arguments.run_id,
                run_limit=arguments.limit,
                recent_failure_limit=arguments.recent_failures,
                event_limit=arguments.event_limit,
                overwrite=arguments.overwrite,
            )
            _print(bundle.to_dict())
            return 0
        if arguments.observe_command == "trace":
            _print(observability.trace(arguments.run_id).to_dict())
            return 0
        if arguments.observe_command == "trace-tree":
            _print(observability.trace_tree(arguments.run_id).to_dict())
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
        approval = runtime.resolve_approval(
            arguments.approval_id, not arguments.reject, arguments.reason
        )
        _print({"id": approval.id, "run_id": approval.run_id, "status": approval.status})
        return 0

    if arguments.command == "doctor":
        doctor_report = RuntimeDoctor(runtime.store).run(arguments.run_id)
        if arguments.json:
            _print(doctor_report.to_dict())
        else:
            print(f"Runtime Doctor: {doctor_report.status}")
            print(f"Database: {doctor_report.database_path}")
            for check in doctor_report.checks:
                marker = {"ok": "[OK]", "attention": "[ATTENTION]", "unhealthy": "[UNHEALTHY]"}[
                    check.level
                ]
                print(f"{marker} {check.name}: {check.summary}")
        return doctor_report.exit_code

    if arguments.command == "resolve-unknown":
        unknown_execution = runtime.resolve_unknown_tool(
            arguments.execution_id,
            arguments.outcome,
            result_content=arguments.result,
            error=arguments.error,
            reason=arguments.reason,
            resolved_by=arguments.resolved_by,
        )
        _print(
            {
                "id": unknown_execution.id,
                "run_id": unknown_execution.run_id,
                "status": unknown_execution.status,
                "result": unknown_execution.result_content,
                "error": unknown_execution.error,
                "resolution": unknown_execution.resolution,
                "resolution_reason": unknown_execution.resolution_reason,
                "resolved_by": unknown_execution.resolved_by,
                "resolved_at": unknown_execution.resolved_at,
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

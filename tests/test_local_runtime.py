from __future__ import annotations

import asyncio
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agent_runtime.cli import async_main, build_parser
from agent_runtime.local_config import (
    LocalConfigError,
    load_local_settings,
    resolve_local_config_path,
    write_default_local_config,
)
from agent_runtime.local_runtime import (
    LocalRuntimeLock,
    LocalRuntimeLockError,
    create_configured_local_runtime,
    local_runtime_status,
)
from agent_runtime.telemetry import configure_structured_logging


def test_local_config_round_trip_and_environment_override(workspace: Path) -> None:
    path = write_default_local_config(workspace / "agent-runtime.toml", workspace=workspace)
    settings = load_local_settings(
        path,
        environment={
            "AGENT_RUNTIME_PORT": "8123",
            "AGENT_RUNTIME_LOG_LEVEL": "WARNING",
        },
    )

    assert settings.workspace == workspace.resolve()
    assert settings.state_dir == workspace / ".agent-runtime"
    assert settings.port == 8123
    assert settings.log_level == "WARNING"
    assert settings.provider == "mock"
    assert settings.enable_process_tool is True
    assert Path(settings.allowed_executables[0]).resolve() == Path(sys.executable).resolve()
    assert settings.allowed_executables[1] == "git"
    assert settings.process_timeout_seconds == 120.0
    assert settings.public_dict()["model"]["api_key_env"] == "OPENAI_API_KEY"


def test_local_config_rejects_remote_bind_and_invalid_tool_capacity(workspace: Path) -> None:
    path = write_default_local_config(workspace / "agent-runtime.toml", workspace=workspace)
    text = path.read_text(encoding="utf-8").replace('host = "127.0.0.1"', 'host = "0.0.0.0"')
    path.write_text(text, encoding="utf-8")
    with pytest.raises(LocalConfigError, match="loopback"):
        load_local_settings(path)

    text = text.replace('host = "0.0.0.0"', 'host = "127.0.0.1"').replace(
        "pending_queue_size = 32", "pending_queue_size = 2"
    )
    path.write_text(text, encoding="utf-8")
    with pytest.raises(LocalConfigError, match="pending_queue_size"):
        load_local_settings(path)


def test_local_config_validation_and_override_edges(workspace: Path) -> None:
    explicit = workspace / "custom.toml"
    assert resolve_local_config_path(explicit) == explicit.resolve()

    absolute_state = workspace / "absolute-state"
    path = write_default_local_config(
        explicit,
        workspace=workspace,
        state_dir=absolute_state,
    )
    with pytest.raises(LocalConfigError, match="already exists"):
        write_default_local_config(path)
    write_default_local_config(path, workspace=workspace, force=True)

    with pytest.raises(LocalConfigError, match="does not exist"):
        load_local_settings(workspace / "missing.toml")

    malformed = workspace / "malformed.toml"
    malformed.write_text("[runtime\n", encoding="utf-8")
    with pytest.raises(LocalConfigError, match="Cannot read"):
        load_local_settings(malformed)

    invalid_provider = workspace / "invalid-provider.toml"
    write_default_local_config(invalid_provider, workspace=workspace)
    invalid_provider.write_text(
        invalid_provider.read_text(encoding="utf-8").replace(
            'provider = "mock"', 'provider = "unsupported"'
        ),
        encoding="utf-8",
    )
    with pytest.raises(LocalConfigError, match=r"model\.provider"):
        load_local_settings(invalid_provider)

    invalid_model = workspace / "invalid-model.toml"
    write_default_local_config(invalid_model, workspace=workspace)
    invalid_model.write_text(
        invalid_model.read_text(encoding="utf-8").replace(
            'model = "arithmetic-demo"', 'model = ""'
        ),
        encoding="utf-8",
    )
    with pytest.raises(LocalConfigError, match=r"model\.model"):
        load_local_settings(invalid_model)


def test_openai_config_requires_api_key(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = write_default_local_config(workspace / "agent-runtime.toml", workspace=workspace)
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            'provider = "mock"', 'provider = "openai-compatible"'
        ),
        encoding="utf-8",
    )
    settings = load_local_settings(path, environment={})
    monkeypatch.delenv(settings.api_key_env, raising=False)
    with pytest.raises(LocalConfigError, match="is required"):
        create_configured_local_runtime(settings)


def test_local_runtime_lock_rejects_active_owner_and_recovers_stale_lock(
    workspace: Path,
) -> None:
    path = workspace / "state" / "runtime.lock"
    first = LocalRuntimeLock(path)
    owned = first.acquire()
    assert owned.status == "owned"
    assert LocalRuntimeLock(path).inspect().status == "active"
    with pytest.raises(LocalRuntimeLockError, match="already owned"):
        LocalRuntimeLock(path).acquire()
    first.release()
    assert LocalRuntimeLock(path).inspect().status == "unlocked"

    path.write_text(
        json.dumps(
            {
                "pid": 999_999_999,
                "hostname": owned.hostname,
                "started_at": owned.started_at,
                "runtime_version": "0.8.0",
                "token": "stale",
            }
        ),
        encoding="utf-8",
    )
    recovered = LocalRuntimeLock(path)
    assert recovered.inspect().status == "stale"
    assert recovered.acquire().status == "owned"
    recovered.release()


def test_local_runtime_lock_detects_a_different_live_process(workspace: Path) -> None:
    path = workspace / "state" / "runtime.lock"
    source_root = Path(__file__).resolve().parents[1] / "src"
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(source_root)
    helper = (
        "import sys,time; "
        "from agent_runtime.local_runtime import LocalRuntimeLock; "
        "lock=LocalRuntimeLock(sys.argv[1]); lock.acquire(); "
        "print('ready', flush=True); time.sleep(30)"
    )
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen(
        [sys.executable, "-c", helper, str(path)],
        creationflags=flags,
        env=environment,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None
        assert process.stdout.readline().strip() == "ready"
        assert LocalRuntimeLock(path).inspect().status == "active"
        with pytest.raises(LocalRuntimeLockError):
            LocalRuntimeLock(path).acquire()
    finally:
        process.terminate()
        process.wait(timeout=10)


@pytest.mark.asyncio
async def test_configured_local_runtime_and_status(workspace: Path) -> None:
    config_path = write_default_local_config(workspace / "agent-runtime.toml", workspace=workspace)
    settings = load_local_settings(config_path)
    runtime = create_configured_local_runtime(settings)
    run = await runtime.run(settings.agent_name, "6 * 7")
    assert run.status == "completed"
    assert run.result == "The result is 42."
    await runtime.shutdown()

    status = local_runtime_status(settings)
    assert status["status"] == "stopped"
    database = status["state"]["database"]
    assert database["quick_check"] == "ok"
    assert database["schema_version"] >= 8


def test_rotating_structured_log_file(workspace: Path) -> None:
    log_file = workspace / "logs" / "runtime.log"
    logger = configure_structured_logging(
        logger_name="agent_runtime.rotation-test",
        file_path=log_file,
        include_stream=False,
        max_bytes=180,
        backup_count=2,
    )
    for index in range(20):
        logger.info("line-%s-%s", index, "x" * 40)
    for handler in logger.handlers:
        handler.flush()
    assert log_file.is_file()
    assert (workspace / "logs" / "runtime.log.1").is_file()
    assert len(list((workspace / "logs").glob("runtime.log*"))) <= 3
    logger.handlers.clear()


def test_cli_eval_preflight_has_no_lock_or_log_side_effect(
    workspace: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = write_default_local_config(workspace / "agent-runtime.toml", workspace=workspace)
    config_path.write_text(
        config_path.read_text(encoding="utf-8")
        .replace('provider = "mock"', 'provider = "openai-compatible"')
        .replace('api_key_env = "OPENAI_API_KEY"', 'api_key_env = "MISSING_ACCEPTANCE_API_KEY"'),
        encoding="utf-8",
    )
    monkeypatch.delenv("MISSING_ACCEPTANCE_API_KEY", raising=False)

    parser = build_parser()
    arguments = parser.parse_args(
        [
            "--workspace",
            str(workspace),
            "--config",
            str(config_path),
            "eval",
            "run",
            "--case",
            "explain-project",
        ]
    )

    assert asyncio.run(async_main(arguments)) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["code"] == "AcceptanceSuiteError"
    assert "MISSING_ACCEPTANCE_API_KEY" in payload["detail"]
    assert not (workspace / ".agent-runtime").exists()
    assert not (workspace / "logs").exists()


def test_cli_init_and_status(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    parser = build_parser()
    init_args = parser.parse_args(["--workspace", str(workspace), "init"])
    assert asyncio.run(async_main(init_args)) == 0
    initialized = json.loads(capsys.readouterr().out)
    assert initialized["status"] == "initialized"

    status_args = parser.parse_args(["--workspace", str(workspace), "status"])
    assert asyncio.run(async_main(status_args)) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["status"] == "stopped"
    assert status["configuration"]["api"]["host"] == "127.0.0.1"


def test_api_module_import_has_no_runtime_state_side_effect(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(workspace)
    module = importlib.import_module("agent_runtime.api.app")
    importlib.reload(module)
    assert not (workspace / ".agent-runtime").exists()

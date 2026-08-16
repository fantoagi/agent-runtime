from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class LocalConfigError(ValueError):
    """Local runtime configuration is missing or invalid."""


_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
_DEFAULT_CONFIG_NAME = "agent-runtime.toml"


@dataclass(frozen=True, slots=True)
class LocalRuntimeSettings:
    config_path: Path
    workspace: Path
    state_dir: Path
    agent_name: str
    system_prompt: str
    provider: str
    model: str
    base_url: str
    api_key_env: str
    model_timeout_seconds: float
    run_timeout_seconds: float
    shutdown_timeout_seconds: float
    max_inflight_runs: int
    max_concurrent_model_requests: int
    max_sync_tool_workers: int
    max_pending_sync_tools: int
    host: str
    port: int
    log_level: str
    log_file: Path
    log_max_bytes: int
    log_backup_count: int

    @property
    def database_path(self) -> Path:
        return self.state_dir / "runtime.sqlite3"

    @property
    def artifact_path(self) -> Path:
        return self.state_dir / "artifacts"

    @property
    def lock_path(self) -> Path:
        return self.state_dir / "runtime.lock"

    def public_dict(self) -> dict[str, Any]:
        return {
            "config_path": str(self.config_path),
            "runtime": {
                "workspace": str(self.workspace),
                "state_dir": str(self.state_dir),
                "database_path": str(self.database_path),
                "artifact_path": str(self.artifact_path),
                "agent_name": self.agent_name,
                "run_timeout_seconds": self.run_timeout_seconds,
                "shutdown_timeout_seconds": self.shutdown_timeout_seconds,
                "max_inflight_runs": self.max_inflight_runs,
                "max_concurrent_model_requests": self.max_concurrent_model_requests,
            },
            "model": {
                "provider": self.provider,
                "model": self.model,
                "base_url": self.base_url,
                "api_key_env": self.api_key_env,
                "api_key_configured": bool(os.getenv(self.api_key_env)),
                "timeout_seconds": self.model_timeout_seconds,
            },
            "tools": {
                "sync_workers": self.max_sync_tool_workers,
                "pending_queue_size": self.max_pending_sync_tools,
            },
            "api": {"host": self.host, "port": self.port},
            "logging": {
                "level": self.log_level,
                "file": str(self.log_file),
                "max_bytes": self.log_max_bytes,
                "backup_count": self.log_backup_count,
            },
        }


def resolve_local_config_path(
    config_path: str | Path | None,
    workspace: str | Path | None = None,
) -> Path:
    if config_path is not None:
        return Path(config_path).expanduser().resolve()
    root = Path(workspace or ".").expanduser().resolve()
    return root / _DEFAULT_CONFIG_NAME


def write_default_local_config(
    config_path: str | Path,
    *,
    workspace: str | Path | None = None,
    state_dir: str | Path | None = None,
    force: bool = False,
) -> Path:
    path = Path(config_path).expanduser().resolve()
    if path.exists() and not force:
        raise LocalConfigError(f"Configuration already exists: {path}")
    root = Path(workspace or path.parent).expanduser().resolve()
    state = Path(state_dir).expanduser() if state_dir is not None else Path(".agent-runtime")
    if state.is_absolute():
        state_value = state.resolve().as_posix()
    else:
        state_value = state.as_posix()
    workspace_value = "." if root == path.parent else root.as_posix()
    content = f'''# Agent Runtime local single-user configuration.\n\n[runtime]\nworkspace = "{workspace_value}"\nstate_dir = "{state_value}"\nagent_name = "local"\nsystem_prompt = "You are a concise local assistant. Use tools only when necessary."\nrun_timeout_seconds = 300\nshutdown_timeout_seconds = 30\nmax_inflight_runs = 8\nmax_concurrent_model_requests = 4\n\n[model]\n# Use "mock" for offline verification or "openai-compatible" for a real model.\nprovider = "mock"\nmodel = "arithmetic-demo"\nbase_url = "https://api.openai.com/v1"\napi_key_env = "OPENAI_API_KEY"\ntimeout_seconds = 60\n\n[tools]\nsync_workers = 8\npending_queue_size = 32\n\n[api]\n# Local Stable Runtime intentionally listens on loopback only.\nhost = "127.0.0.1"\nport = 8000\n\n[logging]\nlevel = "INFO"\nfile = "logs/runtime.log"\nmax_file_size_mb = 20\nbackup_count = 5\n'''
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def load_local_settings(
    config_path: str | Path,
    *,
    workspace_override: str | Path | None = None,
    state_dir_override: str | Path | None = None,
    host_override: str | None = None,
    port_override: int | None = None,
    environment: Mapping[str, str] | None = None,
) -> LocalRuntimeSettings:
    path = Path(config_path).expanduser().resolve()
    if not path.is_file():
        raise LocalConfigError(
            f"Local runtime configuration does not exist: {path}. "
            "Run `agent-runtime init` first."
        )
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise LocalConfigError(f"Cannot read local runtime configuration {path}: {error}") from error
    env = environment if environment is not None else os.environ
    runtime = _section(raw, "runtime")
    model = _section(raw, "model")
    tools = _section(raw, "tools")
    api = _section(raw, "api")
    logging_section = _section(raw, "logging")

    workspace_raw = _first(
        workspace_override,
        env.get("AGENT_RUNTIME_WORKSPACE"),
        _string(runtime, "workspace", "."),
    )
    workspace = _resolve_path(str(workspace_raw), path.parent)
    state_raw = _first(
        state_dir_override,
        env.get("AGENT_RUNTIME_STATE_DIR"),
        _string(runtime, "state_dir", ".agent-runtime"),
    )
    state_dir = _resolve_path(str(state_raw), workspace)

    provider = str(
        _first(env.get("AGENT_RUNTIME_MODEL_PROVIDER"), _string(model, "provider", "mock"))
    ).strip().lower()
    if provider not in {"mock", "openai-compatible"}:
        raise LocalConfigError("model.provider must be 'mock' or 'openai-compatible'.")
    default_model = "arithmetic-demo" if provider == "mock" else "gpt-4.1-mini"
    model_name = str(
        _first(env.get("AGENT_RUNTIME_MODEL"), _string(model, "model", default_model))
    ).strip()
    if not model_name:
        raise LocalConfigError("model.model must not be empty.")
    base_url = str(
        _first(
            env.get("AGENT_RUNTIME_MODEL_BASE_URL"),
            _string(model, "base_url", "https://api.openai.com/v1"),
        )
    ).strip()
    api_key_env = _string(model, "api_key_env", "OPENAI_API_KEY").strip()
    if not api_key_env:
        raise LocalConfigError("model.api_key_env must not be empty.")

    host = str(
        _first(host_override, env.get("AGENT_RUNTIME_HOST"), _string(api, "host", "127.0.0.1"))
    ).strip()
    if host not in _LOCAL_HOSTS:
        raise LocalConfigError(
            "Local Stable Runtime only permits loopback hosts: 127.0.0.1, localhost, or ::1."
        )
    port_value = _first(port_override, env.get("AGENT_RUNTIME_PORT"), _integer(api, "port", 8000))
    port = _coerce_int(port_value, "api.port")
    if not 1 <= port <= 65535:
        raise LocalConfigError("api.port must be between 1 and 65535.")

    log_raw = _string(logging_section, "file", "logs/runtime.log")
    log_file = _resolve_path(log_raw, state_dir)
    log_level = str(
        _first(env.get("AGENT_RUNTIME_LOG_LEVEL"), _string(logging_section, "level", "INFO"))
    ).upper()
    if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise LocalConfigError("logging.level is invalid.")
    max_file_size_mb = _integer(logging_section, "max_file_size_mb", 20)
    backup_count = _integer(logging_section, "backup_count", 5)
    if max_file_size_mb < 1 or backup_count < 1:
        raise LocalConfigError("logging max_file_size_mb and backup_count must be positive.")

    settings = LocalRuntimeSettings(
        config_path=path,
        workspace=workspace,
        state_dir=state_dir,
        agent_name=_nonempty(_string(runtime, "agent_name", "local"), "runtime.agent_name"),
        system_prompt=_nonempty(
            _string(
                runtime,
                "system_prompt",
                "You are a concise local assistant. Use tools only when necessary.",
            ),
            "runtime.system_prompt",
        ),
        provider=provider,
        model=model_name,
        base_url=base_url,
        api_key_env=api_key_env,
        model_timeout_seconds=_positive_float(model, "timeout_seconds", 60.0),
        run_timeout_seconds=_positive_float(runtime, "run_timeout_seconds", 300.0),
        shutdown_timeout_seconds=_positive_float(
            runtime, "shutdown_timeout_seconds", 30.0
        ),
        max_inflight_runs=_positive_int(runtime, "max_inflight_runs", 8),
        max_concurrent_model_requests=_positive_int(
            runtime, "max_concurrent_model_requests", 4
        ),
        max_sync_tool_workers=_positive_int(tools, "sync_workers", 8),
        max_pending_sync_tools=_positive_int(tools, "pending_queue_size", 32),
        host=host,
        port=port,
        log_level=log_level,
        log_file=log_file,
        log_max_bytes=max_file_size_mb * 1024 * 1024,
        log_backup_count=backup_count,
    )
    if settings.max_pending_sync_tools < settings.max_sync_tool_workers:
        raise LocalConfigError(
            "tools.pending_queue_size must be at least tools.sync_workers."
        )
    if settings.provider == "openai-compatible" and not settings.base_url:
        raise LocalConfigError("model.base_url must not be empty for openai-compatible.")
    return settings


def _section(data: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = data.get(name, {})
    if not isinstance(value, Mapping):
        raise LocalConfigError(f"[{name}] must be a TOML table.")
    return value


def _string(section: Mapping[str, Any], key: str, default: str) -> str:
    value = section.get(key, default)
    if not isinstance(value, str):
        raise LocalConfigError(f"{key} must be a string.")
    return value


def _integer(section: Mapping[str, Any], key: str, default: int) -> int:
    return _coerce_int(section.get(key, default), key)


def _coerce_int(value: object, name: str) -> int:
    if isinstance(value, bool):
        raise LocalConfigError(f"{name} must be an integer.")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError as error:
            raise LocalConfigError(f"{name} must be an integer.") from error
    raise LocalConfigError(f"{name} must be an integer.")


def _positive_int(section: Mapping[str, Any], key: str, default: int) -> int:
    value = _integer(section, key, default)
    if value < 1:
        raise LocalConfigError(f"{key} must be positive.")
    return value


def _positive_float(section: Mapping[str, Any], key: str, default: float) -> float:
    value = section.get(key, default)
    if isinstance(value, bool):
        raise LocalConfigError(f"{key} must be a positive number.")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as error:
        raise LocalConfigError(f"{key} must be a positive number.") from error
    if parsed <= 0:
        raise LocalConfigError(f"{key} must be a positive number.")
    return parsed


def _resolve_path(value: str, base: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def _first(*values: object | None) -> object:
    for value in values:
        if value is not None:
            return value
    raise AssertionError("at least one fallback value is required")


def _nonempty(value: str, name: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise LocalConfigError(f"{name} must not be empty.")
    return stripped

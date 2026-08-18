from __future__ import annotations

import importlib
import json
import os
import socket
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO
from uuid import uuid4

from .coding_tools import register_coding_tools
from .completion import CodingCompletionPolicy
from .domain import AgentDefinition, ModelConfig, ToolDefinition
from .git_tools import register_git_tools
from .local_config import LocalConfigError, LocalRuntimeSettings
from .providers import (
    MockProvider,
    ModelProvider,
    OpenAICompatibleProvider,
    StreamingModelProvider,
    arithmetic_demo_responder,
)
from .runtime import Runtime, RuntimeConfig
from .sandbox import LocalProcessSandbox, SandboxLimits, register_process_tool
from .tools import ToolRegistry, register_builtin_tools
from .version import __version__
from .workspace_context import build_local_agent_prompt, load_workspace_instructions

_WINDOWS_LOCK_OFFSET = 1 << 20


class LocalRuntimeLockError(RuntimeError):
    """The configured local state directory is already owned by another process."""


@dataclass(frozen=True, slots=True)
class LocalRuntimeLockInfo:
    status: str
    path: Path
    pid: int | None = None
    hostname: str | None = None
    started_at: str | None = None
    token: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "path": str(self.path),
            "pid": self.pid,
            "hostname": self.hostname,
            "started_at": self.started_at,
        }


class LocalRuntimeLock:
    """OS-backed single-process ownership lock for one local Runtime state directory."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        self._token: str | None = None
        self._handle: BinaryIO | None = None

    def acquire(self) -> LocalRuntimeLockInfo:
        if self._handle is not None:
            raise LocalRuntimeLockError(f"Local Runtime lock is already acquired: {self.path}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        handle = os.fdopen(descriptor, "r+b", buffering=0)
        if not _try_lock_handle(handle):
            handle.close()
            existing = self.inspect()
            raise LocalRuntimeLockError(
                "Local Runtime state is already owned by "
                f"PID {existing.pid} on {existing.hostname}: {self.path}"
            )

        token = uuid4().hex
        hostname = socket.gethostname()
        started_at = datetime.now(UTC).isoformat()
        payload = {
            "pid": os.getpid(),
            "hostname": hostname,
            "started_at": started_at,
            "runtime_version": __version__,
            "token": token,
        }
        try:
            _write_lock_payload(handle, payload)
        except BaseException:
            _unlock_handle(handle)
            handle.close()
            raise
        self._token = token
        self._handle = handle
        return LocalRuntimeLockInfo(
            status="owned",
            path=self.path,
            pid=os.getpid(),
            hostname=hostname,
            started_at=started_at,
            token=token,
        )

    def release(self) -> None:
        handle = self._handle
        token = self._token
        self._handle = None
        self._token = None
        if handle is None:
            return
        try:
            _write_lock_payload(
                handle,
                {
                    "released": True,
                    "runtime_version": __version__,
                    "token": token,
                },
            )
        finally:
            _unlock_handle(handle)
            handle.close()

    def inspect(self) -> LocalRuntimeLockInfo:
        if not self.path.exists():
            return LocalRuntimeLockInfo(status="unlocked", path=self.path)
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if payload.get("released") is True:
                return LocalRuntimeLockInfo(status="unlocked", path=self.path)
            pid = int(payload["pid"])
            hostname = str(payload["hostname"])
            started_at = str(payload["started_at"])
            token = str(payload["token"])
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return LocalRuntimeLockInfo(status="stale", path=self.path)
        same_host = hostname == socket.gethostname()
        status = "active" if same_host and _process_is_running(pid) else "stale"
        if not same_host:
            status = "active"
        return LocalRuntimeLockInfo(
            status=status,
            path=self.path,
            pid=pid,
            hostname=hostname,
            started_at=started_at,
            token=token,
        )

    def __enter__(self) -> LocalRuntimeLock:
        self.acquire()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        del exc_type, exc, traceback
        self.release()


def _try_lock_handle(handle: BinaryIO) -> bool:
    if os.name == "nt":
        import msvcrt

        handle.seek(_WINDOWS_LOCK_OFFSET)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True

    fcntl: Any = importlib.import_module("fcntl")

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock_handle(handle: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(_WINDOWS_LOCK_OFFSET)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        return

    fcntl: Any = importlib.import_module("fcntl")

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


def _write_lock_payload(handle: BinaryIO, payload: dict[str, Any]) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    handle.seek(0)
    handle.write(encoded)
    handle.truncate()
    handle.flush()
    os.fsync(handle.fileno())


def create_configured_local_runtime(settings: LocalRuntimeSettings) -> Runtime:
    if not settings.workspace.is_dir():
        raise LocalConfigError(f"Runtime workspace does not exist: {settings.workspace}")
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    tools = ToolRegistry()
    register_builtin_tools(tools, artifact_path=settings.artifact_path)
    register_coding_tools(tools)
    process_definition = None
    git_definitions: tuple[ToolDefinition, ...] = ()
    if settings.enable_process_tool:
        try:
            process_sandbox = LocalProcessSandbox(
                settings.workspace,
                allowed_executables=settings.allowed_executables,
                limits=SandboxLimits(
                    timeout_seconds=settings.process_timeout_seconds,
                    max_output_bytes=settings.process_max_output_bytes,
                    max_concurrent_processes=settings.process_max_concurrent,
                ),
            )
        except ValueError as error:
            raise LocalConfigError(f"Invalid local process tool configuration: {error}") from error
        process_definition = register_process_tool(
            tools,
            process_sandbox,
            handler_timeout_seconds=settings.process_timeout_seconds + 5.0,
        )
        git_definitions = register_git_tools(tools, process_sandbox)
    provider: ModelProvider | StreamingModelProvider
    if settings.provider == "mock":
        provider = MockProvider(arithmetic_demo_responder)
    else:
        api_key = os.getenv(settings.api_key_env)
        if not api_key:
            raise LocalConfigError(
                f"Environment variable {settings.api_key_env!r} is required by "
                "the openai-compatible provider."
            )
        provider = OpenAICompatibleProvider(
            base_url=settings.base_url,
            api_key=api_key,
            timeout_seconds=settings.model_timeout_seconds,
        )
    runtime = Runtime(
        RuntimeConfig(
            workspace_path=settings.workspace,
            database_path=settings.database_path,
            artifact_path=settings.artifact_path,
            run_timeout_seconds=settings.run_timeout_seconds,
            model_timeout_seconds=settings.model_timeout_seconds,
            shutdown_timeout_seconds=settings.shutdown_timeout_seconds,
            max_sync_tool_workers=settings.max_sync_tool_workers,
            max_pending_sync_tools=settings.max_pending_sync_tools,
            max_inflight_runs=settings.max_inflight_runs,
            max_concurrent_model_requests=settings.max_concurrent_model_requests,
        ),
        provider=provider,
        tools=tools,
        completion_policy=CodingCompletionPolicy(
            {
                "write_text_file",
                "replace_text",
                "apply_patch",
                *(definition.name for definition in git_definitions),
                *([process_definition.name] if process_definition is not None else []),
            }
        ),
    )
    instruction_bundle = load_workspace_instructions(
        settings.workspace,
        enabled=settings.workspace_instructions_enabled,
        configured_files=settings.workspace_instruction_files,
        max_chars=settings.workspace_instruction_max_chars,
    )
    runtime.register_agent(
        AgentDefinition(
            name=settings.agent_name,
            system_prompt=build_local_agent_prompt(
                settings.system_prompt,
                instruction_bundle,
            ),
            tools=[
                tools.get("calculator").definition,
                tools.get("list_files").definition,
                tools.get("search_text").definition,
                tools.get("read_file_lines").definition,
                tools.get("read_text_file").definition,
                tools.get("read_artifact").definition,
                tools.get("replace_text").definition,
                tools.get("apply_patch").definition,
                tools.get("write_text_file").definition,
                *git_definitions,
                *([process_definition] if process_definition is not None else []),
            ],
            model=ModelConfig(provider=settings.provider, model=settings.model),
        )
    )
    return runtime


def local_runtime_status(settings: LocalRuntimeSettings) -> dict[str, Any]:
    lock = LocalRuntimeLock(settings.lock_path).inspect()
    database = _database_status(settings.database_path)
    instruction_bundle = load_workspace_instructions(
        settings.workspace,
        enabled=settings.workspace_instructions_enabled,
        configured_files=settings.workspace_instruction_files,
        max_chars=settings.workspace_instruction_max_chars,
    )
    return {
        "status": "running" if lock.status == "active" else "stopped",
        "version": __version__,
        "lock": lock.to_dict(),
        "configuration": settings.public_dict(),
        "workspace_context": instruction_bundle.public_dict(),
        "state": {
            "directory_exists": settings.state_dir.is_dir(),
            "database": database,
            "wal_bytes": _file_size(Path(f"{settings.database_path}-wal")),
            "artifact_files": _artifact_count(settings.artifact_path),
            "artifact_bytes": _directory_size(settings.artifact_path),
            "log_bytes": _file_size(settings.log_file),
        },
    }


def _process_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        return _windows_process_is_running(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _windows_process_is_running(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes

    process_query_limited_information = 0x1000
    still_active = 259
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    open_process.restype = wintypes.HANDLE
    get_exit_code = kernel32.GetExitCodeProcess
    get_exit_code.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    get_exit_code.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL

    handle = open_process(process_query_limited_information, False, pid)
    if not handle:
        return ctypes.get_last_error() == 5
    try:
        exit_code = wintypes.DWORD()
        if not get_exit_code(handle, ctypes.byref(exit_code)):
            return True
        return exit_code.value == still_active
    finally:
        close_handle(handle)


def _database_status(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"exists": False, "path": str(path), "bytes": 0}
    result: dict[str, Any] = {
        "exists": True,
        "path": str(path),
        "bytes": _file_size(path),
    }
    connection: sqlite3.Connection | None = None
    try:
        uri = path.resolve().as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=1.0)
        result["quick_check"] = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        result["journal_mode"] = str(
            connection.execute("PRAGMA journal_mode").fetchone()[0]
        ).lower()
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
        ).fetchone()
        if table is not None:
            result["schema_version"] = int(
                connection.execute(
                    "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
                ).fetchone()[0]
            )
    except sqlite3.Error as error:
        result["quick_check"] = "unavailable"
        result["error"] = str(error)
    finally:
        if connection is not None:
            connection.close()
    return result


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _artifact_count(path: Path) -> int:
    if not path.is_dir():
        return 0
    count = 0
    try:
        for item in path.rglob("*"):
            if item.is_file():
                count += 1
    except OSError:
        return count
    return count


def _directory_size(path: Path) -> int:
    if not path.is_dir():
        return 0
    total = 0
    try:
        for item in path.rglob("*"):
            if item.is_file():
                total += _file_size(item)
    except OSError:
        return total
    return total

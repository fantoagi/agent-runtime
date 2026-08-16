from __future__ import annotations

import asyncio
import os
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .domain import (
    SandboxExecutionError,
    SandboxOutputLimitError,
    SandboxTimeoutError,
    SandboxViolationError,
    ToolCapability,
    ToolDefinition,
    ToolValidationError,
)
from .tools import CancellationToken, ToolContext, ToolRegistry, ToolResult


@dataclass(frozen=True, slots=True)
class SandboxLimits:
    timeout_seconds: float = 30.0
    max_output_bytes: int = 1_000_000
    max_concurrent_processes: int = 4

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive.")
        if self.max_output_bytes < 1024:
            raise ValueError("max_output_bytes must be at least 1024.")
        if self.max_concurrent_processes < 1:
            raise ValueError("max_concurrent_processes must be at least 1.")


@dataclass(frozen=True, slots=True)
class SandboxRequest:
    argv: tuple[str, ...]
    cwd: str = "."
    environment: Mapping[str, str] = field(default_factory=dict)
    timeout_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class SandboxResult:
    argv: tuple[str, ...]
    cwd: str
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "argv": list(self.argv),
            "cwd": self.cwd,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration_ms": self.duration_ms,
        }


class SandboxExecutor(Protocol):
    async def execute(
        self,
        request: SandboxRequest,
        *,
        cancellation_token: CancellationToken | None = None,
    ) -> SandboxResult: ...

    def snapshot(self) -> dict[str, Any]: ...

    def close(self) -> None: ...

    async def aclose(self) -> None: ...


class LocalProcessSandbox:
    """Bounded local subprocess adapter; this is not a container security boundary."""

    _DEFAULT_ENVIRONMENT = frozenset(
        {
            "PATH",
            "PATHEXT",
            "SYSTEMROOT",
            "WINDIR",
            "COMSPEC",
            "TEMP",
            "TMP",
            "TMPDIR",
            "LANG",
            "LC_ALL",
        }
    )

    def __init__(
        self,
        workspace_path: str | Path,
        *,
        allowed_executables: Iterable[str | Path],
        allowed_environment: Iterable[str] = (),
        limits: SandboxLimits | None = None,
    ) -> None:
        self.workspace_path = Path(workspace_path).resolve()
        self.workspace_path.mkdir(parents=True, exist_ok=True)
        self.limits = limits or SandboxLimits()
        self._allowed_executables = frozenset(
            self._resolve_configured_executable(value) for value in allowed_executables
        )
        if not self._allowed_executables:
            raise ValueError("LocalProcessSandbox requires at least one allowed executable.")
        self._allowed_environment = self._DEFAULT_ENVIRONMENT | frozenset(
            str(name) for name in allowed_environment
        )
        self._capacity = asyncio.Semaphore(self.limits.max_concurrent_processes)
        self._processes: dict[int, asyncio.subprocess.Process] = {}
        self._accepting = True

    @staticmethod
    def _resolve_configured_executable(value: str | Path) -> Path:
        text = str(value)
        candidate = Path(text)
        if candidate.is_absolute():
            resolved = candidate.resolve()
        else:
            located = shutil.which(text)
            if located is None:
                raise ValueError(f"Allowed executable cannot be resolved: {text}")
            resolved = Path(located).resolve()
        if not resolved.is_file():
            raise ValueError(f"Allowed executable does not exist: {resolved}")
        return resolved

    def _resolve_requested_executable(self, value: str) -> Path:
        candidate = Path(value)
        if candidate.is_absolute():
            resolved = candidate.resolve()
        else:
            search_path = os.environ.get("PATH")
            located = shutil.which(value, path=search_path)
            if located is None:
                raise SandboxViolationError(f"Executable cannot be resolved: {value}")
            resolved = Path(located).resolve()
        if resolved not in self._allowed_executables:
            raise SandboxViolationError(f"Executable is not allowed by sandbox policy: {value}")
        return resolved

    def _resolve_cwd(self, value: str) -> Path:
        candidate = (self.workspace_path / value).resolve()
        if candidate != self.workspace_path and self.workspace_path not in candidate.parents:
            raise SandboxViolationError("Sandbox working directory escapes the workspace.")
        if not candidate.is_dir():
            raise SandboxViolationError(f"Sandbox working directory does not exist: {value}")
        return candidate

    def _environment(self, overrides: Mapping[str, str]) -> dict[str, str]:
        unexpected = sorted(set(overrides) - self._allowed_environment)
        if unexpected:
            raise SandboxViolationError(
                "Environment variable is not allowed by sandbox policy: "
                + ", ".join(unexpected)
            )
        environment = {
            name: value
            for name, value in os.environ.items()
            if name in self._allowed_environment
        }
        environment.update({str(name): str(value) for name, value in overrides.items()})
        return environment

    def snapshot(self) -> dict[str, Any]:
        return {
            "kind": "local-process",
            "accepting": self._accepting,
            "strong_isolation": False,
            "network_isolation": False,
            "active_processes": len(self._processes),
            "max_concurrent_processes": self.limits.max_concurrent_processes,
            "timeout_seconds": self.limits.timeout_seconds,
            "max_output_bytes": self.limits.max_output_bytes,
            "workspace_path": str(self.workspace_path),
            "allowed_executables": sorted(str(path) for path in self._allowed_executables),
            "allowed_environment": sorted(self._allowed_environment),
        }

    def close(self) -> None:
        self._accepting = False

    async def aclose(self) -> None:
        self.close()
        processes = list(self._processes.values())
        if processes:
            await asyncio.gather(
                *(self._terminate_process_tree(process) for process in processes),
                return_exceptions=True,
            )

    async def execute(
        self,
        request: SandboxRequest,
        *,
        cancellation_token: CancellationToken | None = None,
    ) -> SandboxResult:
        if not self._accepting:
            raise SandboxExecutionError("Sandbox is closed and no longer accepts processes.")
        if not request.argv or any(not isinstance(value, str) or not value for value in request.argv):
            raise ToolValidationError("Sandbox argv must contain non-empty strings.")
        if cancellation_token is not None:
            cancellation_token.raise_if_cancelled()

        executable = self._resolve_requested_executable(request.argv[0])
        argv = (str(executable), *request.argv[1:])
        cwd = self._resolve_cwd(request.cwd)
        environment = self._environment(request.environment)
        timeout = self.limits.timeout_seconds
        if request.timeout_seconds is not None:
            if request.timeout_seconds <= 0:
                raise ToolValidationError("Sandbox timeout_seconds must be positive.")
            timeout = min(timeout, request.timeout_seconds)

        async with self._capacity:
            if not self._accepting:
                raise SandboxExecutionError("Sandbox is closing.")
            if cancellation_token is not None:
                cancellation_token.raise_if_cancelled()
            process = await self._spawn(argv, cwd, environment)
            self._processes[process.pid] = process
            started = time.monotonic()
            output_state = {"size": 0}
            overflow = asyncio.Event()
            stdout_task = asyncio.create_task(
                self._read_stream(process.stdout, output_state, overflow)
            )
            stderr_task = asyncio.create_task(
                self._read_stream(process.stderr, output_state, overflow)
            )
            wait_task = asyncio.create_task(process.wait())
            overflow_task = asyncio.create_task(overflow.wait())
            cancel_task = (
                asyncio.create_task(cancellation_token.wait())
                if cancellation_token is not None
                else None
            )
            watchers: set[asyncio.Task[Any]] = {wait_task, overflow_task}
            if cancel_task is not None:
                watchers.add(cancel_task)
            try:
                done, _ = await asyncio.wait(
                    watchers,
                    timeout=timeout,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if cancel_task is not None and cancel_task in done:
                    await self._terminate_process_tree(process)
                    raise asyncio.CancelledError
                if overflow_task in done and overflow.is_set():
                    await self._terminate_process_tree(process)
                    raise SandboxOutputLimitError(
                        f"Sandbox output exceeded {self.limits.max_output_bytes} bytes."
                    )
                if wait_task not in done:
                    await self._terminate_process_tree(process)
                    raise SandboxTimeoutError(f"Sandbox process timed out after {timeout}s.")
                exit_code = await wait_task
                stdout_bytes, stderr_bytes = await asyncio.gather(stdout_task, stderr_task)
                return SandboxResult(
                    argv=argv,
                    cwd=str(cwd),
                    exit_code=exit_code,
                    stdout=stdout_bytes.decode("utf-8", errors="replace"),
                    stderr=stderr_bytes.decode("utf-8", errors="replace"),
                    duration_ms=round((time.monotonic() - started) * 1000, 3),
                )
            except asyncio.CancelledError:
                await self._terminate_process_tree(process)
                raise
            finally:
                self._processes.pop(process.pid, None)
                for task in watchers | {stdout_task, stderr_task}:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(
                    *(task for task in watchers | {stdout_task, stderr_task}),
                    return_exceptions=True,
                )

    async def _spawn(
        self,
        argv: tuple[str, ...],
        cwd: Path,
        environment: Mapping[str, str],
    ) -> asyncio.subprocess.Process:
        kwargs: dict[str, Any] = {
            "cwd": str(cwd),
            "env": dict(environment),
            "stdin": asyncio.subprocess.DEVNULL,
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
        }
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        try:
            return await asyncio.create_subprocess_exec(*argv, **kwargs)
        except OSError as error:
            raise SandboxExecutionError(f"Sandbox process could not start: {error}") from error

    async def _read_stream(
        self,
        stream: asyncio.StreamReader | None,
        state: dict[str, int],
        overflow: asyncio.Event,
    ) -> bytes:
        if stream is None:
            return b""
        chunks: list[bytes] = []
        while True:
            chunk = await stream.read(64 * 1024)
            if not chunk:
                break
            remaining = self.limits.max_output_bytes - state["size"]
            if remaining <= 0:
                overflow.set()
                break
            accepted = chunk[:remaining]
            chunks.append(accepted)
            state["size"] += len(accepted)
            if len(accepted) != len(chunk):
                overflow.set()
                break
        return b"".join(chunks)

    async def _terminate_process_tree(self, process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        if sys.platform == "win32":
            try:
                killer = await asyncio.create_subprocess_exec(
                    "taskkill",
                    "/PID",
                    str(process.pid),
                    "/T",
                    "/F",
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await asyncio.wait_for(killer.wait(), timeout=5.0)
            except (OSError, TimeoutError):
                process.kill()
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            await asyncio.wait_for(process.wait(), timeout=5.0)
        except TimeoutError:
            process.kill()
            await process.wait()


def register_process_tool(
    registry: ToolRegistry,
    sandbox: SandboxExecutor,
    *,
    name: str = "run_process",
) -> ToolDefinition:
    definition = ToolDefinition(
        name=name,
        description=(
            "Run an explicitly allowed executable inside the configured local process sandbox. "
            "The command is passed as argv and never through a shell."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "argv": {"type": "array", "items": {"type": "string"}},
                "cwd": {"type": "string"},
                "env": {"type": "object"},
                "timeout_seconds": {"type": "number"},
            },
            "required": ["argv"],
            "additionalProperties": False,
        },
        requires_approval=True,
        side_effecting=True,
        capabilities=(
            ToolCapability.PROCESS_EXEC,
            ToolCapability.FILE_READ,
            ToolCapability.FILE_WRITE,
        ),
        sandbox_only=True,
    )

    async def run_process(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        raw_argv = arguments["argv"]
        if not isinstance(raw_argv, list) or any(not isinstance(item, str) for item in raw_argv):
            raise ToolValidationError("run_process argv must be an array of strings.")
        raw_environment = arguments.get("env", {})
        if not isinstance(raw_environment, dict) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in raw_environment.items()
        ):
            raise ToolValidationError("run_process env must map string names to string values.")
        timeout_value = arguments.get("timeout_seconds")
        timeout_seconds = float(timeout_value) if timeout_value is not None else None
        result = await sandbox.execute(
            SandboxRequest(
                argv=tuple(raw_argv),
                cwd=str(arguments.get("cwd", ".")),
                environment=raw_environment,
                timeout_seconds=timeout_seconds,
            ),
            cancellation_token=context.cancellation_token,
        )
        content = result.stdout
        if result.stderr:
            content = f"{content}\n[stderr]\n{result.stderr}" if content else result.stderr
        if not content:
            content = f"Process exited with code {result.exit_code}."
        return ToolResult(content=content, data=result.to_dict())

    registry.manage(sandbox)
    registry.register(definition, run_process, sandboxed=True)
    return definition

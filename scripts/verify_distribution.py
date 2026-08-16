#!/usr/bin/env python3
"""Install a built wheel into a clean virtual environment and run release smokes."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import textwrap
import venv
import zipfile
from pathlib import Path
from uuid import uuid4


def run(command: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, cwd=cwd, env=env, check=True)


def run_capture(
    command: list[str], *, cwd: Path, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    print("+", " ".join(command), flush=True)
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        check=True,
        text=True,
        capture_output=True,
    )
    if completed.stdout:
        print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)
    return completed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("wheel", type=Path)
    args = parser.parse_args()
    wheel = args.wheel.resolve()
    if wheel.is_dir():
        candidates = sorted(wheel.glob("agent_runtime-*-py3-none-any.whl"))
        if len(candidates) != 1:
            parser.error(
                f"expected exactly one agent-runtime wheel in {wheel}, found {len(candidates)}"
            )
        wheel = candidates[0]
    if not wheel.is_file():
        parser.error(f"wheel not found: {wheel}")

    base = Path.cwd() / ".runtime-test-data" / f"wheel-smoke-{uuid4().hex}"
    environment = base / "venv"
    workspace = base / "workspace"
    workspace.mkdir(parents=True, exist_ok=False)
    try:
        venv.EnvBuilder(with_pip=True, clear=True).create(environment)
        scripts = environment / ("Scripts" if os.name == "nt" else "bin")
        python = scripts / ("python.exe" if os.name == "nt" else "python")
        cli = scripts / ("agent-runtime.exe" if os.name == "nt" else "agent-runtime")
        run([str(python), "-m", "pip", "install", "--upgrade", "pip"], cwd=workspace)
        run([str(python), "-m", "pip", "install", f"{wheel}[api]"], cwd=workspace)
        run([str(cli), "--workspace", str(workspace), "init"], cwd=workspace)
        initialized_status = run_capture(
            [str(cli), "--workspace", str(workspace), "status"],
            cwd=workspace,
        )
        initialized_payload = json.loads(initialized_status.stdout)
        assert initialized_payload["status"] == "stopped", initialized_payload
        assert initialized_payload["version"] == "0.8.1", initialized_payload

        local_service_smoke = textwrap.dedent(
            """
            import json
            import os
            import socket
            import subprocess
            import sys
            import time
            from pathlib import Path

            import httpx


            workspace = Path.cwd()
            scripts = Path(sys.executable).parent
            cli = scripts / ("agent-runtime.exe" if os.name == "nt" else "agent-runtime")
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            command = [
                str(cli),
                "--workspace",
                str(workspace),
                "serve",
                "--port",
                str(port),
            ]
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)


            def start_and_wait():
                process = subprocess.Popen(
                    command,
                    cwd=workspace,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=creationflags,
                )
                for _ in range(200):
                    if process.poll() is not None:
                        raise AssertionError(f"local serve exited early: {process.returncode}")
                    try:
                        response = httpx.get(
                            f"http://127.0.0.1:{port}/health",
                            timeout=0.5,
                        )
                        if response.status_code == 200:
                            payload = response.json()
                            assert payload["status"] == "ok", payload
                            assert payload["version"] == "0.8.1", payload
                            return process
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.05)
                process.terminate()
                process.wait(timeout=10)
                raise AssertionError("local serve did not become healthy")


            first = start_and_wait()
            try:
                status = subprocess.run(
                    [str(cli), "--workspace", str(workspace), "status"],
                    cwd=workspace,
                    text=True,
                    capture_output=True,
                    check=True,
                )
                status_payload = json.loads(status.stdout)
                assert status_payload["status"] == "running", status_payload
                assert status_payload["lock"]["status"] == "active", status_payload

                duplicate = subprocess.run(
                    command,
                    cwd=workspace,
                    text=True,
                    capture_output=True,
                    creationflags=creationflags,
                )
                assert duplicate.returncode == 2, duplicate
                duplicate_payload = json.loads(duplicate.stdout)
                assert duplicate_payload["code"] == "LocalRuntimeLockError", duplicate_payload
            finally:
                first.terminate()
                first.wait(timeout=10)

            # A forced stop leaves a stale file; the next standard serve must reclaim it.
            second = start_and_wait()
            second.terminate()
            second.wait(timeout=10)
            stopped = subprocess.run(
                [str(cli), "--workspace", str(workspace), "status"],
                cwd=workspace,
                text=True,
                capture_output=True,
                check=True,
            )
            stopped_payload = json.loads(stopped.stdout)
            assert stopped_payload["status"] == "stopped", stopped_payload
            """
        )
        run([str(python), "-c", local_service_smoke], cwd=workspace)

        demo = run_capture(
            [
                str(cli),
                "--workspace",
                str(workspace),
                "--json-logs",
                "demo",
                "19 * 23",
            ],
            cwd=workspace,
        )
        assert '"event":"runtime.started"' in demo.stderr.replace(" ", "")
        assert '"event":"run.execution.finished"' in demo.stderr.replace(" ", "")

        diagnostics = run_capture(
            [
                str(cli),
                "--workspace",
                str(workspace),
                "observe",
                "diagnostics",
            ],
            cwd=workspace,
        )
        diagnostics_payload = json.loads(diagnostics.stdout)
        assert diagnostics_payload["version"] == "0.8.1", diagnostics_payload
        assert diagnostics_payload["store"]["status"] == "ok", diagnostics_payload

        incident = workspace / "incident.zip"
        run(
            [
                str(cli),
                "--workspace",
                str(workspace),
                "observe",
                "incident-bundle",
                "--output",
                str(incident),
            ],
            cwd=workspace,
        )
        with zipfile.ZipFile(incident) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            assert manifest["runtime_version"] == "0.8.1", manifest
            assert "diagnostics.json" in archive.namelist()

        backup = workspace / "runtime.agent-backup"
        run(
            [
                str(cli),
                "--workspace",
                str(workspace),
                "backup",
                "create",
                "--output",
                str(backup),
            ],
            cwd=workspace,
        )
        run(
            [str(cli), "--workspace", str(workspace), "backup", "verify", str(backup)],
            cwd=workspace,
        )
        run(
            [
                str(cli),
                "--workspace",
                str(workspace),
                "backup",
                "restore",
                str(backup),
                "--force",
                "--discard-previous",
            ],
            cwd=workspace,
        )

        smoke = textwrap.dedent(
            """
            import asyncio
            import socket
            from pathlib import Path

            import httpx
            import uvicorn

            from agent_runtime.api import create_demo_app
            from agent_runtime.sdk import create_local_runtime, demo_agent


            async def main():
                workspace = Path.cwd()
                runtime = create_local_runtime(workspace, workspace / "sdk-state")
                result = await runtime.run(demo_agent(), "6 * 7")
                assert result.status.value == "completed", result.to_dict()
                assert "42" in (result.result or ""), result.to_dict()
                await runtime.shutdown()

                app = create_demo_app(workspace, workspace / "api-state")
                with socket.socket() as probe:
                    probe.bind(("127.0.0.1", 0))
                    port = probe.getsockname()[1]
                server = uvicorn.Server(
                    uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
                )
                server_task = asyncio.create_task(server.serve())
                for _ in range(200):
                    if server.started:
                        break
                    await asyncio.sleep(0.01)
                assert server.started, "uvicorn did not start"
                base_url = f"http://127.0.0.1:{port}"
                async with httpx.AsyncClient(base_url=base_url, timeout=10) as client:
                    health = await client.get("/health")
                    health.raise_for_status()
                    assert health.json()["status"] == "ok"
                    assert health.json()["version"] == "0.8.1"
                    sandbox_status = await client.get("/observability/sandbox")
                    sandbox_status.raise_for_status()
                    assert sandbox_status.json()["policy"]["network.access"] == "deny"
                    incident_bundle = await client.get("/observability/incident-bundle")
                    incident_bundle.raise_for_status()
                    assert incident_bundle.headers["content-type"].startswith("application/zip")
                    created = await client.post("/runs", json={"input": "8 * 8"})
                    created.raise_for_status()
                    run_id = created.json()["id"]
                    for _ in range(200):
                        current = await client.get(f"/runs/{run_id}")
                        current.raise_for_status()
                        if current.json()["status"] in {"completed", "failed", "cancelled"}:
                            break
                        await asyncio.sleep(0.01)
                    assert current.json()["status"] == "completed", current.json()

                    first_id = None
                    async with client.stream("GET", f"/runs/{run_id}/events/stream") as response:
                        response.raise_for_status()
                        async for line in response.aiter_lines():
                            if line.startswith("id: "):
                                first_id = line[4:]
                                break
                    assert first_id is not None
                    resumed_ids = []
                    async with client.stream(
                        "GET",
                        f"/runs/{run_id}/events/stream",
                        headers={"Last-Event-ID": first_id},
                    ) as response:
                        response.raise_for_status()
                        async for line in response.aiter_lines():
                            if line.startswith("id: "):
                                resumed_ids.append(int(line[4:]))
                    assert resumed_ids and min(resumed_ids) > int(first_id)

                server.should_exit = True
                await asyncio.wait_for(server_task, timeout=10)


            asyncio.run(main())
            """
        )
        run([str(python), "-c", smoke], cwd=workspace)
    finally:
        shutil.rmtree(base, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

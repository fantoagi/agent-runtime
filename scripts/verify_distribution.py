#!/usr/bin/env python3
"""Install a built wheel into a clean virtual environment and run release smokes."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import textwrap
import venv
from pathlib import Path
from uuid import uuid4


def run(command: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, cwd=cwd, env=env, check=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("wheel", type=Path)
    args = parser.parse_args()
    wheel = args.wheel.resolve()
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
        run([str(cli), "--workspace", str(workspace), "demo", "19 * 23"], cwd=workspace)

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

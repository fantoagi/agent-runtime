from __future__ import annotations

from pathlib import Path
from typing import Any

from .console import LearningConsole

try:
    from fastapi import FastAPI, HTTPException, status
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles
    from pydantic import BaseModel, Field
except ImportError as error:  # pragma: no cover
    raise ImportError(
        "Learning Console requires the API extra. Install it with `pip install -e .[api]`."
    ) from error


STATIC_ROOT = Path(__file__).with_name("static")


class StartScenarioRequest(BaseModel):
    input: str | None = Field(default=None, min_length=1)


class LabApprovalResolutionRequest(BaseModel):
    approved: bool
    reason: str | None = None


def install_learning_console(app: FastAPI, console: LearningConsole) -> None:
    """Install browser learning routes without adding UI concerns to Runtime Kernel."""
    app.state.learning_console = console
    app.mount("/lab/static", StaticFiles(directory=STATIC_ROOT), name="lab-static")

    @app.get("/lab", include_in_schema=False)
    @app.get("/lab/", include_in_schema=False)
    async def learning_console_page() -> FileResponse:
        return FileResponse(STATIC_ROOT / "index.html")

    @app.get("/lab/api/scenarios")
    async def list_learning_scenarios() -> list[dict[str, Any]]:
        return console.list_scenarios()

    @app.post(
        "/lab/api/scenarios/{scenario_id}/runs",
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def start_learning_scenario(
        scenario_id: str, request: StartScenarioRequest
    ) -> dict[str, Any]:
        try:
            return console.start(scenario_id, request.input)
        except (KeyError, ValueError) as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.get("/lab/api/runs/{run_id}/snapshot")
    async def learning_run_snapshot(run_id: str) -> dict[str, Any]:
        try:
            return console.snapshot(run_id)
        except (KeyError, ValueError) as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.post("/lab/api/approvals/{approval_id}/resolve")
    async def resolve_learning_approval(
        approval_id: str, request: LabApprovalResolutionRequest
    ) -> dict[str, Any]:
        try:
            return await console.resolve_approval(approval_id, request.approved, request.reason)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

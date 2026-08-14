from __future__ import annotations

import httpx
import pytest

from agent_runtime.api.app import create_demo_app


@pytest.mark.asyncio
async def test_learning_console_page_and_scenario_catalog(workspace) -> None:
    app = create_demo_app(workspace, enable_learning_console=True)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        page = await client.get("/lab")
        assert page.status_code == 200
        assert "Agent Runtime Learning Console" in page.text

        stylesheet = await client.get("/lab/static/styles.css")
        assert stylesheet.status_code == 200
        assert "--accent" in stylesheet.text
        assert ".swimlane-board" in stylesheet.text
        assert ".swimlane-link" in stylesheet.text
        assert ".empty-state[hidden]" in stylesheet.text
        assert "min-height: 164px" in stylesheet.text

        script = await client.get("/lab/static/app.js")
        assert script.status_code == 200
        assert "function eventLane" in script.text
        assert "function swimlanePoint" in script.text
        assert "swimlane-event" in script.text
        assert 'id="swimlaneBoard"' in page.text
        assert 'id="swimlaneViewport"' in page.text

        scenarios = await client.get("/lab/api/scenarios")
        assert scenarios.status_code == 200
        assert [item["id"] for item in scenarios.json()] == [
            "plain-text",
            "tool-calling",
            "token-streaming",
            "human-approval",
        ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scenario_id", "required_event", "result_fragment"),
    [
        ("plain-text", "model.completed", "Agent Runtime"),
        ("tool-calling", "tool.completed", "437"),
        ("token-streaming", "model.delta", "Runtime Event"),
    ],
)
async def test_learning_scenarios_run_through_real_runtime(
    workspace, scenario_id: str, required_event: str, result_fragment: str
) -> None:
    app = create_demo_app(workspace, enable_learning_console=True)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(f"/lab/api/scenarios/{scenario_id}/runs", json={"input": None})
        assert created.status_code == 202
        run_id = created.json()["id"]

        runtime = app.state.learning_console.runtime_for_run(run_id)
        completed = await runtime.wait(run_id)
        assert completed.status.value == "completed"

        snapshot = await client.get(f"/lab/api/runs/{run_id}/snapshot")
        assert snapshot.status_code == 200
        payload = snapshot.json()
        assert result_fragment in payload["run"]["result"]
        assert required_event in [event["type"] for event in payload["events"]]
        assert payload["events"][0]["teaching"]["code"]
        assert payload["events"][0]["state_before"] != payload["events"][0]["state_after"]
        assert payload["trace"]["trace_id"] == payload["run"]["metadata"]["trace_id"]
        assert payload["trace_tree"]["root_run_id"] == run_id
        assert payload["trace_tree"]["node_count"] == 1
        assert payload["persistence"]["tables"]["events"] == len(payload["events"])
        assert payload["acceptance"]["passed"] is True


@pytest.mark.asyncio
async def test_human_approval_scenario_pauses_and_resumes(workspace) -> None:
    app = create_demo_app(workspace, enable_learning_console=True)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post("/lab/api/scenarios/human-approval/runs", json={"input": None})
        run_id = created.json()["id"]
        runtime = app.state.learning_console.runtime_for_run(run_id)
        waiting = await runtime.wait(run_id)
        assert waiting.status.value == "waiting_for_approval"

        before = (await client.get(f"/lab/api/runs/{run_id}/snapshot")).json()
        assert before["pending_approval"] is not None
        assert before["acceptance"]["waiting_for_human"] is True
        assert before["tool_executions"][0]["status"] == "waiting_for_approval"

        approval_id = before["pending_approval"]["id"]
        resolved = await client.post(
            f"/lab/api/approvals/{approval_id}/resolve",
            json={"approved": True, "reason": "test approval"},
        )
        assert resolved.status_code == 200
        assert resolved.json()["run"]["status"] == "completed"

        after = (await client.get(f"/lab/api/runs/{run_id}/snapshot")).json()
        assert after["pending_approval"] is None
        assert after["approvals"][0]["status"] == "approved"
        assert after["tool_executions"][0]["status"] == "completed"
        assert after["acceptance"]["passed"] is True
        assert "approval.resolved" in [event["type"] for event in after["events"]]

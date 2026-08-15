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
        assert 'data-tab="context"' in page.text
        assert 'data-tab="memory"' in page.text
        assert 'data-tab="artifacts"' in page.text
        assert 'id="swimlaneLabels"' in page.text
        assert 'class="flow-legend"' in page.text

        stylesheet = await client.get("/lab/static/styles.css")
        assert stylesheet.status_code == 200
        assert "--accent" in stylesheet.text
        assert ".swimlane-board" in stylesheet.text
        assert ".swimlane-link" in stylesheet.text
        assert ".swimlane-link.delegation-flow" in stylesheet.text
        assert ".swimlane-link.aggregation-flow" in stylesheet.text
        assert "repeat(8, 76px)" not in stylesheet.text
        assert ".topology-node" in stylesheet.text
        assert ".empty-state[hidden]" in stylesheet.text

        script = await client.get("/lab/static/app.js")
        assert script.status_code == 200
        assert "function eventLane" in script.text
        assert "function buildSwimlanes" in script.text
        assert "function buildTimelineLinks" in script.text
        assert "function swimlanePoint" in script.text
        assert 'return `agent:${event.run_id}`' in script.text
        assert 'kind: "delegation-flow"' in script.text
        assert 'kind: "aggregation-flow"' in script.text
        assert "swimlane-event" in script.text
        assert '"delegation.created"' in script.text
        assert '"context.compacted"' in script.text
        assert "function renderMemoryInspector" in script.text
        assert 'id="swimlaneBoard"' in page.text
        assert 'id="swimlaneViewport"' in page.text
        assert 'class="swimlane-label agent"' not in page.text

        scenarios = await client.get("/lab/api/scenarios")
        assert scenarios.status_code == 200
        assert [item["id"] for item in scenarios.json()] == [
            "plain-text",
            "tool-calling",
            "token-streaming",
            "human-approval",
            "multi-agent-sequential",
            "multi-agent-parallel",
            "session-memory",
            "context-compaction",
            "large-tool-artifact",
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
        assert payload["reliability"]["runtime_accepting"] is True
        assert payload["reliability"]["sqlite"]["status"] == "ok"
        assert payload["reliability"]["run_health"] == "healthy"
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scenario_id", "workflow_type"),
    [
        ("multi-agent-sequential", "sequential"),
        ("multi-agent-parallel", "parallel"),
    ],
)
async def test_multi_agent_learning_scenarios_show_parent_child_topology(
    workspace, scenario_id: str, workflow_type: str
) -> None:
    app = create_demo_app(workspace, enable_learning_console=True)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(f"/lab/api/scenarios/{scenario_id}/runs", json={"input": None})
        run_id = created.json()["id"]
        runtime = app.state.learning_console.runtime_for_run(run_id)
        completed = await runtime.wait(run_id)
        assert completed.status.value == "completed"

        payload = (await client.get(f"/lab/api/runs/{run_id}/snapshot")).json()
        assert payload["run"]["metadata"]["workflow_type"] == workflow_type
        assert len(payload["runs"]) == 4
        assert len(payload["relations"]) == 3
        assert payload["trace_tree"]["node_count"] == 4
        assert payload["metrics"]["multi_agent"]["child_runs"] >= 3
        assert payload["acceptance"]["passed"] is True
        assert all("timeline_sequence" in event for event in payload["events"])
        assert any(event["run_role"] == "child" for event in payload["events"])
        assert "delegation.created" in [event["type"] for event in payload["events"]]
        assert payload["persistence"]["tables"]["run_relations"] == 3
        children = [run for run in payload["runs"] if run["run_role"] == "child"]
        order_key = "workflow_step" if workflow_type == "sequential" else "workflow_branch"
        ordered_children = sorted(children, key=lambda run: run["metadata"][order_key])
        expected_names = (
            ["Planner", "Worker", "Reviewer"]
            if workflow_type == "sequential"
            else ["Research", "Test", "Risk"]
        )
        assert [run["metadata"]["workflow_step_name"] for run in ordered_children] == expected_names
        child_run_ids = {run["id"] for run in children}
        assert child_run_ids == {
            event["run_id"] for event in payload["events"] if event["run_role"] == "child"
        }
        assert all(
            any(
                event["run_id"] == child_id and event["type"] == "run.created"
                for event in payload["events"]
            )
            for child_id in child_run_ids
        )


@pytest.mark.asyncio
async def test_session_memory_learning_scenario_exposes_retrieval_and_context(workspace) -> None:
    app = create_demo_app(workspace, enable_learning_console=True)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post("/lab/api/scenarios/session-memory/runs", json={"input": None})
        run_id = created.json()["id"]
        runtime = app.state.learning_console.runtime_for_run(run_id)
        completed = await runtime.wait(run_id)
        assert completed.status.value == "completed"

        payload = (await client.get(f"/lab/api/runs/{run_id}/snapshot")).json()
        event_types = [event["type"] for event in payload["events"]]
        assert payload["session"] is not None
        assert len(payload["session_runs"]) == 1
        assert len(payload["memories"]) == 2
        assert {item["scope"] for item in payload["memories"]} == {"session", "agent"}
        assert "session.run.attached" in event_types
        assert "memory.search.started" in event_types
        assert "memory.search.completed" in event_types
        assert "context.built" in event_types
        assert payload["context_builds"][0]["memory_ids"]
        assert "Mermaid" in payload["run"]["result"]
        assert payload["acceptance"]["passed"] is True


@pytest.mark.asyncio
async def test_context_compaction_learning_scenario_keeps_full_checkpoint(workspace) -> None:
    app = create_demo_app(workspace, enable_learning_console=True)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post("/lab/api/scenarios/context-compaction/runs", json={"input": None})
        run_id = created.json()["id"]
        runtime = app.state.learning_console.runtime_for_run(run_id)
        completed = await runtime.wait(run_id)
        assert completed.status.value == "completed"

        payload = (await client.get(f"/lab/api/runs/{run_id}/snapshot")).json()
        compacted = [item for item in payload["context_builds"] if item["event_type"] == "context.compacted"]
        assert compacted
        assert any(item["omitted_messages"] > 0 for item in compacted)
        assert len(payload["checkpoint"]["messages"]) > compacted[-1]["selected_messages"]
        assert payload["run"]["tool_call_count"] == 4
        assert payload["acceptance"]["passed"] is True


@pytest.mark.asyncio
async def test_large_tool_result_learning_scenario_writes_real_artifact(workspace) -> None:
    app = create_demo_app(workspace, enable_learning_console=True)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post("/lab/api/scenarios/large-tool-artifact/runs", json={"input": None})
        run_id = created.json()["id"]
        runtime = app.state.learning_console.runtime_for_run(run_id)
        completed = await runtime.wait(run_id)
        assert completed.status.value == "completed"

        payload = (await client.get(f"/lab/api/runs/{run_id}/snapshot")).json()
        assert "tool.result.artifactized" in [event["type"] for event in payload["events"]]
        assert len(payload["artifacts"]) == 1
        artifact = payload["artifacts"][0]
        assert artifact["exists"] is True
        assert artifact["characters"] > 256
        assert artifact["preview"]
        assert payload["tool_executions"][0]["result_data"]["_artifact"]["path"] == artifact["path"]
        assert payload["acceptance"]["passed"] is True

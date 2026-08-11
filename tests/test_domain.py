from __future__ import annotations

import pytest

from agent_runtime.domain import AgentRun, InvalidStateTransition, RunStatus


def test_run_state_machine_accepts_valid_transitions() -> None:
    run = AgentRun.create("test", "hello")
    run.transition_to(RunStatus.RUNNING)
    run.transition_to(RunStatus.PAUSED)
    run.transition_to(RunStatus.RUNNING)
    run.transition_to(RunStatus.COMPLETED)
    assert run.status is RunStatus.COMPLETED


def test_run_state_machine_rejects_invalid_transition() -> None:
    run = AgentRun.create("test", "hello")
    with pytest.raises(InvalidStateTransition):
        run.transition_to(RunStatus.COMPLETED)

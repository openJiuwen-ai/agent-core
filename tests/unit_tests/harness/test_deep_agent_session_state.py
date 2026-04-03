# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Session-state tests for DeepAgent runtime state methods."""
from __future__ import annotations

from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.schema.state import DeepAgentState
from openjiuwen.harness.schema.task import (
    TaskItem,
    TaskPlan,
)
from openjiuwen.core.single_agent.schema.agent_card import AgentCard


class FakeSession:
    def __init__(self) -> None:
        self._state = {}
        self._sid = "sess_test"

    def get_session_id(self) -> str:
        return self._sid

    def get_state(self, key=None):  # noqa: ANN001
        if key is None:
            return dict(self._state)
        return self._state.get(key)

    def update_state(self, data: dict) -> None:
        self._state.update(data)


def _make_agent() -> DeepAgent:
    return DeepAgent(AgentCard(name="test_state"))


def test_load_empty_state() -> None:
    """Loading from empty session yields defaults."""
    agent = _make_agent()
    session = FakeSession()
    state = agent.load_state(session)
    assert state.iteration == 0
    assert state.task_plan is None


def test_save_and_reload_state() -> None:
    """Round-trip: save → clear → load preserves data."""
    agent = _make_agent()
    session = FakeSession()
    plan = TaskPlan(
        goal="demo",
        tasks=[TaskItem(id="t1", title="first")],
    )
    state = DeepAgentState(iteration=3, task_plan=plan)
    agent.save_state(session, state)
    agent.clear_state(session)

    loaded = agent.load_state(session)
    assert loaded.iteration == 3
    assert loaded.task_plan is not None
    assert loaded.task_plan.goal == "demo"
    assert len(loaded.task_plan.tasks) == 1
    assert loaded.task_plan.tasks[0].id == "t1"


def test_pending_follow_ups_round_trip() -> None:
    """pending_follow_ups survives save -> clear -> load."""
    agent = _make_agent()
    session = FakeSession()
    state = DeepAgentState(
        iteration=1,
        pending_follow_ups=["msg1", "msg2", "msg3"],
    )
    agent.save_state(session, state)
    agent.clear_state(session)

    loaded = agent.load_state(session)
    assert loaded.pending_follow_ups == [
        "msg1",
        "msg2",
        "msg3",
    ]


def test_pending_follow_ups_defaults_empty() -> None:
    """Legacy state without pending_follow_ups loads as empty list."""
    agent = _make_agent()
    session = FakeSession()
    # Simulate old-format state (no pending_follow_ups key)
    session.update_state(
        {
            "deepagent": {
                "iteration": 2,
                "task_plan": None,
                "stop_condition_state": None,
            }
        }
    )
    loaded = agent.load_state(session)
    assert loaded.pending_follow_ups == []

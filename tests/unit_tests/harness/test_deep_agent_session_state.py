# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Session-state tests for DeepAgent runtime state methods."""

from __future__ import annotations

import pytest

from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.schema import state as state_schema
from openjiuwen.harness.schema.state import DeepAgentState
from openjiuwen.harness.schema.task import (
    TaskPlan,
    TodoItem,
    TodoStatus,
)


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
    """Round-trip: save -> clear -> load preserves data."""
    agent = _make_agent()
    session = FakeSession()
    plan = TaskPlan(
        goal="demo",
        tasks=[TodoItem(id="t1", title="first")],
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


def test_save_and_reload_state_with_todo_status() -> None:
    """Round-trip with TodoStatus works."""
    agent = _make_agent()
    session = FakeSession()
    plan = TaskPlan(
        goal="demo",
        tasks=[
            TodoItem(id="t1", title="first", status=TodoStatus.COMPLETED),
            TodoItem(id="t2", title="second", status=TodoStatus.PENDING),
        ],
    )
    state = DeepAgentState(iteration=3, task_plan=plan)
    agent.save_state(session, state)
    agent.clear_state(session)

    loaded = agent.load_state(session)
    assert loaded.task_plan is not None
    assert loaded.task_plan.tasks[0].status == TodoStatus.COMPLETED
    assert loaded.task_plan.tasks[1].status == TodoStatus.PENDING


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


def test_turn_state_round_trip() -> None:
    """Turn identity and next step survive session persistence."""
    agent = _make_agent()
    session = FakeSession()
    state = DeepAgentState(
        turn_state=state_schema.TurnState(
            turn_id="turn-1",
            step_index=3,
        ),
    )

    agent.save_state(session, state)
    agent.clear_state(session)

    loaded = agent.load_state(session)
    assert loaded.turn_state == state_schema.TurnState(
        turn_id="turn-1",
        step_index=3,
    )


def test_start_turn_overwrites_runtime_turn_state() -> None:
    """A new logical task replaces the previous turn in runtime; dict updates on exit save."""
    agent = _make_agent()
    session = FakeSession()
    agent.save_state(
        session,
        DeepAgentState(
            turn_state=state_schema.TurnState(
                turn_id="previous",
                step_index=4,
            ),
        ),
    )

    started = agent._start_turn(session)
    assert started.turn_id != "previous"
    assert started.step_index == 0
    assert agent.load_state(session).turn_state == started

    agent.save_state(session)
    agent.clear_state(session)

    loaded = agent.load_state(session)
    assert loaded.turn_state == started


@pytest.mark.parametrize("step_index", [0, 3])
def test_turn_state_accepts_non_negative_int_step_index(step_index: int) -> None:
    """A persisted non-negative int step index restores with the same turn."""
    restored = state_schema.TurnState.from_dict({"turn_id": "turn-1", "step_index": step_index})

    assert restored == state_schema.TurnState(turn_id="turn-1", step_index=step_index)


def test_turn_state_missing_step_index_defaults_to_zero() -> None:
    """Older snapshots without step_index resume at the first step."""
    restored = state_schema.TurnState.from_dict({"turn_id": "turn-1"})

    assert restored == state_schema.TurnState(turn_id="turn-1", step_index=0)


@pytest.mark.parametrize(
    "step_index",
    ["nope", "3", True, False, -1, 1.5, None],
)
def test_turn_state_rejects_unusable_step_index(step_index: object) -> None:
    """Illegal step indexes are dropped instead of raising or restoring a dead turn."""
    restored = state_schema.TurnState.from_dict({"turn_id": "turn-1", "step_index": step_index})

    assert restored is None


def test_session_snapshot_with_bad_step_index_drops_turn() -> None:
    """Session restore must not leak ValueError from a corrupt step index."""
    loaded = DeepAgentState.from_session_dict(
        {
            "iteration": 1,
            "turn_state": {"turn_id": "turn-1", "step_index": "-1"},
        }
    )

    assert loaded.iteration == 1
    assert loaded.turn_state is None

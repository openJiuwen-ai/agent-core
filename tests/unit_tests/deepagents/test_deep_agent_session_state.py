# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Session-state tests for DeepAgent runtime helpers."""
from __future__ import annotations

from openjiuwen.deepagents.schema.state import (
    clear_state,
    DeepAgentState,
    load_state,
    save_state,
)
from openjiuwen.deepagents.schema.task import (
    TaskItem,
    TaskPlan,
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


class FakeCtx:
    def __init__(self, session: FakeSession) -> None:
        self.session = session


def test_load_empty_state() -> None:
    """Loading from empty session yields defaults."""
    session = FakeSession()
    ctx = FakeCtx(session)
    state = load_state(ctx)
    assert state.iteration == 0
    assert state.task_plan is None


def test_save_and_reload_state() -> None:
    """Round-trip: save → clear → load preserves data."""
    session = FakeSession()
    ctx = FakeCtx(session)
    plan = TaskPlan(
        goal="demo",
        tasks=[TaskItem(id="t1", title="first")],
    )
    state = DeepAgentState(iteration=3, task_plan=plan)
    save_state(ctx, state)
    clear_state(ctx)

    loaded = load_state(ctx)
    assert loaded.iteration == 3
    assert loaded.task_plan is not None
    assert loaded.task_plan.goal == "demo"
    assert len(loaded.task_plan.tasks) == 1
    assert loaded.task_plan.tasks[0].id == "t1"

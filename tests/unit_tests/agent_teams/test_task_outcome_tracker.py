# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for the deferred review-pass notice (F_119 TaskOutcomeTracker)."""

from types import SimpleNamespace

import pytest

from openjiuwen.agent_teams.i18n import get_language, set_language
from openjiuwen.agent_teams.team_context import TASK_OUTCOME_STATE_KEY, TaskOutcomeTracker


class _Session:
    """Member session stand-in holding the state bucket."""

    def __init__(self) -> None:
        self.state: dict = {}
        self.commits = 0

    def get_state(self, key: str):
        return self.state.get(key)

    def update_state(self, data: dict) -> None:
        self.state.update(data)

    async def commit(self) -> None:
        self.commits += 1


def _task(task_id: str, *, reviewers: tuple[str, ...]) -> SimpleNamespace:
    return SimpleNamespace(task_id=task_id, title=f"title-{task_id}", reviewers=lambda: list(reviewers))


class _TaskManager:
    def __init__(self, completed: list) -> None:
        self.completed = completed
        self.queries: list[tuple[str, str | None]] = []

    async def get_tasks_by_assignee(self, member_name: str, status: str | None = None) -> list:
        self.queries.append((member_name, status))
        return list(self.completed)


def _tracker(completed: list) -> tuple[TaskOutcomeTracker, _TaskManager]:
    manager = _TaskManager(completed)
    backend = SimpleNamespace(task_manager=manager)
    return TaskOutcomeTracker(team_backend=backend, member_name="dev-1"), manager


@pytest.mark.asyncio
@pytest.mark.level0
async def test_only_reviewed_completions_are_announced():
    tracker, manager = _tracker([_task("a", reviewers=("v1",)), _task("b", reviewers=())])

    text = await tracker.pending_text(_Session())

    assert text is not None
    assert '<team-event kind="task-verified">' in text
    assert "- a · title-a" in text
    assert "title-b" not in text
    assert manager.queries == [("dev-1", "completed")]


@pytest.mark.asyncio
@pytest.mark.level0
async def test_commit_records_the_announcement_once():
    tracker, _manager = _tracker([_task("a", reviewers=("v1",))])
    session = _Session()

    assert await tracker.pending_text(session)
    await tracker.commit(session)

    assert session.state[TASK_OUTCOME_STATE_KEY] == ["a"]
    assert session.commits == 1
    assert await tracker.pending_text(session) is None


@pytest.mark.asyncio
@pytest.mark.level1
async def test_uncommitted_announcement_is_offered_again():
    """Delivery failed (no commit): the next input carries the notice again."""
    tracker, _manager = _tracker([_task("a", reviewers=("v1",))])
    session = _Session()

    assert await tracker.pending_text(session)
    assert await tracker.pending_text(session)


@pytest.mark.asyncio
@pytest.mark.level1
async def test_later_passes_are_announced_without_repeating_earlier_ones():
    completed = [_task("a", reviewers=("v1",))]
    tracker, _manager = _tracker(completed)
    session = _Session()
    await tracker.pending_text(session)
    await tracker.commit(session)

    completed.append(_task("b", reviewers=("v1",)))
    text = await tracker.pending_text(session)
    await tracker.commit(session)

    assert "- b · title-b" in text
    assert "title-a" not in text
    assert session.state[TASK_OUTCOME_STATE_KEY] == ["a", "b"]


@pytest.mark.asyncio
@pytest.mark.level1
async def test_commit_without_pending_text_is_a_no_op():
    tracker, _manager = _tracker([])
    session = _Session()

    assert await tracker.pending_text(session) is None
    await tracker.commit(session)

    assert session.commits == 0
    assert TASK_OUTCOME_STATE_KEY not in session.state


@pytest.mark.asyncio
@pytest.mark.level1
async def test_disabled_without_backend_session_or_member():
    assert await TaskOutcomeTracker(team_backend=None, member_name="dev-1").pending_text(_Session()) is None
    tracker, _manager = _tracker([_task("a", reviewers=("v1",))])
    assert await tracker.pending_text(None) is None
    backend = SimpleNamespace(task_manager=_TaskManager([_task("a", reviewers=("v1",))]))
    assert await TaskOutcomeTracker(team_backend=backend, member_name=None).pending_text(_Session()) is None


@pytest.mark.asyncio
@pytest.mark.level1
async def test_notice_follows_the_runtime_language():
    tracker, _manager = _tracker([_task("a", reviewers=("v1",))])
    previous = get_language()
    try:
        set_language("cn")
        assert "已通过验收" in await tracker.pending_text(_Session())
        set_language("en")
        assert "passed review" in await tracker.pending_text(_Session())
    finally:
        set_language(previous)

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


async def _text(tracker: TaskOutcomeTracker, session: "_Session | None" = None) -> str | None:
    notice = await tracker.claim(session if session is not None else _Session())
    return notice.text if notice is not None else None


@pytest.mark.asyncio
@pytest.mark.level0
async def test_only_reviewed_completions_are_announced():
    tracker, manager = _tracker([_task("a", reviewers=("v1",)), _task("b", reviewers=())])

    text = await _text(tracker)

    assert text is not None
    assert '<team-event kind="task-verified">' in text
    assert "- a · title-a" in text
    assert "title-b" not in text
    assert manager.queries == [("dev-1", "completed")]


@pytest.mark.asyncio
@pytest.mark.level0
async def test_a_claim_is_announced_once_and_persisted():
    tracker, _manager = _tracker([_task("a", reviewers=("v1",))])
    session = _Session()

    assert await _text(tracker, session)
    assert await _text(tracker, session) is None
    await tracker.persist(session)
    await tracker.persist(session)

    assert session.state[TASK_OUTCOME_STATE_KEY] == ["a"]
    assert session.commits == 1


@pytest.mark.asyncio
@pytest.mark.level1
async def test_a_released_claim_is_offered_again():
    """Delivery failed: the claim is given back and the next input carries the notice."""
    tracker, _manager = _tracker([_task("a", reviewers=("v1",))])
    session = _Session()

    notice = await tracker.claim(session)
    tracker.release(notice)

    assert await _text(tracker, session)


@pytest.mark.asyncio
@pytest.mark.level1
async def test_a_passed_review_never_goes_out_on_its_own():
    """F_119: a passed review must not wake its author, so announce sends and claims nothing."""
    tracker, _manager = _tracker([_task("a", reviewers=("v1",))])
    session = _Session()
    sent: list[str] = []

    async def _send(text: str) -> None:
        sent.append(text)

    assert await tracker.announce(session, _send) is False
    assert sent == []
    assert await _text(tracker, session)


@pytest.mark.asyncio
@pytest.mark.level1
async def test_later_passes_are_announced_without_repeating_earlier_ones():
    completed = [_task("a", reviewers=("v1",))]
    tracker, _manager = _tracker(completed)
    session = _Session()
    await tracker.claim(session)
    await tracker.persist(session)

    completed.append(_task("b", reviewers=("v1",)))
    text = await _text(tracker, session)
    await tracker.persist(session)

    assert "- b · title-b" in text
    assert "title-a" not in text
    assert session.state[TASK_OUTCOME_STATE_KEY] == ["a", "b"]


@pytest.mark.asyncio
@pytest.mark.level1
async def test_a_checkpointed_baseline_is_honoured_after_a_restart():
    session = _Session()
    session.state[TASK_OUTCOME_STATE_KEY] = ["a"]
    tracker, _manager = _tracker([_task("a", reviewers=("v1",))])

    assert await _text(tracker, session) is None
    await tracker.persist(session)

    assert session.commits == 0


@pytest.mark.asyncio
@pytest.mark.level1
async def test_persist_without_claims_is_a_no_op():
    tracker, _manager = _tracker([])
    session = _Session()

    assert await _text(tracker, session) is None
    await tracker.persist(session)
    await tracker.persist(None)

    assert session.commits == 0
    assert TASK_OUTCOME_STATE_KEY not in session.state


@pytest.mark.asyncio
@pytest.mark.level1
async def test_disabled_without_session_backend_or_member():
    tracker, _manager = _tracker([_task("a", reviewers=("v1",))])
    assert await tracker.claim(None) is None
    assert await _text(TaskOutcomeTracker(team_backend=None, member_name="dev-1")) is None
    backend = SimpleNamespace(task_manager=_TaskManager([_task("a", reviewers=("v1",))]))
    assert await _text(TaskOutcomeTracker(team_backend=backend, member_name=None)) is None


@pytest.mark.asyncio
@pytest.mark.level1
async def test_notice_follows_the_runtime_language():
    previous = get_language()
    try:
        set_language("cn")
        assert "已通过验收" in await _text(_tracker([_task("a", reviewers=("v1",))])[0])
        set_language("en")
        assert "passed review" in await _text(_tracker([_task("a", reviewers=("v1",))])[0])
    finally:
        set_language(previous)

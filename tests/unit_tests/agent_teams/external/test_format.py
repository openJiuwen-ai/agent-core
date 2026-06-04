# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for pure-function inbound rendering."""

from types import SimpleNamespace

import pytest

from openjiuwen.agent_teams.external.format import (
    render_message,
    render_messages,
    render_task_board,
    render_task_line,
)
from openjiuwen.agent_teams.i18n import get_language, set_language

# Fixed anchor so relative-time assertions stay deterministic.
_NOW_MS = 1_700_000_000_000
_THREE_MINUTES_MS = 3 * 60 * 1000
_TWELVE_MINUTES_MS = 12 * 60 * 1000


@pytest.fixture
def lang_en():
    """Pin the process language to English for stable assertions."""
    previous = get_language()
    set_language("en")
    yield
    set_language(previous)


def _message(
    message_id: str,
    sender: str,
    content: str,
    *,
    broadcast: bool,
    timestamp: int = _NOW_MS - _THREE_MINUTES_MS,
) -> SimpleNamespace:
    return SimpleNamespace(
        message_id=message_id,
        from_member_name=sender,
        content=content,
        broadcast=broadcast,
        timestamp=timestamp,
    )


def _task(
    task_id: str,
    status: str,
    assignee: str | None,
    *,
    updated_at: int | None = _NOW_MS - _TWELVE_MINUTES_MS,
) -> SimpleNamespace:
    return SimpleNamespace(
        task_id=task_id,
        title=f"title-{task_id}",
        content=f"content-{task_id}",
        status=status,
        assignee=assignee,
        updated_at=updated_at,
    )


@pytest.mark.level0
def test_render_message_direct(lang_en):
    out = render_message(_message("m1", "leader", "hello", broadcast=False), now_ms=_NOW_MS)
    assert "m1" in out
    assert "leader" in out
    assert "hello" in out
    assert "direct message" in out
    # Send time is rendered as absolute + relative diff.
    assert "3m ago" in out


@pytest.mark.level0
def test_render_message_broadcast(lang_en):
    out = render_message(_message("m2", "leader", "all hands", broadcast=True), now_ms=_NOW_MS)
    assert "broadcast" in out


@pytest.mark.level0
def test_render_messages_joins_batch(lang_en):
    out = render_messages(
        [
            _message("m1", "leader", "first", broadcast=False),
            _message("m2", "dev-2", "second", broadcast=False),
        ],
        now_ms=_NOW_MS,
    )
    assert "first" in out
    assert "second" in out


@pytest.mark.level0
def test_render_task_line_carries_time_and_assignee(lang_en):
    line = render_task_line(_task("t1", "claimed", "dev-1"), now_ms=_NOW_MS)
    assert "t1" in line
    assert "claimed" in line
    assert "→ dev-1" in line
    assert "12m ago" in line


@pytest.mark.level0
def test_render_task_board_filters_terminal_and_marks_assignment(lang_en):
    tasks = [
        _task("t1", "pending", None),
        _task("t2", "completed", "dev-1"),
        _task("t3", "claimed", "dev-1"),
        _task("t4", "cancelled", None),
    ]
    out = render_task_board(tasks, is_leader=False, now_ms=_NOW_MS)
    assert "t1" in out
    assert "t3" in out
    assert "t2" not in out
    assert "t4" not in out
    assert "→ dev-1" in out


@pytest.mark.level0
def test_render_task_board_empty_when_all_terminal(lang_en):
    tasks = [_task("t1", "completed", None), _task("t2", "cancelled", None)]
    assert render_task_board(tasks, is_leader=False, now_ms=_NOW_MS) == ""

# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for pure-function inbound rendering."""

import re
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
    out = render_message(_message("m1", "leader", "hello", broadcast=False))
    # Rendered as <team-inbound> XML, mirroring the in-process dispatcher.
    assert out.startswith("<team-inbound ")
    assert 'message_id="m1"' in out
    assert 'from="leader"' in out
    assert 'type="direct"' in out
    assert "hello" in out
    # A non-human member gets a reply-hint note.
    assert '<team-note kind="reply-hint">' in out
    # Send time is the absolute time only: the block stays in the history,
    # where a relative diff rendered now would go stale.
    assert re.search(r'time="\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}( [+-]\d{2}:\d{2})?"', out)
    assert "ago" not in out


@pytest.mark.level0
def test_render_message_broadcast(lang_en):
    out = render_message(_message("m2", "leader", "all hands", broadcast=True))
    assert 'type="broadcast"' in out


@pytest.mark.level0
def test_render_message_human_agent_is_controller_notification(lang_en):
    out = render_message(
        _message("m3", "leader", "please review", broadcast=False),
        is_human_agent=True,
    )
    # A human-agent avatar sees the message as a controller notification with
    # the load-bearing hitt-silence constraint, not a reply-hint.
    assert 'for="controller"' in out
    assert '<team-note kind="hitt-silence">' in out
    assert '<team-note kind="reply-hint">' not in out


@pytest.mark.level0
def test_render_messages_joins_batch(lang_en):
    out = render_messages(
        [
            _message("m1", "leader", "first", broadcast=False),
            _message("m2", "dev-2", "second", broadcast=False),
        ],
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
    out = render_task_board(tasks, is_leader=False, now_ms=_NOW_MS, member_name="dev-1")
    assert "t1" in out
    # Terminal tasks and in-flight work are not something to start now.
    assert "t2" not in out
    assert "t3" not in out
    assert "t4" not in out
    assert "(unassigned)" in out


@pytest.mark.level0
def test_render_task_board_wraps_in_team_event(lang_en):
    out = render_task_board([_task("t1", "pending", None)], is_leader=True, now_ms=_NOW_MS)
    # The board is a single <team-event kind="task-board"> block, matching the
    # in-process TaskBoardHandler.
    assert out.startswith('<team-event kind="task-board">')
    assert out.rstrip().endswith("</team-event>")
    assert "t1" in out


@pytest.mark.level0
def test_render_task_board_empty_when_all_terminal(lang_en):
    tasks = [_task("t1", "completed", None), _task("t2", "cancelled", None)]
    assert render_task_board(tasks, is_leader=False, now_ms=_NOW_MS) == ""


@pytest.mark.level0
def test_render_task_line_can_drop_the_body_and_name_blockers(lang_en):
    task = _task("t1", "blocked", "dev-1")

    line = render_task_line(task, now_ms=_NOW_MS, with_content=False, blocked_by=["t0", "tx"])

    assert task.content not in line
    assert line.startswith("- [t1] [blocked] ")
    assert "→ dev-1" in line
    assert line.endswith("[blocked by #t0, #tx]")


@pytest.mark.level0
def test_render_task_board_leaves_bodies_out_for_the_leader_only(lang_en):
    task = _task("t1", "pending", None)

    leader = render_task_board([task], is_leader=True, now_ms=_NOW_MS)
    teammate = render_task_board([task], is_leader=False, now_ms=_NOW_MS)

    assert task.content not in leader
    assert task.content in teammate


@pytest.mark.level0
def test_teammate_board_lists_only_tasks_it_can_start(lang_en):
    tasks = [
        _task("mine", "pending", "dev-1"),
        _task("theirs", "pending", "dev-2"),
        _task("waiting", "blocked", "dev-1"),
        _task("open", "pending", None),
    ]

    out = render_task_board(tasks, is_leader=False, now_ms=_NOW_MS, member_name="dev-1")

    assert "[mine]" in out
    assert "[open]" in out
    assert "[theirs]" not in out
    assert "[waiting]" not in out
    assert "view_task" not in out


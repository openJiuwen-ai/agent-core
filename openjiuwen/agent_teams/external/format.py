# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Pure-function rendering of inbound team context for external agents.

These helpers turn raw team models (messages, tasks) into the same
human-readable text an in-process member would receive through its
coordination handlers, reusing the shared ``i18n`` strings so an external
agent sees identical wording. No I/O, no LLM calls — fully deterministic
and unit-testable.
"""

from __future__ import annotations

from typing import Protocol

from openjiuwen.agent_teams.i18n import t
from openjiuwen.agent_teams.timefmt import format_time_context

# Task statuses that should not appear on the actionable task board.
_TERMINAL_TASK_STATUSES = frozenset({"completed", "cancelled"})


class _MessageLike(Protocol):
    """Structural view of a team message row used for rendering."""

    message_id: str
    from_member_name: str
    content: str
    broadcast: bool
    timestamp: int


class _TaskLike(Protocol):
    """Structural view of a team task row used for rendering."""

    task_id: str
    title: str
    content: str
    status: str
    assignee: str | None
    updated_at: int | None


def render_message(message: _MessageLike, *, now_ms: int) -> str:
    """Render one inbound message exactly like the in-process dispatcher.

    Args:
        message: A team message row (direct or broadcast).
        now_ms: Current millisecond UTC epoch, the relative-time anchor.

    Returns:
        Localised text mirroring ``dispatcher.msg_received``.
    """
    msg_type = t("dispatcher.msg_type_broadcast") if message.broadcast else t("dispatcher.msg_type_direct")
    return t(
        "dispatcher.msg_received",
        msg_type=msg_type,
        message_id=message.message_id,
        sender=message.from_member_name,
        content=message.content,
        time_info=format_time_context(message.timestamp, now_ms),
    )


def render_messages(messages: list[_MessageLike], *, now_ms: int) -> str:
    """Render a batch of inbound messages, newest-handling left to caller."""
    return "\n\n".join(render_message(m, now_ms=now_ms) for m in messages)


def render_task_line(task: _TaskLike, *, now_ms: int) -> str:
    """Render one task-board line with its last-transition time.

    Single source of truth for the task-board row format, shared by the
    in-process ``TaskBoardHandler`` and the external task-board renderer.

    Args:
        task: A team task row.
        now_ms: Current millisecond UTC epoch, the relative-time anchor.

    Returns:
        A line like ``- [t1] [claimed] Title: body → dev-1 (3 分钟前)``.
    """
    assignee = f" → {task.assignee}" if task.assignee else t("dispatcher.task_unassigned_marker")
    time_info = format_time_context(task.updated_at, now_ms)
    return f"- [{task.task_id}] [{task.status}] {task.title}: {task.content}{assignee} ({time_info})"


def render_task_board(tasks: list[_TaskLike], *, is_leader: bool, now_ms: int) -> str:
    """Render the actionable task board for an idle member.

    Mirrors ``TaskBoardHandler._nudge_idle_agent``: a role-specific header
    followed by one line per non-terminal task.

    Args:
        tasks: All team tasks; terminal ones are filtered out here.
        is_leader: Whether the viewer is the leader (changes the header).
        now_ms: Current millisecond UTC epoch, the relative-time anchor.

    Returns:
        Localised task-board text, or an empty string when nothing is
        actionable.
    """
    incomplete = [task for task in tasks if task.status not in _TERMINAL_TASK_STATUSES]
    if not incomplete:
        return ""

    header = t("dispatcher.leader_task_board") if is_leader else t("dispatcher.teammate_task_list")
    lines = [header]
    lines.extend(render_task_line(task, now_ms=now_ms) for task in incomplete)
    return "\n".join(lines)


__all__ = [
    "render_message",
    "render_messages",
    "render_task_board",
    "render_task_line",
]

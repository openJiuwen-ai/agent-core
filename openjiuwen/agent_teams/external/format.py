# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Pure-function rendering of inbound team context for external agents.

These helpers turn raw team models (messages, tasks) into the same
``<team-inbound>`` / ``<team-event>`` XML an in-process member receives
through its coordination handlers, reusing the shared ``inbound_render``
structure and the ``i18n`` note wording so an external agent reads an
identical shape. No I/O, no LLM calls — fully deterministic and
unit-testable.
"""

from __future__ import annotations

from typing import Protocol

from openjiuwen.agent_teams.i18n import t
from openjiuwen.agent_teams.inbound_render import (
    INBOUND_TYPE_BROADCAST,
    INBOUND_TYPE_DIRECT,
    render_event,
    render_inbound,
)
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


def render_message(
    message: _MessageLike,
    *,
    is_human_agent: bool = False,
    now_ms: int,
    body: str | None = None,
) -> str:
    """Render one inbound message as ``<team-inbound>`` XML.

    Mirrors the in-process ``MessageHandler._format_message``: the message body
    goes verbatim inside ``<team-inbound>`` (sender / message_id / type / time
    as attributes) and the framework hint goes in a separate ``<team-note>``. A
    human-agent avatar gets ``for="controller"`` plus a ``hitt-silence`` note;
    any other member gets a ``reply-hint`` note.

    Args:
        message: A team message row (direct or broadcast).
        is_human_agent: Whether the recipient is a human-agent avatar.
        now_ms: Current millisecond UTC epoch, the relative-time anchor.
        body: Delivery-time body of a framework template message, rendered by
            the caller (which owns the DB handle this pure module does not —
            see ``message_template.expand_message``). Passing it also drops the
            reply hint: a framework instruction is answered with a tool call,
            not a reply. ``None`` means an ordinary message — render its own
            content and keep the hint.

    Returns:
        The ``<team-inbound>`` XML, optionally followed by a ``<team-note>``.
    """
    msg_type = INBOUND_TYPE_BROADCAST if message.broadcast else INBOUND_TYPE_DIRECT
    time_info = format_time_context(message.timestamp, now_ms)
    if is_human_agent:
        return render_inbound(
            content=body if body is not None else message.content,
            sender=message.from_member_name,
            message_id=message.message_id,
            msg_type=msg_type,
            time_info=time_info,
            for_controller=True,
            note_kind="hitt-silence",
            note_text=t("hitt.silence_note"),
        )
    if body is not None:
        return render_inbound(
            content=body,
            sender=message.from_member_name,
            message_id=message.message_id,
            msg_type=msg_type,
            time_info=time_info,
        )
    return render_inbound(
        content=message.content,
        sender=message.from_member_name,
        message_id=message.message_id,
        msg_type=msg_type,
        time_info=time_info,
        note_kind="reply-hint",
        note_text=t("dispatcher.reply_hint", sender=message.from_member_name),
    )


def render_messages(
    messages: list[_MessageLike],
    *,
    is_human_agent: bool = False,
    now_ms: int,
    bodies: dict[str, str] | None = None,
) -> str:
    """Render a batch of inbound messages, newest-handling left to caller.

    Args:
        bodies: Delivery-time bodies of the framework template messages in the
            batch, keyed by ``message_id``. Ordinary messages are absent from
            the mapping and render their own content.
    """
    bodies = bodies or {}
    return "\n\n".join(
        render_message(m, is_human_agent=is_human_agent, now_ms=now_ms, body=bodies.get(m.message_id))
        for m in messages
    )


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
    """Render the actionable task board as a ``<team-event kind="task-board">``.

    Mirrors ``TaskBoardHandler``'s ``render_event(kind="task-board", ...)``: a
    role-specific header followed by one line per non-terminal task, wrapped in
    a single ``<team-event>`` block.

    Args:
        tasks: All team tasks; terminal ones are filtered out here.
        is_leader: Whether the viewer is the leader (changes the header).
        now_ms: Current millisecond UTC epoch, the relative-time anchor.

    Returns:
        The ``<team-event kind="task-board">`` XML, or an empty string when
        nothing is actionable.
    """
    incomplete = [task for task in tasks if task.status not in _TERMINAL_TASK_STATUSES]
    if not incomplete:
        return ""

    header = t("dispatcher.leader_task_board") if is_leader else t("dispatcher.teammate_task_list")
    lines = [header]
    lines.extend(render_task_line(task, now_ms=now_ms) for task in incomplete)
    return render_event(kind="task-board", body="\n".join(lines))


__all__ = [
    "render_message",
    "render_messages",
    "render_task_board",
    "render_task_line",
]

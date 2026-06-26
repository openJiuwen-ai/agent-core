# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""XML rendering for inbound team messages and framework events.

A team member's harness input mixes two very different things: the
*original message* another member or the user sent, and the *framework
metadata / instructions* the runtime adds around it (message id, time,
reply hints, event notices). The legacy ``i18n`` templates glue both into
one flat string, so the LLM cannot tell "who said it" from "what the
framework added".

This module renders that input as semantic XML instead, giving the LLM a
clean boundary:

- ``<team-inbound>`` wraps the **original message** verbatim, with the
  sender / id / type / time as attributes.
- ``<team-event>`` wraps a **framework event** (task assignment, plan
  decision, nudge, completion notice, task board, ...), with the event
  type in the ``kind`` attribute.
- ``<team-note>`` carries a framework-added hint or constraint attached to
  either of the above (e.g. a reply hint, or the HITT silence constraint).
- A ``for="controller"`` attribute marks content surfaced to a human
  agent's controller (HITT), which the avatar must stay silent about.

These are pure structural functions: callers pass the dynamic data plus
already-localized text fragments (resolved via ``i18n.t``) and get back an
XML string. Keeping the wording out of this layer means the bilingual
contract stays in ``i18n`` (one source of truth) while the tag structure
stays here. The ``type`` / ``kind`` / ``for`` attribute values are stable
English contract tokens — never localized — because the inbound-tags
system-prompt section documents them by name.
"""

from __future__ import annotations

import html

# Stable contract tokens for the <team-inbound> ``type`` attribute.
INBOUND_TYPE_DIRECT = "direct"
INBOUND_TYPE_BROADCAST = "broadcast"


def _esc_text(text: str | None) -> str:
    """Escape text for an XML element body (leaves quotes intact)."""
    return html.escape(text or "", quote=False)


def _esc_attr(value: object) -> str:
    """Escape a value for an XML attribute (escapes quotes)."""
    return html.escape("" if value is None else str(value), quote=True)


def _render_note(note_kind: str | None, note_text: str | None) -> str:
    """Render an optional ``<team-note>`` block, or ``""`` when absent."""
    if not note_kind or not note_text:
        return ""
    return f'<team-note kind="{_esc_attr(note_kind)}">\n{_esc_text(note_text)}\n</team-note>'


def render_inbound(
    *,
    content: str,
    sender: str,
    message_id: str,
    msg_type: str,
    time_info: str,
    for_controller: bool = False,
    note_kind: str | None = None,
    note_text: str | None = None,
) -> str:
    """Render one inbound member/user message as ``<team-inbound>`` XML.

    Args:
        content: The sender's original message body, rendered verbatim
            inside the element (XML-escaped, never paraphrased).
        sender: The sending member's ``member_name``.
        message_id: The message id, so the LLM can reference / mark it.
        msg_type: A stable contract token — ``INBOUND_TYPE_DIRECT`` or
            ``INBOUND_TYPE_BROADCAST`` — not a localized label.
        time_info: Human-readable send time (already rendered by
            ``timefmt.format_time_context``).
        for_controller: When True, add ``for="controller"`` so a HITT
            avatar treats this as a notification for its human controller.
        note_kind: Optional ``<team-note>`` kind (e.g. ``"reply-hint"``,
            ``"hitt-silence"``).
        note_text: Optional ``<team-note>`` body; rendered only when both
            ``note_kind`` and ``note_text`` are set.

    Returns:
        The rendered XML string (a ``<team-inbound>`` block, optionally
        followed by a ``<team-note>`` block).
    """
    attrs = [
        f'from="{_esc_attr(sender)}"',
        f'message_id="{_esc_attr(message_id)}"',
        f'type="{_esc_attr(msg_type)}"',
        f'time="{_esc_attr(time_info)}"',
    ]
    if for_controller:
        attrs.append('for="controller"')
    block = f"<team-inbound {' '.join(attrs)}>\n{_esc_text(content)}\n</team-inbound>"
    note = _render_note(note_kind, note_text)
    return f"{block}\n{note}" if note else block


def render_event(
    *,
    kind: str,
    body: str,
    task_id: str | None = None,
    for_controller: bool = False,
    note_kind: str | None = None,
    note_text: str | None = None,
) -> str:
    """Render one framework event as a ``<team-event>`` XML block.

    Used for task assignments, plan decisions, nudges, completion
    notices, the task board, etc. The ``body`` is the framework's own
    instruction text (resolved via ``i18n.t`` by the caller) — there is no
    "original message" to separate out, so the whole body sits inside the
    one element.

    Args:
        kind: A stable contract token for the event type (e.g.
            ``"task-assigned"``, ``"plan-approved"``, ``"all-done"``,
            ``"task-board"``, ``"stale-claim"``).
        body: The framework instruction text, rendered verbatim (escaped).
        task_id: Optional task id, added as a ``task_id`` attribute.
        for_controller: When True, add ``for="controller"`` (HITT).
        note_kind: Optional ``<team-note>`` kind appended after the event.
        note_text: Optional ``<team-note>`` body; rendered only when both
            ``note_kind`` and ``note_text`` are set.

    Returns:
        The rendered XML string (a ``<team-event>`` block, optionally
        followed by a ``<team-note>`` block).
    """
    attrs = [f'kind="{_esc_attr(kind)}"']
    if task_id is not None:
        attrs.append(f'task_id="{_esc_attr(task_id)}"')
    if for_controller:
        attrs.append('for="controller"')
    block = f"<team-event {' '.join(attrs)}>\n{_esc_text(body)}\n</team-event>"
    note = _render_note(note_kind, note_text)
    return f"{block}\n{note}" if note else block


__all__ = [
    "INBOUND_TYPE_BROADCAST",
    "INBOUND_TYPE_DIRECT",
    "render_event",
    "render_inbound",
]

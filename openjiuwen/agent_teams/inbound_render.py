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
  decision, nudge, completion notice, task board, roster change, ...),
  with the event type in the ``kind`` attribute.
- ``<team-context>`` wraps **standing team state** the member is being
  told about for the first time (its own identity, the team metadata) —
  a fact about the team rather than something that happened.
- ``<team-note>`` carries a framework-added hint or constraint attached to
  either of the above (e.g. a reply hint, or the HITT silence constraint).
  It is rendered **nested inside the block it annotates**, as the last
  child: which message or event a note is about is then a fact about the
  tree, not something the LLM has to infer from "the note came right
  after". A sibling note sitting between two blocks is exactly as close to
  the one before it as to the one after it.
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

# Event kinds whose every occurrence is a *complete* snapshot of one piece of
# state, so the newest occurrence alone says everything the earlier ones said.
# Only kinds with that property belong here. A delta (``roster-change``) or an
# event scoped to one subject (``stale-claim``, which carries a ``task_id``)
# loses information the moment an earlier occurrence is dropped.
SNAPSHOT_EVENT_KINDS = frozenset({"task-board"})

# Opening tag of a <team-event> of a given kind. ``render_event`` puts ``kind``
# first, so this identifies a rendered block from its very first characters.
_OPEN_TAG = '<team-event kind="%s"'


def _esc_text(text: str | None) -> str:
    """Escape text for an XML element body (leaves quotes intact)."""
    return html.escape(text or "", quote=False)


def _esc_attr(value: object) -> str:
    """Escape a value for an XML attribute (escapes quotes)."""
    return html.escape("" if value is None else str(value), quote=True)


def _render_note(note_kind: str | None, note_text: str | None) -> str:
    """Render the nested ``<team-note>`` child, or ``""`` when absent.

    Carries its own trailing newline so the enclosing block needs no branch
    on whether a note is there.
    """
    if not note_kind or not note_text:
        return ""
    return f'<team-note kind="{_esc_attr(note_kind)}">\n{_esc_text(note_text)}\n</team-note>\n'


def _render_block(tag: str, attrs: list[str], body: str, note: str = "") -> str:
    """Render one block element around its body and optional nested note.

    Args:
        tag: The element name (``team-inbound`` / ``team-event`` / ...).
        attrs: Already-escaped ``name="value"`` fragments, in contract order.
        body: The element body, escaped here.
        note: A rendered ``<team-note>`` child (already escaped), or ``""``.

    Returns:
        The rendered element.
    """
    open_tag = " ".join([tag, *attrs])
    return f"<{open_tag}>\n{_esc_text(body)}\n{note}</{tag}>"


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
        The rendered ``<team-inbound>`` block, with the ``<team-note>``
        nested inside it as its last child when there is one.
    """
    attrs = [
        f'from="{_esc_attr(sender)}"',
        f'message_id="{_esc_attr(message_id)}"',
        f'type="{_esc_attr(msg_type)}"',
        f'time="{_esc_attr(time_info)}"',
    ]
    if for_controller:
        attrs.append('for="controller"')
    return _render_block("team-inbound", attrs, content, _render_note(note_kind, note_text))


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
        note_kind: Optional ``<team-note>`` kind nested inside the event.
        note_text: Optional ``<team-note>`` body; rendered only when both
            ``note_kind`` and ``note_text`` are set.

    Returns:
        The rendered ``<team-event>`` block, with the ``<team-note>`` nested
        inside it as its last child when there is one.
    """
    attrs = [f'kind="{_esc_attr(kind)}"']
    if task_id is not None:
        attrs.append(f'task_id="{_esc_attr(task_id)}"')
    if for_controller:
        attrs.append('for="controller"')
    return _render_block("team-event", attrs, body, _render_note(note_kind, note_text))


def render_team_context(*, body: str) -> str:
    """Render standing team state as a ``<team-context>`` XML block.

    Used for the member's own identity and the team metadata: facts about the
    team rather than events. Unlike the per-round prompt attachment this
    replaces, the block is written into the conversation once, at the moment the
    state first exists, and stays there — so it is ordinary history, not a
    snapshot that is refreshed or withdrawn.

    Args:
        body: The rendered state text (escaped into the element body).

    Returns:
        The rendered ``<team-context>`` block.
    """
    return _render_block("team-context", [], body)


def snapshot_kind_of(text: str) -> str | None:
    """Return the snapshot kind ``text`` is, or None when it is not one.

    An input qualifies only when a snapshot event is *all* it is: these are
    delivered one per ``deliver_input`` call, so the whole entry is the block
    and dropping it takes nothing else with it. A ``<team-note>`` is not
    "something else" — it is nested inside the block and annotates that block
    alone, so it goes stale with the snapshot it hangs on and is superseded
    together with it.

    Args:
        text: One queued input, as it was handed to ``deliver_input``.

    Returns:
        The matching kind from :data:`SNAPSHOT_EVENT_KINDS`, or None.
    """
    stripped = text.strip()
    if not stripped.endswith("</team-event>"):
        return None
    return next((kind for kind in SNAPSHOT_EVENT_KINDS if stripped.startswith(_OPEN_TAG % kind)), None)


def drop_superseded_snapshots(parts: list[str]) -> list[str]:
    """Return ``parts`` without the snapshot inputs a later one supersedes.

    A member that was busy comes back to everything that queued up meanwhile.
    The task board can be in there several times, each a full survey taken
    seconds apart — but only the last describes the board the member is about
    to act on, and the earlier ones are noise one step away from becoming
    permanent history.

    Entries are dropped whole, never edited: each is one input, and only the
    kinds in :data:`SNAPSHOT_EVENT_KINDS` are ever dropped. Everything else,
    including a roster delta or a per-task nudge, carries something no other
    entry repeats.

    Args:
        parts: The queued inputs, oldest first.

    Returns:
        A new list with the superseded snapshot entries removed.
    """
    newest: dict[str, int] = {}
    for index, part in enumerate(parts):
        kind = snapshot_kind_of(part)
        if kind is not None:
            newest[kind] = index
    # A non-snapshot entry has no kind, so ``newest.get(None, index)`` gives
    # back its own index and it survives without a branch of its own.
    return [part for index, part in enumerate(parts) if newest.get(snapshot_kind_of(part), index) == index]


__all__ = [
    "INBOUND_TYPE_BROADCAST",
    "INBOUND_TYPE_DIRECT",
    "SNAPSHOT_EVENT_KINDS",
    "drop_superseded_snapshots",
    "render_event",
    "render_inbound",
    "render_team_context",
    "snapshot_kind_of",
]

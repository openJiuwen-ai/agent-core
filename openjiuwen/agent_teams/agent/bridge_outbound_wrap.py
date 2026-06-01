# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Wrap inbound team mailbox messages for forwarding to a remote bridge agent.

Used by the coordination message handler when an inbound message
targets a bridge member: the framework calls
:func:`wrap_outbound_to_remote` to format the body according to the
bridge's ``mailbox_inject_mode`` before invoking
``BridgeProtocolAdapter.relay``.

Pure function — no I/O, no LLM call — so the format is exactly
predictable and unit-testable. Adapters never re-format; what we
emit here is what the remote agent sees.
"""

from __future__ import annotations

from typing import Optional

from openjiuwen.agent_teams.schema.team import (
    BridgeMailboxInjectMode,
    TeamRole,
)

__all__ = ["wrap_outbound_to_remote"]


def wrap_outbound_to_remote(
    *,
    sender: str,
    sender_display_name: Optional[str],
    sender_role: Optional[TeamRole],
    sender_persona: Optional[str],
    body: str,
    broadcast: bool,
    task_hint: Optional[str],
    mode: BridgeMailboxInjectMode,
    language: str = "cn",
) -> str:
    """Build the text payload relayed to the remote agent.

    Both modes prepend a sender header so the remote knows where the
    text came from. REPHRASE additionally inlines role + persona and
    appends an optional task hint, suitable for stateless wrapping
    CLIs that need full context per turn.

    Args:
        sender: Internal ``member_name`` of the team member that sent
            the message (or ``"user"`` for a user-originated turn).
        sender_display_name: Human-readable label of the sender; falls
            back to ``sender`` when None.
        sender_role: ``TeamRole`` of the sender. Only used in REPHRASE.
        sender_persona: Short persona of the sender. Only used in
            REPHRASE.
        body: The team-side message text.
        broadcast: True if the message arrived via team-wide broadcast.
        task_hint: Optional task context (e.g. ``"task #42: Refactor
            metrics"``). Only used in REPHRASE.
        mode: ``PASSTHROUGH`` for a minimal header, ``REPHRASE`` for
            full sender context.
        language: ``"cn"`` (default) or ``"en"``. Affects label text
            only; identifiers (member names, role values) stay
            untranslated.

    Returns:
        Formatted text suitable for ``BridgeProtocolAdapter.relay``.
    """
    display = sender_display_name or sender
    if mode == BridgeMailboxInjectMode.PASSTHROUGH:
        return _wrap_passthrough(sender_label=display, body=body, broadcast=broadcast, language=language)
    return _wrap_rephrase(
        sender_label=display,
        sender_role=sender_role,
        sender_persona=sender_persona,
        body=body,
        broadcast=broadcast,
        task_hint=task_hint,
        language=language,
    )


def _wrap_passthrough(
    *,
    sender_label: str,
    body: str,
    broadcast: bool,
    language: str,
) -> str:
    """PASSTHROUGH: minimal sender header + body."""
    if language == "en":
        suffix = " (broadcast)" if broadcast else ""
        return f"[from {sender_label}{suffix}] {body}"
    suffix = "（广播）" if broadcast else ""
    return f"[来自 {sender_label}{suffix}] {body}"


def _wrap_rephrase(
    *,
    sender_label: str,
    sender_role: Optional[TeamRole],
    sender_persona: Optional[str],
    body: str,
    broadcast: bool,
    task_hint: Optional[str],
    language: str,
) -> str:
    """REPHRASE: full sender context + body + optional task hint."""
    role_value = sender_role.value if sender_role is not None else "unknown"
    persona = sender_persona or ""
    if language == "en":
        kind = "broadcast" if broadcast else "direct"
        header = f"[from {sender_label} (role={role_value}, persona={persona!r}, kind={kind})]"
        suffix = f"\nRe: {task_hint}" if task_hint else ""
        return f"{header}\n{body}{suffix}"
    kind = "广播" if broadcast else "点对点"
    header = f"[来自 {sender_label}（角色={role_value}，人设={persona!r}，类型={kind}）]"
    suffix = f"\n相关任务：{task_hint}" if task_hint else ""
    return f"{header}\n{body}{suffix}"

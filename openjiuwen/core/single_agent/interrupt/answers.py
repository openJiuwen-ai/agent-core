# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Answer shapes per interrupt kind, for hosts.

Each kind of interrupt expects its own answer shape:

======================== ==========================================================
kind                     answer
======================== ==========================================================
permission, confirm,     ``{"approved": bool, "feedback": str, "auto_confirm": bool}``
team_tool_approval,      (``auto_confirm`` + ``"persist_allow": False`` = remember
undeclared (``""``)      for this session)
skill_evolution_approval ``{"action": "allow_once" | "allow_always" | "reject",
                         "feedback": str}``
ask_user                 ``{"answers": {question: answer}}`` or the answer text
======================== ==========================================================

An undeclared kind may be answered in the ``approved`` shape, which every
interrupt took before kinds existed, and these helpers treat it as an
approval. Hosts keep their own earlier handling of it: the CLI asks
allow / always / reject, the native harness passes the host's answer through
unchanged.

:func:`interrupt_answer` builds the approval shapes from a decision or from
the word a person typed:

============ ==============================================================
approve once ``y``, ``yes``, ``ok``, ``approve``, ``true``, ``1``, ``allow``,
             ``allow_once``
always       ``a``, ``always``, ``allow_always`` (approve for this session)
reject       ``n``, ``no``, ``deny``, ``reject``
============ ==============================================================

Words are matched case-insensitively. Any other text, empty text included,
rejects with that text as feedback.
"""

from __future__ import annotations

from typing import Any, Optional, Tuple

from openjiuwen.core.single_agent.interrupt.response import InterruptKind

#: Kinds answered in the ``{"approved", "feedback", "auto_confirm"}`` shape (an undeclared
#: kind may be: the interrupt handler honours its ``auto_confirm``).
APPROVED_ANSWER_KINDS = frozenset({
    InterruptKind.PERMISSION,
    InterruptKind.CONFIRM,
    InterruptKind.TEAM_TOOL_APPROVAL,
    InterruptKind.UNDECLARED,
})

#: Kinds these helpers answer as an approve / reject decision (an undeclared kind included).
APPROVAL_INTERRUPT_KINDS = APPROVED_ANSWER_KINDS | {InterruptKind.SKILL_EVOLUTION_APPROVAL}

#: Choices a text-only host can offer for an approval.
APPROVAL_CHOICES = ("allow", "allow_always", "deny")

_ALLOW_WORDS = frozenset({"y", "yes", "ok", "approve", "true", "1", "allow", "allow_once"})
_ALWAYS_WORDS = frozenset({"a", "always", "allow_always"})
_DENY_WORDS = frozenset({"n", "no", "deny", "reject"})


def is_approval_kind(kind: str) -> bool:
    """Whether :func:`interrupt_answer` answers ``kind`` as an approval (an undeclared kind does)."""
    return kind in APPROVAL_INTERRUPT_KINDS


def _parse_approval_text(text: str) -> Tuple[bool, bool, str]:
    """Read ``(approved, remember, feedback)`` from what a person typed (words in the module docstring)."""
    word = text.strip()
    lowered = word.lower()
    if lowered in _ALWAYS_WORDS:
        return True, True, ""
    if lowered in _ALLOW_WORDS:
        return True, False, ""
    if lowered in _DENY_WORDS:
        return False, False, ""
    return False, False, word


def interrupt_answer(
    kind: str,
    *,
    approved: Optional[bool] = None,
    remember: bool = False,
    feedback: str = "",
    text: Optional[str] = None,
) -> Any:
    """Build the answer for an interrupt of ``kind``.

    For an approval kind, pass a decision (``approved``, optionally
    ``remember`` and ``feedback``) or the word a person typed (``text``).
    For any other kind, ``text`` is the answer as typed.

    Args:
        kind: ``InterruptRequest.interrupt_kind`` of the pending interrupt.
        approved: Approve (True) or reject (False) an approval kind.
        remember: Also approve matching calls later (session-scoped).
        feedback: Reason given with a rejection.
        text: What a person typed.

    Raises:
        ValueError: ``approved`` for a kind that is not an approval, or
            neither ``approved`` nor ``text``.
    """
    if approved is not None and not is_approval_kind(kind):
        raise ValueError(f"interrupts of kind {kind!r} are not approvals; answer them with text")
    if approved is None and text is None:
        raise ValueError("pass approved= or text=")
    if not is_approval_kind(kind):
        return text
    if approved is None:
        approved, remember, parsed_feedback = _parse_approval_text(text)
        feedback = feedback or parsed_feedback
    if kind == InterruptKind.SKILL_EVOLUTION_APPROVAL:
        if approved:
            return {"action": "allow_always" if remember else "allow_once"}
        return {"action": "reject", "feedback": feedback}
    answer: dict = {
        "approved": approved,
        "feedback": "" if approved else feedback,
        "auto_confirm": bool(approved and remember),
    }
    if approved and remember:
        answer["persist_allow"] = False
    return answer


__all__ = [
    "APPROVAL_CHOICES",
    "APPROVED_ANSWER_KINDS",
    "APPROVAL_INTERRUPT_KINDS",
    "interrupt_answer",
    "is_approval_kind",
]

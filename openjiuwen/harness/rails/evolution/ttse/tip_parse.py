# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Deterministic TIP shape parsing and quality gates for Auto-dream purge."""

from __future__ import annotations

import re
from typing import FrozenSet, Optional, Set, Tuple

# When <condition>: use <capability> to <action>
_TIP_RE = re.compile(
    r"^\s*when\s+(.+?)\s*:\s*use\s+(.+?)\s+to\s+(.+?)\s*$",
    re.IGNORECASE | re.DOTALL,
)

_GENERIC_CONDITIONS: FrozenSet[str] = frozenset(
    {
        "any task",
        "in general",
        "always",
        "when needed",
        "when appropriate",
        "by default",
        "everything",
        "for all tasks",
        "any time",
        "whenever",
        "in all cases",
    }
)

_GENERIC_ACTIONS: FrozenSet[str] = frozenset(
    {
        "handle it",
        "be careful",
        "do the task",
        "process",
        "check",
        "verify",
        "do it",
        "proceed",
        "continue",
        "fix it",
        "solve it",
    }
)

# Lightweight anchors that keep a condition from being "too generic".
_CONDITION_ANCHOR_RE = re.compile(
    r"(\.\w{1,8}\b|"  # file extension
    r"\b(csv|json|log|xml|yaml|yml|tsv|parquet|grader|benchmark)\b|"
    r"[\\/][\w.\-]+|"  # path fragment
    r"`[^`]+`|"
    r"\b(skill|tool)\b)",
    re.IGNORECASE,
)


def strip_pinned_prefix(text: str) -> Tuple[bool, str]:
    """Return (is_pinned, body) for optional ``[PINNED]`` prefix."""
    s = (text or "").strip()
    if s.upper().startswith("[PINNED]"):
        return True, s[8:].strip()
    return False, s


def parse_tip(text: str) -> Optional[Tuple[str, str, str]]:
    """Parse ``When <cond>: use <cap> to <action>`` -> (condition, capability, action).

    Returns ``None`` when the text does not match the required shape.
    """
    _, body = strip_pinned_prefix(text)
    match = _TIP_RE.match(body)
    if not match:
        return None
    condition = " ".join(match.group(1).split()).strip()
    capability = " ".join(match.group(2).split()).strip().strip("`\"'")
    action = " ".join(match.group(3).split()).strip()
    if not condition or not capability or not action:
        return None
    return condition, capability, action


def _condition_too_generic(condition: str) -> bool:
    lowered = condition.lower().strip()
    if lowered in _GENERIC_CONDITIONS:
        return True
    for phrase in _GENERIC_CONDITIONS:
        if lowered == phrase or lowered.startswith(phrase + " ") or lowered.endswith(" " + phrase):
            if not _CONDITION_ANCHOR_RE.search(condition):
                tokens = [t for t in re.split(r"\W+", lowered) if t]
                if len(tokens) < 4:
                    return True
    if lowered in {"needed", "appropriate", "required"} and not _CONDITION_ANCHOR_RE.search(condition):
        return True
    tokens = [t for t in re.split(r"\W+", lowered) if t]
    if len(tokens) <= 2 and not _CONDITION_ANCHOR_RE.search(condition):
        if any(t in {"always", "everything", "anything", "generally"} for t in tokens):
            return True
    return False


def _action_too_generic(action: str) -> bool:
    lowered = action.lower().strip().rstrip(".")
    if lowered in _GENERIC_ACTIONS:
        return True
    # Single vague verb with no object.
    tokens = [t for t in re.split(r"\W+", lowered) if t]
    return len(tokens) == 1 and tokens[0] in {"process", "check", "verify", "handle", "fix"}


def tip_purge_reason(text: str, capability_names: Set[str]) -> Optional[str]:
    """Return a retire reason code, or ``None`` if the TIP should be kept.

    Reason codes: ``tip_malformed``, ``tip_unknown_capability``,
    ``tip_too_generic_condition``, ``tip_too_generic_action``, ``tip_fact_shaped``.
    """
    pinned, body = strip_pinned_prefix(text)
    if pinned:
        return None

    parsed = parse_tip(body)
    if parsed is None:
        lowered = body.lower()
        if not lowered.startswith("when") or ": use" not in lowered:
            # Declarative / non-procedural → fact-shaped or malformed.
            if "use " not in lowered or " to " not in lowered:
                return "tip_fact_shaped"
            return "tip_malformed"
        return "tip_malformed"

    condition, capability, action = parsed
    names_lower = {n.lower() for n in capability_names}
    if capability.lower() not in names_lower:
        return "tip_unknown_capability"
    if _condition_too_generic(condition):
        return "tip_too_generic_condition"
    if _action_too_generic(action):
        return "tip_too_generic_action"
    return None


def is_valid_tip_shape(text: str, capability_names: Optional[Set[str]] = None) -> bool:
    """True when text is a well-formed TIP (and capability is known if names given)."""
    parsed = parse_tip(text)
    if parsed is None:
        return False
    if capability_names is None:
        return True
    _, capability, _ = parsed
    return capability.lower() in {n.lower() for n in capability_names}


__all__ = [
    "parse_tip",
    "strip_pinned_prefix",
    "tip_purge_reason",
    "is_valid_tip_shape",
]

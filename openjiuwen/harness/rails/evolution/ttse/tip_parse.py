# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Deterministic TIP shape parsing and quality gates for Auto-dream purge."""

from __future__ import annotations

import re
from typing import FrozenSet, Optional, Set, Tuple

from openjiuwen.core.common.logging import logger

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
    r"\b(csv|json|log|xml|yaml|yml|tsv|parquet|api)\b|"
    r"[\\/][\w.\-]+|"  # path fragment
    r"`[^`]+`|"
    r"\b(skill|tool)\b)",
    re.IGNORECASE,
)

# Leading filler words before a tool/skill name in the use-span.
_CAPABILITY_SKIP_TOKENS: FrozenSet[str] = frozenset({"the", "a", "an", "skill", "tool"})


def strip_pinned_prefix(text: str) -> Tuple[bool, str]:
    """Return (is_pinned, body) for optional ``[PINNED]`` prefix."""
    s = (text or "").strip()
    if s.upper().startswith("[PINNED]"):
        return True, s[8:].strip()
    return False, s


def _capability_span(raw: str) -> str:
    """Normalize the ``use … to`` middle segment: drop backticks/quotes, collapse space."""
    s = (raw or "").replace("`", "").replace('"', "").replace("'", "")
    return " ".join(s.split()).strip()


def _first_capability_token(span: str) -> str:
    """Fallback capability: first tool-like token after optional filler words."""
    for token in span.split():
        cleaned = token.strip(".,;:()[]{}")
        if not cleaned:
            continue
        if cleaned.lower() in _CAPABILITY_SKIP_TOKENS:
            continue
        return cleaned
    return ""


def _longest_whitelist_match(span: str, capability_names: Set[str]) -> Optional[str]:
    """Return the longest capability name that appears as a token-ish substring in ``span``."""
    span_l = span.lower()
    best: Optional[str] = None
    best_len = -1
    for name in capability_names:
        n = (name or "").strip()
        if not n:
            continue
        nl = n.lower()
        # Word-ish boundary so ``code`` does not match ``decode``.
        if re.search(rf"(^|[^\w]){re.escape(nl)}([^\w]|$)", span_l) and len(nl) > best_len:
            best = n
            best_len = len(nl)
    return best


def _split_tip(text: str) -> Optional[Tuple[str, str, str]]:
    """Split TIP into (condition, capability_span, action). Span keeps full use…to middle."""
    _, body = strip_pinned_prefix(text)
    match = _TIP_RE.match(body)
    if not match:
        return None
    condition = " ".join(match.group(1).split()).strip()
    span = _capability_span(match.group(2))
    action = " ".join(match.group(3).split()).strip()
    if not condition or not span or not action:
        return None
    return condition, span, action


def parse_tip(text: str) -> Optional[Tuple[str, str, str]]:
    """Parse ``When <cond>: use <cap> to <action>`` -> (condition, capability, action).

    Capability is the first tool-like token in the use-span after stripping backticks.
    Returns ``None`` when the text does not match the required shape.
    """
    split = _split_tip(text)
    if split is None:
        return None
    condition, span, action = split
    capability = _first_capability_token(span)
    logger.info(
        "[TTSERail] parse_tip condition=%s capability=%s span=%s action=%s",
        condition,
        capability,
        span,
        action,
    )
    if not capability:
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

    Capability membership uses longest whitelist match against the full use-span
    (backticks stripped), not exact equality on the whole span.
    """
    pinned, body = strip_pinned_prefix(text)
    if pinned:
        return None

    split = _split_tip(body)
    if split is None:
        lowered = body.lower()
        if not lowered.startswith("when") or ": use" not in lowered:
            # Declarative / non-procedural → fact-shaped or malformed.
            if "use " not in lowered or " to " not in lowered:
                return "tip_fact_shaped"
            return "tip_malformed"
        return "tip_malformed"

    condition, span, action = split
    if _longest_whitelist_match(span, capability_names) is None:
        return "tip_unknown_capability"
    if _condition_too_generic(condition):
        return "tip_too_generic_condition"
    if _action_too_generic(action):
        return "tip_too_generic_action"
    return None


def is_valid_tip_shape(text: str, capability_names: Optional[Set[str]] = None) -> bool:
    """True when text is a well-formed TIP (and capability is known if names given)."""
    split = _split_tip(text)
    if split is None:
        return False
    if capability_names is None:
        return bool(_first_capability_token(split[1]))
    return _longest_whitelist_match(split[1], capability_names) is not None


__all__ = [
    "parse_tip",
    "strip_pinned_prefix",
    "tip_purge_reason",
    "is_valid_tip_shape",
]

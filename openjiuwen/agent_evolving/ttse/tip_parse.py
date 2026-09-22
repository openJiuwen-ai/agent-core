# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""TIP shape parsing helpers (When <cond>: use <cap> to <action>)."""

from __future__ import annotations

import re
from typing import FrozenSet, Optional, Tuple

from openjiuwen.core.common.logging import logger

# When <condition>: use <capability> to <action>
_TIP_RE = re.compile(
    r"^\s*when\s+(.+?)\s*:\s*use\s+(.+?)\s+to\s+(.+?)\s*$",
    re.IGNORECASE | re.DOTALL,
)

# Leading filler words before a tool/skill name in the use-span.
_CAPABILITY_SKIP_TOKENS: FrozenSet[str] = frozenset({"the", "a", "an", "skill", "tool"})


def _normalize_tip_punctuation(text: str) -> str:
    """Map fullwidth colon to ASCII so ``When <cond>: use …`` still parses."""
    return (text or "").replace("：", ":")


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


def _split_tip(text: str) -> Optional[Tuple[str, str, str]]:
    """Split TIP into (condition, capability_span, action). Span keeps full use…to middle."""
    body = _normalize_tip_punctuation((text or "").strip())
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


__all__ = [
    "parse_tip",
]

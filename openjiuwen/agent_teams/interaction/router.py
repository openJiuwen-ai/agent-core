# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Pure helpers for parsing user-facing input routing syntax.

``@member_name body`` is the single routing grammar the runtime supports
for external input. Keeping the parser here (instead of inside the
dispatcher) makes it trivially testable and keeps the regex from leaking
into places that only need to *detect* a mention, not dispatch on it.
"""

from __future__ import annotations

import re

from openjiuwen.agent_teams.constants import RESERVED_MEMBER_NAMES

_MENTION_RE = re.compile(r"^@(\S+)\s+([\s\S]+)$")


def parse_mention(content: str) -> tuple[str, str] | None:
    """Parse ``@target body`` from raw user input.

    Returns ``(target, body)`` on a match; ``None`` when the input has
    no mention prefix. No validation of target existence — callers
    decide whether the name refers to a real roster entry.
    """
    if not content:
        return None
    match = _MENTION_RE.match(content)
    if match is None:
        return None
    target, body = match.group(1), match.group(2)
    return target, body


def is_reserved_name(name: str) -> bool:
    """Whether ``name`` collides with a runtime-reserved member name.

    Reserved names ("user", "team_leader", "human_agent") are owned by
    the runtime and must not be reused by user-declared members.
    """
    return name in RESERVED_MEMBER_NAMES


__all__ = [
    "is_reserved_name",
    "parse_mention",
]

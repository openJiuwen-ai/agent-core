# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unified prefixed id generator for agent-team background tasks.

Single source of truth for task-style ids inside ``agent_teams`` so a human
or an LLM can tell a task's kind from the id at a glance (e.g. ``w3xf9kv2``
is a swarmflow run). The prefix scheme mirrors Claude Code's (``b`` = bash,
``a`` = agent, ``w`` = workflow, ...): a one-character kind prefix followed
by a base36 random body. Ids stay opaque strings — nothing downstream parses
them. Kept in the ``agent_teams`` layer rather than ``core`` since the kinds
it indexes (swarmflow / session_spawn / async_tool) are team-scoped.
"""

from __future__ import annotations

import secrets

# base36: digits + lowercase ASCII. Compact, url-safe, case-insensitive.
_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"

# Kind -> one-character prefix. Unknown kinds fall back to ``_DEFAULT_PREFIX``.
# Keep prefixes single-character and mnemonic so ids stay short and scannable.
_TASK_PREFIXES: dict[str, str] = {
    "async_tool": "x",  # generic async background tool
    "swarmflow": "w",  # swarmflow orchestration run
    "session_spawn": "s",  # spawned sub-session
}

_DEFAULT_PREFIX = "t"


def generate_id(kind: str, *, length: int = 8) -> str:
    """Return a prefixed random id of the form ``{prefix}{base36*length}``.

    Args:
        kind: The task kind, mapped to a one-character prefix via
            ``_TASK_PREFIXES``. An unknown kind uses the default prefix ``t``.
        length: Number of base36 characters in the random body (default 8).

    Returns:
        An id such as ``"w3xf9kv2"`` (swarmflow) or ``"thq0a1b2"`` (unknown
        kind). The body is drawn from a cryptographically strong source.
    """
    prefix = _TASK_PREFIXES.get(kind, _DEFAULT_PREFIX)
    body = "".join(secrets.choice(_ALPHABET) for _ in range(length))
    return f"{prefix}{body}"


__all__ = ["generate_id"]

# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Module-wide constants for agent teams.

Central home for reserved member names so the runtime has a single source
of truth instead of scattered string literals. Adding a new reserved name
means updating this module and nothing else.
"""

from __future__ import annotations

HUMAN_AGENT_MEMBER_NAME: str = "human_agent"
"""Reserved member name for the human collaborator in a HITT team."""

USER_PSEUDO_MEMBER_NAME: str = "user"
"""Pseudo-member representing the external caller (not a team member)."""

DEFAULT_LEADER_MEMBER_NAME: str = "team_leader"
"""Default leader member name when no explicit override is provided."""

RESERVED_MEMBER_NAMES: frozenset[str] = frozenset(
    {
        HUMAN_AGENT_MEMBER_NAME,
        USER_PSEUDO_MEMBER_NAME,
        DEFAULT_LEADER_MEMBER_NAME,
    }
)
"""Names that user-declared members must never take.

Enforced at ``TeamAgentSpec.build()`` time. ``human_agent`` is allowed only
when the runtime injects it via ``enable_hitt=True``; manual declarations
under these names are rejected to keep model-facing identities stable.
"""

__all__ = [
    "DEFAULT_LEADER_MEMBER_NAME",
    "HUMAN_AGENT_MEMBER_NAME",
    "RESERVED_MEMBER_NAMES",
    "USER_PSEUDO_MEMBER_NAME",
]

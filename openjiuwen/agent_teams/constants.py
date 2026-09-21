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

RESERVED_TEAM_DIR_NAMES: frozenset[str] = frozenset(
    {
        "members",
        "jiuwen_team_members",
        "agent_groups",
        "remote_repos",
        "traces",
    }
)
"""Root-level directory names under ``.agent_teams/`` reserved for runtime
state (member real dirs — current and pre-rename layouts, agent group
definitions, remote repo caches, trace dumps). A team must never take one of
these names: its per-team directory would collide with — and be mistaken for —
runtime state.
"""


def is_reserved_team_dir_name(name: str) -> bool:
    """Whether ``name`` collides with a reserved ``.agent_teams/`` root entry.

    Case-insensitive on purpose: Windows filesystems are case-insensitive, so
    ``Jiuwen_Team_Members`` would collide with ``jiuwen_team_members/`` just
    as much.
    """
    return str(name or "").strip().casefold() in RESERVED_TEAM_DIR_NAMES


__all__ = [
    "DEFAULT_LEADER_MEMBER_NAME",
    "HUMAN_AGENT_MEMBER_NAME",
    "RESERVED_MEMBER_NAMES",
    "RESERVED_TEAM_DIR_NAMES",
    "USER_PSEUDO_MEMBER_NAME",
    "is_reserved_team_dir_name",
]

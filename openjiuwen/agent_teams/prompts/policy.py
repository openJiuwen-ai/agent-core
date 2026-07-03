# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Role-aware policy text loader.

The system prompt is the primary driver of team behavior — the
CoordinatorLoop only wakes the DeepAgent and injects unread messages;
all decision logic comes from these prompts.

Policy text lives in external Markdown templates under ``cn/`` and ``en/``
so prompt authors can edit them without touching Python source. The full
team system prompt is assembled from the per-section builders in
``sections.py`` (consumed by ``TeamPolicyRail`` and, for external CLI
members, ``build_team_member_system_prompt``); this module only exposes the
role-policy slice those builders read.
"""

from __future__ import annotations

from openjiuwen.agent_teams.prompts.loader import load_template
from openjiuwen.agent_teams.schema.team import TeamRole


def role_policy(role: TeamRole, language: str = "cn") -> str:
    """Return the base policy string for a role.

    Args:
        role: LEADER loads ``leader_policy``; any other role loads
            ``teammate_policy``.
        language: Markdown template language (``"cn"`` / ``"en"``).

    Returns:
        The role policy markdown content.
    """
    name = "leader_policy" if role == TeamRole.LEADER else "teammate_policy"
    return load_template(name, language).content


__all__ = ["role_policy"]

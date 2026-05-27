# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Agent-team prompt assembly: template loaders, policy text, section builders.

This package owns everything that produces system-prompt content for a
TeamAgent. Consumers (rails, configurators, tests) import from here
instead of reaching into individual files.

Layout:
- ``loader``: ``load_template`` / ``load_shared_template`` for markdown.
- ``policy``: legacy monolithic ``build_system_prompt`` + ``role_policy``.
- ``sections``: per-section ``PromptSection`` builders consumed by the rail.
- ``section_cache``: mtime-keyed cache primitive for dynamic sections.
- ``system_prompt.md`` / ``cn/`` / ``en/``: markdown templates.
"""

from __future__ import annotations

from openjiuwen.agent_teams.prompts.loader import (
    load_shared_template,
    load_template,
)
from openjiuwen.agent_teams.prompts.policy import (
    build_system_prompt,
    role_policy,
)
from openjiuwen.agent_teams.prompts.section_cache import MtimeSectionCache
from openjiuwen.agent_teams.prompts.sections import (
    TeamSectionName,
    build_team_bridge_section,
    build_team_extra_section,
    build_team_hitt_section,
    build_team_info_section,
    build_team_lifecycle_section,
    build_team_member_system_prompt,
    build_team_members_section,
    build_team_persona_section,
    build_team_role_section,
    build_team_static_sections,
    build_team_workflow_section,
)
from openjiuwen.agent_teams.prompts.team_plan_agent import (
    DEFAULT_TEAM_PLAN_AGENT_SYSTEM_PROMPT,
    TEAM_PLAN_AGENT_DESC,
    TEAM_PLAN_AGENT_SYSTEM_PROMPT_CN,
    TEAM_PLAN_AGENT_SYSTEM_PROMPT_EN,
    apply_team_plan_agent_prompt,
    build_team_plan_agent_card,
)
from openjiuwen.agent_teams.prompts.team_plan_mode import (
    TEAM_PLAN_MODE_PROMPT_CN,
    TEAM_PLAN_MODE_PROMPT_EN,
    build_team_plan_mode_prompt,
    build_team_plan_mode_prompt_template,
    build_team_plan_mode_section,
    get_team_plan_mode_prompt,
)

__all__ = [
    "MtimeSectionCache",
    "DEFAULT_TEAM_PLAN_AGENT_SYSTEM_PROMPT",
    "TEAM_PLAN_AGENT_DESC",
    "TEAM_PLAN_AGENT_SYSTEM_PROMPT_CN",
    "TEAM_PLAN_AGENT_SYSTEM_PROMPT_EN",
    "TEAM_PLAN_MODE_PROMPT_CN",
    "TEAM_PLAN_MODE_PROMPT_EN",
    "TeamSectionName",
    "apply_team_plan_agent_prompt",
    "build_system_prompt",
    "build_team_bridge_section",
    "build_team_plan_agent_card",
    "build_team_extra_section",
    "build_team_hitt_section",
    "build_team_info_section",
    "build_team_lifecycle_section",
    "build_team_member_system_prompt",
    "build_team_members_section",
    "build_team_persona_section",
    "build_team_plan_mode_prompt",
    "build_team_plan_mode_prompt_template",
    "build_team_plan_mode_section",
    "build_team_role_section",
    "build_team_static_sections",
    "build_team_workflow_section",
    "get_team_plan_mode_prompt",
    "load_shared_template",
    "load_template",
    "role_policy",
]

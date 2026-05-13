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
    build_team_extra_section,
    build_team_hitt_section,
    build_team_info_section,
    build_team_lifecycle_section,
    build_team_members_section,
    build_team_persona_section,
    build_team_role_section,
    build_team_workflow_section,
)

__all__ = [
    "MtimeSectionCache",
    "TeamSectionName",
    "build_system_prompt",
    "build_team_extra_section",
    "build_team_hitt_section",
    "build_team_info_section",
    "build_team_lifecycle_section",
    "build_team_members_section",
    "build_team_persona_section",
    "build_team_role_section",
    "build_team_workflow_section",
    "load_shared_template",
    "load_template",
    "role_policy",
]

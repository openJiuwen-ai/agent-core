# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Agent evolution prompt section builders."""

from openjiuwen.agent_evolving.prompts.sections.evolution import (
    EVOLUTION_PROTOCOL_PROMPT,
    TEAM_EVOLUTION_PROTOCOL_PROMPT,
    build_evolution_protocol_section,
    build_team_evolution_protocol_section,
)
from openjiuwen.agent_evolving.prompts.sections.skill_creation import (
    build_skill_creation_guidance_section,
    build_team_skill_creation_guidance_section,
    build_team_skill_creation_nudge_section,
)

__all__ = [
    "EVOLUTION_PROTOCOL_PROMPT",
    "TEAM_EVOLUTION_PROTOCOL_PROMPT",
    "build_evolution_protocol_section",
    "build_skill_creation_guidance_section",
    "build_team_evolution_protocol_section",
    "build_team_skill_creation_guidance_section",
    "build_team_skill_creation_nudge_section",
]

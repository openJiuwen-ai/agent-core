# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Agent evolution prompt sections and tool metadata providers."""

from openjiuwen.agent_evolving.prompts.sections import (
    EVOLUTION_PROTOCOL_PROMPT,
    TEAM_EVOLUTION_PROTOCOL_PROMPT,
    build_evolution_protocol_section,
    build_skill_creation_guidance_section,
    build_team_evolution_protocol_section,
    build_team_skill_creation_guidance_section,
    build_team_skill_creation_nudge_section,
)
from openjiuwen.agent_evolving.prompts.tools import (
    EvolveReviewTaskMetadataProvider,
    EvolveSkillExperiencesMetadataProvider,
    ListSkillExperiencesMetadataProvider,
    PrepareSkillEvolutionReviewMetadataProvider,
    ReadSkillExperiencesMetadataProvider,
    SimplifySkillExperiencesMetadataProvider,
    build_evolution_tool_card,
    build_evolution_subject_schema,
    get_evolution_tool_description,
    get_evolution_tool_input_params,
)

__all__ = [
    "EVOLUTION_PROTOCOL_PROMPT",
    "TEAM_EVOLUTION_PROTOCOL_PROMPT",
    "EvolveReviewTaskMetadataProvider",
    "EvolveSkillExperiencesMetadataProvider",
    "ListSkillExperiencesMetadataProvider",
    "PrepareSkillEvolutionReviewMetadataProvider",
    "ReadSkillExperiencesMetadataProvider",
    "SimplifySkillExperiencesMetadataProvider",
    "build_evolution_protocol_section",
    "build_evolution_tool_card",
    "build_evolution_subject_schema",
    "build_skill_creation_guidance_section",
    "build_team_evolution_protocol_section",
    "build_team_skill_creation_guidance_section",
    "build_team_skill_creation_nudge_section",
    "get_evolution_tool_description",
    "get_evolution_tool_input_params",
]

# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Team.plan specialization for the built-in plan subagent.

The generic ``plan_agent`` lives under ``openjiuwen.harness`` and remains
code-plan oriented. Team-level planning prompt ownership stays in
``agent_teams`` because it describes team composition, delegation, and approval
workflow rather than generic DeepAgent behavior.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from openjiuwen.agent_teams.prompts.loader import load_template
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.prompts import resolve_language
from openjiuwen.harness.schema.config import SubAgentConfig
from openjiuwen.harness.subagents.plan_agent import (
    DEFAULT_PLAN_AGENT_SYSTEM_PROMPT,
)


TEAM_PLAN_AGENT_DESC: Dict[str, str] = {
    "cn": "团队规划专家。基于目标、约束和上下文设计团队执行方案、分工、依赖和验收计划。",
    "en": (
        "Team planning specialist. Designs team execution strategy, role split, "
        "dependencies, and acceptance plans from goals, constraints, and context."
    ),
}

TEAM_PLAN_AGENT_SYSTEM_PROMPT_CN = str(load_template("team_plan_agent", "cn").content).strip()
TEAM_PLAN_AGENT_SYSTEM_PROMPT_EN = str(load_template("team_plan_agent", "en").content).strip()

DEFAULT_TEAM_PLAN_AGENT_SYSTEM_PROMPT: Dict[str, str] = {
    "cn": TEAM_PLAN_AGENT_SYSTEM_PROMPT_CN,
    "en": TEAM_PLAN_AGENT_SYSTEM_PROMPT_EN,
}


def _team_plan_agent_description(language: str) -> str:
    return TEAM_PLAN_AGENT_DESC.get(language, TEAM_PLAN_AGENT_DESC["cn"])


def _team_plan_agent_prompt(language: str) -> str:
    return DEFAULT_TEAM_PLAN_AGENT_SYSTEM_PROMPT.get(
        language,
        DEFAULT_TEAM_PLAN_AGENT_SYSTEM_PROMPT["cn"],
    )


def apply_team_plan_agent_prompt(
    subagents: Optional[list[Any]],
    *,
    language: Optional[str] = None,
) -> bool:
    """Specialize the built-in plan_agent for team.plan leaders.

    User-provided custom plan_agent prompts are left untouched. The helper only
    replaces the default code-plan prompt injected by create_code_agent().
    """
    if not subagents:
        return False

    resolved_language = resolve_language(language)
    builtin_prompts = set(DEFAULT_PLAN_AGENT_SYSTEM_PROMPT.values())

    for spec in subagents:
        if not isinstance(spec, SubAgentConfig):
            continue
        if spec.agent_card.name != "plan_agent":
            continue
        if spec.system_prompt not in builtin_prompts:
            return False
        spec.system_prompt = _team_plan_agent_prompt(resolved_language)
        spec.agent_card = spec.agent_card.model_copy(
            update={
                "description": _team_plan_agent_description(resolved_language),
            }
        )
        return True
    return False


def build_team_plan_agent_card(language: Optional[str] = None) -> AgentCard:
    """Build the default team.plan plan-agent card for tests and customizers."""
    resolved_language = resolve_language(language)
    return AgentCard(
        name="plan_agent",
        description=_team_plan_agent_description(resolved_language),
    )


__all__ = [
    "DEFAULT_TEAM_PLAN_AGENT_SYSTEM_PROMPT",
    "TEAM_PLAN_AGENT_DESC",
    "TEAM_PLAN_AGENT_SYSTEM_PROMPT_CN",
    "TEAM_PLAN_AGENT_SYSTEM_PROMPT_EN",
    "apply_team_plan_agent_prompt",
    "build_team_plan_agent_card",
]

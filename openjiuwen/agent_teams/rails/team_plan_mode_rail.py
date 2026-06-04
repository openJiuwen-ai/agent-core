# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Team.plan prompt overlay rail.

The generic ``AgentModeRail`` owns plan-mode mechanics and safety. This rail
owns only the Team Leader's team.plan prompt semantics.
"""

from __future__ import annotations

from typing import Any

from openjiuwen.agent_teams.prompts.team_plan_agent import apply_team_plan_agent_prompt
from openjiuwen.agent_teams.prompts.team_plan_mode import build_team_plan_mode_section
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.prompts import resolve_language
from openjiuwen.harness.prompts.sections import SectionName
from openjiuwen.harness.rails.base import DeepAgentRail


class TeamPlanModeRail(DeepAgentRail):
    """Inject team.plan leader instructions during plan mode.

    Runs after ``AgentModeRail`` (priority 85) so the team-specific
    ``MODE_INSTRUCTIONS`` section replaces the generic plan prompt while
    preserving all generic plan tools and safety checks.
    """

    priority = 84

    def __init__(self, *, language: str | None = None) -> None:
        super().__init__()
        self._language_override = resolve_language(language) if language else None
        self._agent: Any | None = None
        self.system_prompt_builder: Any | None = None

    def init(self, agent: Any) -> None:
        """Cache prompt builder and specialize the default plan subagent."""
        self._agent = agent
        self.system_prompt_builder = getattr(agent, "system_prompt_builder", None)
        self._specialize_plan_agent()

    def uninit(self, agent: Any) -> None:  # noqa: ARG002
        """Remove the team.plan prompt overlay."""
        if self.system_prompt_builder is not None:
            self.system_prompt_builder.remove_section(SectionName.MODE_INSTRUCTIONS)
        self._agent = None
        self.system_prompt_builder = None

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """Replace generic plan instructions with team.plan instructions."""
        if self._agent is None or self.system_prompt_builder is None:
            return

        state = self._agent.load_state(ctx.session)
        if getattr(state.plan_mode, "mode", None) != "plan":
            self.system_prompt_builder.remove_section(SectionName.MODE_INSTRUCTIONS)
            return

        self._specialize_plan_agent()
        language = self._resolve_language()
        self.system_prompt_builder.add_section(
            build_team_plan_mode_section(
                language=language,
                agent=self._agent,
                session=ctx.session,
            )
        )

    def _resolve_language(self) -> str:
        """Resolve team.plan prompt language independently from code profile."""
        if self._language_override:
            return self._language_override
        return resolve_language(getattr(self.system_prompt_builder, "language", None))

    def _specialize_plan_agent(self) -> None:
        """Specialize built-in plan_agent, including subagents added after init."""
        if self._agent is None:
            return
        deep_config = getattr(self._agent, "deep_config", None)
        applied = apply_team_plan_agent_prompt(
            getattr(deep_config, "subagents", None),
            language=self._resolve_language(),
        )
        if applied:
            team_logger.info("[team.plan] specialized built-in plan_agent prompt")


__all__ = ["TeamPlanModeRail"]

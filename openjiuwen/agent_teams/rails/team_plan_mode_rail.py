# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Team.plan prompt overlay rail.

The generic ``AgentModeRail`` owns plan-mode mechanics and safety. This rail
owns only the Team Leader's team.plan prompt semantics.
"""

from __future__ import annotations

from typing import Any

from openjiuwen.agent_teams.prompts.loader import TemplateLoader, load_template
from openjiuwen.agent_teams.prompts.team_plan_agent import apply_team_plan_agent_prompt
from openjiuwen.agent_teams.prompts.team_plan_mode import build_team_plan_mode_section
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.prompts import resolve_language
from openjiuwen.harness.prompts.prompt_attachment_manager import PromptAttachmentKind
from openjiuwen.harness.prompts.sections import SectionName
from openjiuwen.harness.rails.base import DeepAgentRail


class TeamPlanModeRail(DeepAgentRail):
    """Inject team.plan leader instructions during plan mode.

    Runs after ``AgentModeRail`` (priority 85) so the team-specific
    ``MODE_INSTRUCTIONS`` section replaces the generic plan prompt while
    preserving all generic plan tools and safety checks.
    """

    priority = 84

    def __init__(
        self,
        *,
        language: str | None = None,
        loader: TemplateLoader = load_template,
    ) -> None:
        super().__init__()
        self._language_override = resolve_language(language) if language else None
        # The loader is bound at construction by the rail factory
        # (elements.py) from the backend's cache; the default is the
        # framework read-only loader. No second
        # fallback here — the factory owns the cache wiring.
        self._loader = loader
        self._agent: Any | None = None
        self.system_prompt_builder: Any | None = None
        self.attachment_manager: Any | None = None

    def init(self, agent: Any) -> None:
        """Cache prompt builder and specialize the default plan subagent."""
        self._agent = agent
        self.system_prompt_builder = getattr(agent, "system_prompt_builder", None)
        self.attachment_manager = getattr(agent, "prompt_attachment_manager", None)
        self._specialize_plan_agent()

    def uninit(self, agent: Any) -> None:  # noqa: ARG002
        """Remove the team.plan prompt overlay."""
        if self.system_prompt_builder is not None:
            self.system_prompt_builder.remove_section(SectionName.MODE_INSTRUCTIONS)
        self._agent = None
        self.system_prompt_builder = None
        self.attachment_manager = None

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        """Stage the team.plan attachment before the first user message."""
        await self._sync_plan_attachment(ctx)

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """Replace generic plan instructions with team.plan instructions."""
        await self._sync_plan_attachment(ctx)

    async def _sync_plan_attachment(self, ctx: AgentCallbackContext) -> None:
        """Upsert or clear team.plan guidance in attachment history."""
        if self._agent is None or self.system_prompt_builder is None or ctx.session is None:
            return

        state = self._agent.load_state(ctx.session)
        if getattr(state.plan_mode, "mode", None) != "plan":
            self.system_prompt_builder.remove_section(SectionName.MODE_INSTRUCTIONS)
            if self.attachment_manager is not None:
                try:
                    await self.attachment_manager.bind_context(ctx).clear_section(SectionName.MODE_INSTRUCTIONS)
                except ValueError as exc:
                    team_logger.warning("[team.plan] failed to clear attachment: {}", exc)
            return

        self._specialize_plan_agent()
        language = self._resolve_language()
        section = build_team_plan_mode_section(
            language=language,
            agent=self._agent,
            session=ctx.session,
            loader=self._loader,
        )
        self.system_prompt_builder.remove_section(SectionName.MODE_INSTRUCTIONS)
        if self.attachment_manager is None:
            self.system_prompt_builder.add_section(section)
            return
        try:
            await self.attachment_manager.bind_context(ctx).add_from_prompt_section(
                prompt_section=section,
                kind=PromptAttachmentKind.RUNTIME,
                source="agent_core.team_plan_mode_rail",
                language=language,
                content_kind="text/markdown",
            )
        except ValueError as exc:
            team_logger.warning("[team.plan] failed to queue attachment: {}", exc)
            self.system_prompt_builder.add_section(section)

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
            loader=self._loader,
        )
        if applied:
            team_logger.info("[team.plan] specialized built-in plan_agent prompt")


__all__ = ["TeamPlanModeRail"]

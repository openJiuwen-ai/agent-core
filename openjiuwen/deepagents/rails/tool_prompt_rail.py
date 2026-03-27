# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""ToolPromptRail — dynamically injects the tool prompt section before each model call."""
from __future__ import annotations

from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.deepagents.prompts.sections import SectionName
from openjiuwen.deepagents.prompts.sections.tools import build_tools_section
from openjiuwen.deepagents.rails.base import DeepAgentRail


class ToolPromptRail(DeepAgentRail):
    """Rail that injects the tool prompt section into system prompt.

    Dynamically reads the agent's registered tools at each model call
    and builds the tool section from their actual descriptions.
    """

    priority = 85

    def __init__(self, language: str = "cn") -> None:
        super().__init__()
        self.language = language
        self.system_prompt_builder = None
        self._ability_manager = None

    def init(self, agent) -> None:
        self.system_prompt_builder = getattr(agent, "system_prompt_builder", None)
        self._ability_manager = getattr(agent, "ability_manager", None)

    def uninit(self, agent) -> None:
        if self.system_prompt_builder is not None:
            self.system_prompt_builder.remove_section(SectionName.TOOLS)
        self.system_prompt_builder = None
        self._ability_manager = None

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """Inject tool prompt section before model call."""
        if self.system_prompt_builder is None:
            return

        tools_section = self._build_tools_section()
        if tools_section is not None:
            self.system_prompt_builder.add_section(tools_section)
        else:
            self.system_prompt_builder.remove_section(SectionName.TOOLS)

    def _build_tools_section(self):
        """Build PromptSection from runtime tool list."""
        if self._ability_manager is None:
            return None

        tool_descriptions = {}
        for ability in self._ability_manager.list():
            if isinstance(ability, ToolCard) and ability.name and ability.description:
                tool_descriptions[ability.name] = ability.description

        return build_tools_section(
            tool_descriptions=tool_descriptions,
            language=self.language,
        )


__all__ = [
    "ToolPromptRail",
]

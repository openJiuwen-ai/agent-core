# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""SecurityPromptRail — injects the safety prompt section before each model call."""
from __future__ import annotations

from openjiuwen.core.common.logging import logger
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.deepagents.prompts.sections import SectionName
from openjiuwen.deepagents.prompts.sections.safety import build_safety_section
from openjiuwen.deepagents.rails.base import DeepAgentRail


class SecurityRail(DeepAgentRail):
    """Rail that injects the safety prompt section into system prompt.

    Reads the bilingual safety/security guidelines and adds them
    as a PromptSection before each model call.
    """

    priority = 85

    def __init__(self, language: str = "cn") -> None:
        super().__init__()
        self.language = language
        self.system_prompt_builder = None

    def init(self, agent) -> None:
        self.system_prompt_builder = getattr(agent, "system_prompt_builder", None)

    def uninit(self, agent) -> None:
        if self.system_prompt_builder is not None:
            self.system_prompt_builder.remove_section(SectionName.SAFETY)
        self.system_prompt_builder = None

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """Inject safety prompt section before model call."""
        if self.system_prompt_builder is None:
            return

        safety_section = build_safety_section(self.language)
        if safety_section is not None:
            self.system_prompt_builder.add_section(safety_section)


__all__ = [
    "SecurityRail",
]

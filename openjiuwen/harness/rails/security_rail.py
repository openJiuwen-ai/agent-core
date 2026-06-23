# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""SecurityPromptRail — injects the safety and system-authority prompt sections before each model call."""
from __future__ import annotations

from openjiuwen.core.common.logging import logger
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.prompts.sections import SectionName
from openjiuwen.harness.prompts.sections.safety import build_safety_section
from openjiuwen.harness.prompts.sections.system_authority import build_system_authority_section
from openjiuwen.harness.rails.base import DeepAgentRail


class SecurityRail(DeepAgentRail):
    """Rail that injects the safety and system-authority prompt sections into system prompt.

    Reads the bilingual safety/security guidelines and the global system-authority
    declaration, adding both as PromptSections before each model call.
    """

    priority = 85

    def __init__(self) -> None:
        super().__init__()
        self.system_prompt_builder = None

    def init(self, agent) -> None:
        self.system_prompt_builder = getattr(agent, "system_prompt_builder", None)

    def uninit(self, agent) -> None:
        if self.system_prompt_builder is not None:
            self.system_prompt_builder.remove_section(SectionName.SAFETY)
            self.system_prompt_builder.remove_section(SectionName.SYSTEM_AUTHORITY)
        self.system_prompt_builder = None

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """Inject safety and system-authority prompt sections before model call."""
        if self.system_prompt_builder is None:
            return

        language = self.system_prompt_builder.language
        safety_section = build_safety_section(language)
        if safety_section is not None:
            self.system_prompt_builder.add_section(safety_section)

        authority_section = build_system_authority_section(language)
        if authority_section is not None:
            self.system_prompt_builder.add_section(authority_section)


__all__ = [
    "SecurityRail",
]

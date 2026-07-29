# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Safety and prompt security rails."""

from __future__ import annotations

from openjiuwen.core.single_agent.rail.base import AgentCallbackEvent
from openjiuwen.harness.prompts.sections import SectionName
from openjiuwen.harness.prompts.sections.safety import build_safety_section
from openjiuwen.harness.rails.security.base_security_rail import (
    BaseSecurityRail,
    SecurityCheckContext,
)


class SafetyPromptRail(BaseSecurityRail):
    """Rail that injects the safety prompt section into system prompt.

    Reads the bilingual safety/security guidelines and adds them
    as a PromptSection before each model call.
    """

    priority = 85
    supported_events = {AgentCallbackEvent.BEFORE_MODEL_CALL}

    def __init__(self) -> None:
        super().__init__()
        self.system_prompt_builder = None

    def init(self, agent) -> None:
        self.system_prompt_builder = getattr(agent, "system_prompt_builder", None)

    def uninit(self, agent) -> None:
        if self.system_prompt_builder is not None:
            self.system_prompt_builder.remove_section(SectionName.SAFETY)
        self.system_prompt_builder = None

    async def run_security_check(self, security_ctx: SecurityCheckContext):
        """Inject safety prompt section before model call."""
        if self.system_prompt_builder is None:
            return self.allow()

        safety_section = build_safety_section(self.system_prompt_builder.language)
        if safety_section is not None:
            self.system_prompt_builder.add_section(safety_section)
        return self.allow()


SecurityRail = SafetyPromptRail


__all__ = [
    "SafetyPromptRail",
    "SecurityRail",
]

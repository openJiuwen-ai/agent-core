# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from __future__ import annotations

from typing import TYPE_CHECKING

from openjiuwen.core.single_agent.prompts.builder import PromptSection
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.base import DeepAgentRail

if TYPE_CHECKING:
    from openjiuwen.harness.deep_agent import DeepAgent

_SECTION_NAME = "member_harness_structure"
_SECTION_PRIORITY = 88


class HarnessStructureRail(DeepAgentRail):
    """Inject target harness structure summaries into the planner system prompt."""

    priority = 88

    def __init__(self, harness_summaries: dict[str, str] | None = None) -> None:
        super().__init__()
        self._harness_summaries = dict(harness_summaries or {})
        self.system_prompt_builder = None

    def init(self, agent: "DeepAgent") -> None:
        self.system_prompt_builder = getattr(agent, "system_prompt_builder", None)

    def uninit(self, agent: "DeepAgent") -> None:
        if self.system_prompt_builder is not None:
            self.system_prompt_builder.remove_section(_SECTION_NAME)

    def update_harness_summaries(self, harness_summaries: dict[str, str]) -> None:
        self._harness_summaries = dict(harness_summaries)

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        del ctx
        if self.system_prompt_builder is None:
            return
        if not self._harness_summaries:
            self.system_prompt_builder.remove_section(_SECTION_NAME)
            return
        content_lines = [
            "## Role Harness Structure",
            "",
            "The following summaries describe the REAL existing harness surfaces for each role.",
            "Prefer plans that modify these existing surfaces instead of inventing missing files.",
            "",
        ]
        for role, summary in self._harness_summaries.items():
            content_lines.append(f"- {role}: {summary}")
        self.system_prompt_builder.remove_section(_SECTION_NAME)
        self.system_prompt_builder.add_section(
            PromptSection(
                name=_SECTION_NAME,
                content={"en": "\n".join(content_lines), "cn": "\n".join(content_lines)},
                priority=_SECTION_PRIORITY,
            )
        )

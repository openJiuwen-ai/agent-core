# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Experience rail for auto-harness agents."""

from __future__ import annotations

import logging
from typing import Set

from openjiuwen.core.runner.runner import Runner
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
)
from openjiuwen.harness.prompts.builder import PromptSection
from openjiuwen.harness.prompts.sections import SectionName
from openjiuwen.harness.rails.base import DeepAgentRail
from openjiuwen.auto_harness.tools.experience_search_tool import (
    ExperienceSearchTool,
)

logger = logging.getLogger(__name__)


def build_experience_section(
    language: str = "cn",
    experience_dir: str = ".auto_harness/experience",
) -> PromptSection:
    """Build prompt guidance for auto-harness experience usage."""
    content = {
        "cn": (
            "## Experience Library\n\n"
            f"经验库位于 `{experience_dir}`。\n"
            "需要回顾历史优化、失败案例和洞察时，使用 `experience_search`。"
        ),
        "en": (
            "## Experience Library\n\n"
            f"The experience library lives at `{experience_dir}`.\n"
            "Use `experience_search` when reviewing prior optimizations, "
            "failures, and insights."
        ),
    }
    return PromptSection(
        name=SectionName.MEMORY,
        content=content,
        priority=85,
    )


class AutoHarnessExperienceRail(DeepAgentRail):
    """Register experience tool and inject experience prompt section."""

    priority = 80

    def __init__(
        self,
        experience_dir: str,
        *,
        language: str = "cn",
    ) -> None:
        super().__init__()
        self._experience_dir = experience_dir
        self._language = language
        self._owned_tool_names: Set[str] = set()
        self._owned_tool_ids: Set[str] = set()
        self.system_prompt_builder = None

    def init(self, agent) -> None:
        super().init(agent)
        self.system_prompt_builder = getattr(
            agent,
            "system_prompt_builder",
            None,
        )
        self._register_experience_tool(agent)

    def uninit(self, agent) -> None:
        if hasattr(agent, "ability_manager"):
            ability_mgr = agent.ability_manager
            for tool_name in list(self._owned_tool_names):
                ability_mgr.remove(tool_name)
        for tool_id in list(self._owned_tool_ids):
            if Runner.resource_mgr.get_tool(tool_id) is None:
                continue
            result = Runner.resource_mgr.remove_tool(tool_id)
            if hasattr(result, "is_err") and result.is_err():
                logger.warning(
                    "Failed to remove experience tool: %s",
                    tool_id,
                )
        self._owned_tool_ids.clear()
        self._owned_tool_names.clear()
        if self.system_prompt_builder is not None:
            self.system_prompt_builder.remove_section(
                SectionName.MEMORY
            )
            self.system_prompt_builder = None

    async def before_model_call(
        self,
        ctx: AgentCallbackContext,
    ) -> None:
        if self.system_prompt_builder is None:
            return
        self.system_prompt_builder.remove_section(
            SectionName.MEMORY
        )
        self.system_prompt_builder.add_section(
            build_experience_section(
                self.system_prompt_builder.language,
                self._experience_dir,
            )
        )

    def _register_experience_tool(self, agent) -> None:
        if not hasattr(agent, "ability_manager"):
            return
        tool = ExperienceSearchTool(
            self._experience_dir,
            language=self._language,
        )
        existing = Runner.resource_mgr.get_tool(tool.card.id)
        if existing is None:
            Runner.resource_mgr.add_tool(tool)
            self._owned_tool_ids.add(tool.card.id)
        result = agent.ability_manager.add(tool.card)
        if result.added:
            self._owned_tool_names.add(tool.card.name)

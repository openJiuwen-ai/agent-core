# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Sentinel tool for marking a skill complete and unloading its body."""
from __future__ import annotations

from typing import Any, AsyncIterator, Dict, Optional

from openjiuwen.core.foundation.tool import Tool
from openjiuwen.harness.prompts.sections.tools import build_tool_card
from openjiuwen.harness.tools import ToolOutput


class SkillCompleteTool(Tool):
    """Marks a skill as complete; SkillUseRail will unload its body."""

    def __init__(
        self,
        language: str = "cn",
        agent_id: Optional[str] = None,
    ):
        super().__init__(
            build_tool_card("skill_complete", "SkillCompleteTool", language, agent_id=agent_id)
        )
        self.language = language

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        skill_name = str(inputs.get("skill_name", "") or "").strip()
        if not skill_name:
            return ToolOutput(success=False, error="skill_name is required")
        return ToolOutput(
            success=True,
            data=f"Skill '{skill_name}' marked as complete; body unloaded.",
            extra_metadata={"unload_skill_name": skill_name},
        )

    async def stream(self, inputs: Dict[str, Any], **kwargs) -> AsyncIterator[Any]:
        if False:
            yield None


__all__ = ["SkillCompleteTool"]

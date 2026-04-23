# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from pathlib import Path
from typing import Any, AsyncIterator, Callable, Dict, List, Optional

from openjiuwen.core.foundation.tool import Tool
from openjiuwen.core.single_agent.skills.skill_manager import Skill
from openjiuwen.core.sys_operation.sys_operation import SysOperation
from openjiuwen.harness.prompts.tools import build_tool_card
from openjiuwen.harness.tools import ToolOutput


class SkillTool(Tool):
    """View the skill contents of a certain skill"""
    operation: SysOperation
    get_skills: Callable[[], List[Skill]]

    def __init__(
        self,
        operation: SysOperation,
        get_skills: Callable[[], List[Skill]],
        language: str = "cn",
        agent_id: Optional[str] = None,
    ):
        """Initialize SkillTool.

        Args:
            operation: SysOperation for file system operations to read files
            get_skills: Callable that returns current enabled skills.
        """
        super().__init__(
            build_tool_card("skill_tool", "SkillTool", language, agent_id=agent_id)
        )
        self.operation = operation
        self.get_skills = get_skills
        self.language = language

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        """Invoke skill_tool tool."""
        skill_name = str(inputs.get("skill_name", "") or "").strip()
        relative_file_path = str(inputs.get("relative_file_path") or "SKILL.md").strip() # SKILL.md by default

        try:
            skill = self._get_skill_by_name(skill_name)
            if not skill:
                return ToolOutput(
                    success=False,
                    error=f"Skill not found: {skill_name}"
                )
            
            file_path = str(Path(skill.directory) / relative_file_path)
            read_file_result = await self.operation.fs().read_file(file_path)
            if read_file_result.code != 0:
                return ToolOutput(
                    success=False,
                    error=read_file_result.message
                )

            skill_file_content = read_file_result.data.content

            return ToolOutput(
                success=True,
                data={
                    "skill_directory": str(skill.directory),
                    "skill_content": skill_file_content,
                },
            )
        
        except Exception as exc:
            return ToolOutput(
                success=False,
                error=str(exc),
            )

    async def stream(self, inputs: Dict[str, Any], **kwargs) -> AsyncIterator[Any]:
        if False:
            yield None

    def _get_skill_by_name(self, skill_name: str) -> Skill:
        """Select skill object by name."""
        if not skill_name:
            return None

        skills = self.get_skills() or []
        skill_map = {skill.name: skill for skill in skills}
        return skill_map.get(skill_name)
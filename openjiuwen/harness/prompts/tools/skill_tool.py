# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Bilingual description and input params for the Skill tool."""
from __future__ import annotations

from typing import Any, Dict

from openjiuwen.harness.prompts.tools.base import (
    ToolMetadataProvider,
)

DESCRIPTION: Dict[str, str] = {
    "cn": "使用此工具查看特定技能的内容",
    "en": "Use this tool to view the skill contents of a certain skill",
}

SKILL_TOOL_PARAMS: Dict[str, Dict[str, str]] = {
    "skill_name": {
        "cn": "技能的名称",
        "en": "Name of the skill",
    },
    "relative_file_path": {
        "cn": "可选。查看技能目录中指定路径（relative_file_path）下的特定文件。留空则查看主 SKILL.md 文件。",
        "en": "Optional. Views a specific file within the skill directory at the relative_file_path. "\
              "Leave blank to view the main SKILL.md file.",
    },
}


def get_skill_tool_input_params(language: str = "cn") -> Dict[str, Any]:
    """Return the full JSON Schema for skill tool input_params."""
    p = SKILL_TOOL_PARAMS
    return {
        "type": "object",
        "properties": {
            "skill_name": {
                "type": "string",
                "description": p["skill_name"].get(language, p["skill_name"]["cn"])
            },
            "relative_file_path": {
                "type": "string", 
                "description": p["relative_file_path"].get(language, p["relative_file_path"]["cn"])
            },
        },
        "required": ["skill_name"],
    }


class SkillToolMetadataProvider(ToolMetadataProvider):
    """SkillTool 工具的元数据 provider。"""

    def get_name(self) -> str:
        return "skill_tool"

    def get_description(self, language: str = "cn") -> str:
        return DESCRIPTION.get(language, DESCRIPTION["cn"])

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return get_skill_tool_input_params(language)

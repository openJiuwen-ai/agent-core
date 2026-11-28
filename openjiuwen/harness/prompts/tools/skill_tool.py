# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Bilingual description and input params for the Skill tool."""
from __future__ import annotations

from typing import Any, Dict

from openjiuwen.harness.prompts.tools.base import (
    ToolMetadataProvider,
)

DESCRIPTION: Dict[str, str] = {
    "cn": (
        "读取指定技能的 SKILL.md 或技能目录内的文件。可用时返回目录树和子技能路径。加载子技能时再次调用，传 relative_file_path（如 "
        "designer/SKILL.md）。"
    ),
    "en": (
        "Read a skill’s SKILL.md or another file within its directory. Returns a directory tree "
        "and nested skill paths when available. To load a nested skill, call again with "
        "relative_file_path (e.g. designer/SKILL.md)."
    ),
}

SKILL_TOOL_PARAMS: Dict[str, Dict[str, str]] = {
    "skill_name": {
        "cn": "技能的名称",
        "en": "Name of the skill",
    },
    "relative_file_path": {
        "cn": "技能目录内的相对路径。省略时读取主 SKILL.md。",
        "en": "File relative to the skill root; omit for SKILL.md.",
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

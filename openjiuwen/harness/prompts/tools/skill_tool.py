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
        "使用此工具查看特定技能的 SKILL.md 正文。仅支持读取技能目录下的 SKILL.md。"
        "成功时默认附带技能根目录的 ASCII 目录树（directory_tree）"
        "以及嵌套子技能相对路径列表（discovered_skill_names，含 SKILL.md 的子目录）；"
        "加载子技能请再次调用本工具，并设置 relative_file_path（如 designer/SKILL.md）。"
    ),
    "en": (
        "Use this tool to view a skill's SKILL.md body. "
        "Only SKILL.md within the skill directory is readable. "
        "On success it always includes an ASCII directory_tree of the skill root "
        "and discovered_skill_names (relative paths of nested dirs that contain SKILL.md). "
        "To load a nested skill, call again with relative_file_path "
        "(e.g. designer/SKILL.md)."
    ),
}

SKILL_TOOL_PARAMS: Dict[str, Dict[str, str]] = {
    "skill_name": {
        "cn": "技能的名称",
        "en": "Name of the skill",
    },
    "relative_file_path": {
        "cn": "可选。仅支持读取技能目录下的 SKILL.md（留空或传 SKILL.md 均可）。其他路径会被拒绝。",
        "en": "Optional. Only SKILL.md within the skill directory is supported (omit, or pass SKILL.md). "
              "Other paths are rejected.",
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

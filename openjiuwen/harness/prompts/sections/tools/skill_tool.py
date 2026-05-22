# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Bilingual description and input params for the Skill tool."""
from __future__ import annotations

from typing import Any, Dict

from openjiuwen.harness.prompts.sections.tools.base import (
    ToolMetadataProvider,
)

DESCRIPTION: Dict[str, str] = {
    "cn": "使用此工具查看特定技能的 SKILL.md 正文；可在注册表未收录时按 skills 根目录递归定位同名 skill。"
         "仅支持读取技能根目录下的 SKILL.md。默认返回目录树与已发现 skill 名列表；"
         "若不需要可显式传 include_directory_tree / include_discovered_skill_names 为 false。",
    "en": "View a skill's SKILL.md body; can resolve skills by recursive directory search when not in the registry. "
         "Only SKILL.md at the skill root is readable. "
         "By default includes a directory tree and discovered skill names; "
         "pass those flags as false to disable.",
}

SKILL_TOOL_PARAMS: Dict[str, Dict[str, str]] = {
    "skill_name": {
        "cn": "技能的名称",
        "en": "Name of the skill",
    },
    "relative_file_path": {
        "cn": "可选。仅支持读取技能根目录下的 SKILL.md（留空或传 SKILL / SKILL.md 均可）。其他路径会被拒绝。",
        "en": "Optional. Only SKILL.md at the skill root is supported (omit, or pass SKILL / SKILL.md). "
              "Other paths are rejected.",
    },
    "include_directory_tree": {
        "cn": "默认 true。为 false 时不返回 directory_tree。为 true 时返回技能根下的 ASCII 目录树（├──/└──），有深度与行数上限。",
        "en": "Defaults to true; set false to omit directory_tree. When true, returns an ASCII directory tree under the "
              "skill root (├── / └──), bounded by depth and line limits.",
    },
    "tree_max_depth": {
        "cn": "可选。directory_tree 的最大递归深度（仅当 include_directory_tree 为 true 时生效），默认 4，范围 1–12。",
        "en": "Optional. Max recursion depth for directory_tree when include_directory_tree is true; default 4; 1–12.",
    },
    "tree_max_entries": {
        "cn": "可选。directory_tree 最多多少行（含根目录行；仅当 include_directory_tree 为 true 时生效），默认 200，范围 20–800。",
        "en": "Optional. Max lines in directory_tree output including the root line when include_directory_tree is true; "
              "default 200; 20–800.",
    },
    "include_discovered_skill_names": {
        "cn": "默认 true。为 false 时不返回 discovered_skill_names。为 true 时在返回中附带各 skill 根下递归找到的、含 SKILL.md 的目录名（有上限）。",
        "en": "Defaults to true; set false to omit discovered_skill_names. When true, lists directory names under "
              "skill roots that contain SKILL.md (bounded).",
    },
    "max_discovered_skill_names": {
        "cn": "可选。discovered_skill_names 最多返回多少个名称（仅当 include_discovered_skill_names 为 true 时生效），默认 400。",
        "en": "Optional. Max names in discovered_skill_names when include_discovered_skill_names is true; default 400.",
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
            "include_directory_tree": {
                "type": "boolean",
                "description": p["include_directory_tree"].get(
                    language, p["include_directory_tree"]["cn"]
                ),
                "default": True,
            },
            "tree_max_depth": {
                "type": "integer",
                "description": p["tree_max_depth"].get(language, p["tree_max_depth"]["cn"]),
                "default": 4,
            },
            "tree_max_entries": {
                "type": "integer",
                "description": p["tree_max_entries"].get(language, p["tree_max_entries"]["cn"]),
                "default": 200,
            },
            "include_discovered_skill_names": {
                "type": "boolean",
                "description": p["include_discovered_skill_names"].get(
                    language, p["include_discovered_skill_names"]["cn"]
                ),
                "default": True,
            },
            "max_discovered_skill_names": {
                "type": "integer",
                "description": p["max_discovered_skill_names"].get(
                    language, p["max_discovered_skill_names"]["cn"]
                ),
                "default": 400,
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

# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Bilingual description and input params for the Skill tool."""
from __future__ import annotations

from typing import Any, Dict

from openjiuwen.harness.prompts.sections.tools.base import (
    ToolMetadataProvider,
)

DESCRIPTION: Dict[str, str] = {
    "cn": "使用此工具查看特定技能的 SKILL.md 正文。子skill必须使用含命名空间的目录（ parentskill/subskill ）。"
         "skill_name 含命名空间时，直接查询该目录下的 SKILL.md；"
         "skill_name 裸名时先查注册表，"
         "未匹配时按 skills 根目录递归查找全部同名 skill，"
         "skill_name 裸名时匹配多个 skill 返回 ambiguous 错误和冲突目录列表，"
         "从 error 中的冲突目录列表中选择一个目录重试。"
         "仅支持读取技能根目录下的 SKILL.md。默认返回目录树与已发现 skill 名列表；"
         "若不需要可显式传 include_directory_tree / include_discovered_skill_names 为 false。",
    "en": "View a skill's SKILL.md body. Sub-skills must use a namespaced directory path (parentskill/subskill). "
         "when skill_name includes a namespace, resolve SKILL.md directly under that directory; "
         "when skill_name is a bare name, check the registry first, "
         "then recursively find all same-name skills under skill roots if unmatched, "
         "when a bare name matches multiple skills, return an ambiguous error with a conflicting directory list,"
         "pick one directory from the conflicting directory list in the error and retry. "
         "Only SKILL.md at the skill root is readable. "
         "By default includes a directory tree and discovered skill names; "
         "pass those flags as false to disable.",
}

SKILL_TOOL_PARAMS: Dict[str, Dict[str, str]] = {
    "skill_name": {
        "cn": "技能名称。必须使用包含命名空间的目录（ parentskill/subskill ），"
              "裸名可从 error 里的冲突目录列表中选择一个目录，立即用该目录重调 skill_tool。",
        "en": "Skill name. Must use a namespaced directory path (parentskill/subskill), "
              "For a bare name, pick one directory from the conflicting directory list in the error "
              "and immediately re-call skill_tool with that path.",
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
        "cn": "默认 true。为 false 时不返回 discovered_skill_names。为 true 时在返回中附带各 skill 根下递归找到的、含 SKILL.md 的目录名。",
        "en": "Defaults to true; set false to omit discovered_skill_names. When true, lists relative paths under "
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

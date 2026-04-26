# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Bilingual description and input params for the skill_complete tool."""
from __future__ import annotations

from typing import Any, Dict

from openjiuwen.harness.prompts.sections.tools.base import (
    ToolMetadataProvider,
)

DESCRIPTION: Dict[str, str] = {
    "cn": (
        "标记某个技能已完成，并释放该技能下已加载的所有 SKILL.md / 引用文件正文。"
        "完成该技能的全部步骤、并且不再需要回看该技能正文时，立即调用本工具，"
        "比仅用自然语言宣告完成更优先。"
        "若之后又需要使用该技能，请重新调用 skill_tool 加载。"
        "本期不支持只释放部分 reference 文件——一次调用会释放该 skill 下全部已加载正文。"
    ),
    "en": (
        "Mark a skill as complete and release every loaded SKILL.md / reference body for that skill. "
        "Call this immediately after finishing all steps of a skill and no longer needing to consult its body; "
        "it takes priority over a plain natural-language 'I'm done'. "
        "If you later need the skill again, re-call skill_tool. "
        "This release is all-or-nothing per skill: partial reference retention is not supported in this iteration."
    ),
}

SKILL_COMPLETE_PARAMS: Dict[str, Dict[str, str]] = {
    "skill_name": {
        "cn": "已完成的技能名称",
        "en": "Name of the completed skill",
    },
}


def get_skill_complete_input_params(language: str = "cn") -> Dict[str, Any]:
    p = SKILL_COMPLETE_PARAMS
    return {
        "type": "object",
        "properties": {
            "skill_name": {
                "type": "string",
                "description": p["skill_name"].get(language, p["skill_name"]["cn"]),
            },
        },
        "required": ["skill_name"],
    }


class SkillCompleteMetadataProvider(ToolMetadataProvider):
    """skill_complete tool metadata provider."""

    def get_name(self) -> str:
        return "skill_complete"

    def get_description(self, language: str = "cn") -> str:
        return DESCRIPTION.get(language, DESCRIPTION["cn"])

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return get_skill_complete_input_params(language)

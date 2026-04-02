# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from typing import Any, Dict

from openjiuwen.harness.prompts.sections.tools.base import (
    ToolMetadataProvider,
)

DESCRIPTION: Dict[str, str] = {
    "cn": "中断执行并向用户请求输入",
    "en": "Interrupts the execution and requests input from the user",
}

ASK_USER_PARAMS: Dict[str, Dict[str, str]] = {
    "query": {
        "cn": "向用户展示的问题",
        "en": "The question to present to the user.",
    },
}


def get_ask_user_input_params(language: str = "cn") -> Dict[str, Any]:
    """Return the full JSON Schema for ask_user tool input_params."""
    p = ASK_USER_PARAMS
    return {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": p["query"].get(language, p["query"]["cn"])},
        },
        "required": ["query"],
    }


class AskUserMetadataProvider(ToolMetadataProvider):
    """AskUser 工具的元数据 provider。"""

    def get_name(self) -> str:
        return "ask_user"

    def get_description(self, language: str = "cn") -> str:
        return DESCRIPTION.get(language, DESCRIPTION["cn"])

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return get_ask_user_input_params(language)

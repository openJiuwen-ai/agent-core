# coding: utf-8
"""Bilingual description and input schema for the ``tool_call`` tool."""

from __future__ import annotations

from typing import Any, Dict

from openjiuwen.harness.prompts.tools.base import ToolMetadataProvider


DESCRIPTION: Dict[str, str] = {
    "cn": (
        "用当前 tool_search 结果的准确名称和符合 Schema 的参数执行工具。定义变更后重新查询，目录已移除的工具不可通过任何路径调用。"
    ),
    "en": (
        "Execute a current tool_search result with exact name/schema-compatible args. Re-search "
        "changed contracts; removed tools cannot run by any route."
    ),
}


TOOL_CALL_PARAMS: Dict[str, Dict[str, str]] = {
    "name": {
        "cn": "tool_search 返回的准确工具名称",
        "en": "Exact tool name returned by tool_search",
    },
    "args": {
        "cn": "按 tool_search 返回的 Schema 填写参数，无参数时用 {}。",
        "en": "Arguments matching the searched schema; {} for no-argument tools.",
    },
}


def get_tool_call_input_params(language: str = "cn") -> Dict[str, Any]:
    params = TOOL_CALL_PARAMS
    return {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": params["name"].get(language, params["name"]["cn"]),
            },
            "args": {
                "type": "object",
                "description": params["args"].get(language, params["args"]["cn"]),
                "additionalProperties": True,
            },
        },
        "required": ["name", "args"],
        "additionalProperties": False,
    }


class ToolCallMetadataProvider(ToolMetadataProvider):
    """Metadata provider used to build the model-visible ``tool_call`` card."""

    def get_name(self) -> str:
        return "tool_call"

    def get_description(self, language: str = "cn") -> str:
        return DESCRIPTION.get(language, DESCRIPTION["cn"])

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return get_tool_call_input_params(language)

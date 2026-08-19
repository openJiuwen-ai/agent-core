# coding: utf-8
"""Bilingual description and input schema for the ``tool_search`` tool."""

from __future__ import annotations

from typing import Any, Dict

from openjiuwen.harness.prompts.tools.base import ToolMetadataProvider


DESCRIPTION: Dict[str, str] = {
    "cn": (
        "搜索当前已注册但未直接展示的 deferred 工具。"
        "当用户需要的能力没有明确匹配的 direct 工具，或你不确定该使用哪个工具时，必须调用本工具。"
        "请根据用户意图和所需能力查询，不要猜测 deferred 工具名称；结果包含完整参数 schema，下一轮可以直接调用。"
    ),
    "en": (
        "Search registered deferred tools that are not directly exposed. "
        "You must call this tool when no direct tool clearly matches the user's required capability "
        "or when you are unsure which tool to use. Search by user intent and required capability, "
        "do not guess deferred tool names; results include complete schemas for the next direct call."
    ),
}


TOOL_SEARCH_PARAMS: Dict[str, Dict[str, str]] = {
    "query": {
        "cn": "用用户意图和所需能力描述查询，不要猜测 deferred 工具名称",
        "en": "Describe the user's intent and required capability; do not guess deferred tool names",
    },
    "limit": {
        "cn": "最多返回的工具数量",
        "en": "Maximum number of tools to return",
    },
}


def get_tool_search_input_params(language: str = "cn") -> Dict[str, Any]:
    params = TOOL_SEARCH_PARAMS
    return {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": params["query"].get(language, params["query"]["cn"]),
            },
            "limit": {
                "type": "integer",
                "description": params["limit"].get(language, params["limit"]["cn"]),
                "default": 5,
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    }


class ToolSearchMetadataProvider(ToolMetadataProvider):
    """Metadata provider used to build the model-visible ``tool_search`` card."""

    def get_name(self) -> str:
        return "tool_search"

    def get_description(self, language: str = "cn") -> str:
        return DESCRIPTION.get(language, DESCRIPTION["cn"])

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return get_tool_search_input_params(language)

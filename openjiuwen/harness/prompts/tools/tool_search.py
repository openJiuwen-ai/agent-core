# coding: utf-8
"""Bilingual description and input schema for the ``tool_search`` tool."""

from __future__ import annotations

from typing import Any, Dict

from openjiuwen.harness.prompts.tools.base import ToolMetadataProvider


DESCRIPTION: Dict[str, str] = {
    "cn": (
        "直接工具无法满足任务或不确定选哪个时，按用户意图和所需能力搜索当前可用的按需工具。返回准确名称与完整 Schema，下一轮用 tool_call "
        "执行。定义变更后重新查询，目录移除后旧结果和授权失效。"
    ),
    "en": (
        "When direct tools do not fit or selection is uncertain, BM25-search current deferred "
        "tools by intent and capability. Use the returned exact name and full schema with "
        "tool_call on the next turn. Re-search changed definitions. Removal invalidates old "
        "results and grants."
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

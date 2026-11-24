# coding: utf-8
"""Bilingual description and input schema for the ``tool_search`` tool."""

from __future__ import annotations

from typing import Any, Dict

from openjiuwen.harness.prompts.tools.base import ToolMetadataProvider


DESCRIPTION: Dict[str, str] = {
    "cn": (
        "根据用户意图搜索已注册且可用的 deferred 工具。"
        "当 direct 工具不能明确支持用户所需能力，或者你不确定应该使用哪个工具时，调用本工具。"
        "请使用用户意图和所需能力描述进行搜索，不要猜测工具名称。"
        "搜索使用 BM25 匹配工具名称、描述和参数信息。"
        "返回结果包含匹配工具的名称、描述和完整 parameters schema。"
        "搜索结果不会加入顶层 tools；要执行搜索结果，必须下一轮调用固定的 tool_call，并使用返回的准确工具名称和参数。"
        "只返回当前仍注册且可用的 deferred 工具。当前目录和当前注册表是判断当前能力的唯一依据；"
        "历史消息、历史成功结果，或其它工具/子代理描述中的名称不能恢复当前已删除或已修改的工具。"
        "工具未变化时，可以继续复用之前的搜索结果；如果工具描述或 schema 发生变化，应重新搜索；"
        "如果最新工具目录显示某工具已删除，不得使用该工具的旧搜索结果或旧授权，也不得通过 task_tool 或子代理间接调用。"
        "对于同一用户意图，不要重复调用相同或相近的搜索；如果返回结果不适用，不要猜测工具名称。"
    ),
    "en": (
        "BM25-search current deferred tools by intent/capability when direct tools do not fit or "
        "selection is uncertain. Returns exact names/full schemas; execute via tool_call next turn. "
        "Latest catalog/schema overrides history."
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

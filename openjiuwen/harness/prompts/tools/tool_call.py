# coding: utf-8
"""Bilingual description and input schema for the ``tool_call`` tool."""

from __future__ import annotations

from typing import Any, Dict

from openjiuwen.harness.prompts.tools.base import ToolMetadataProvider


DESCRIPTION: Dict[str, str] = {
    "cn": (
        "执行 tool_search 返回的 deferred 工具。"
        "name 必须使用最近一次 tool_search 返回的准确工具名称，args 必须符合该结果中的完整 parameters schema。"
        "搜索结果工具不会加入顶层 tools，不能直接调用搜索结果中的工具名称，也不能猜测工具名称。"
        "工具未变化时可以复用之前的搜索结果；如果工具已修改，应重新搜索获取最新 schema；"
        "如果当前目录后来显示该工具已删除，之前的搜索结果和授权立即失效，不能继续调用，也不能改用 task_tool 或子代理间接调用。"
    ),
    "en": (
        "Execute a deferred tool returned by tool_search. "
        "The name must exactly match a tool name returned by the latest tool_search, "
        "and args must conform to that result's complete parameters schema. "
        "Search-result tools are not added to the top-level tools list; do not call a result tool by its own name "
        "or guess a tool name. An unchanged tool may reuse a previous result; if a later "
        "directory update changes its schema, search again, and if it marks the tool as "
        "removed, the previous result and authorization are invalid; do not invoke it "
        "through task_tool or a subagent."
    ),
}


TOOL_CALL_PARAMS: Dict[str, Dict[str, str]] = {
    "name": {
        "cn": "tool_search 返回的准确工具名称",
        "en": "Exact tool name returned by tool_search",
    },
    "args": {
        "cn": "按照 tool_search 返回的完整 parameters schema 填写工具参数；无参数工具使用空对象",
        "en": (
            "Arguments matching the complete parameters schema returned by tool_search; "
            "use an empty object for no-argument tools"
        ),
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

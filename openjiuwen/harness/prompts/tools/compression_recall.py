"""Bilingual metadata for the compressed-context recall tool."""

from __future__ import annotations

from typing import Any, Dict

from openjiuwen.harness.prompts.tools.base import ToolMetadataProvider

_DESCRIPTIONS = {
    "cn": (
        "在当前 session 的压缩归档中检索。先匹配最相关的问答轮次（可能跨多个轮次），再在返回预算内"
        "返回相关原文片段。memory_id 来自 [[COMPRESSION_RECALL: id=...]] 标记；省略 memory_id 时"
        "跨本 session 的全部压缩归档搜索，结果附带每个片段所属的归档。"
    ),
    "en": (
        "Search compressed memory archives of the current session. It first selects the most relevant turns "
        "(possibly across multiple turns), then returns relevant source chunks within a return budget. "
        "memory_id comes from a [[COMPRESSION_RECALL: id=...]] marker; omit it to search across all "
        "compression archives of the session, with each chunk annotated by its source archive."
    ),
}


def _input_params(language: str) -> Dict[str, Any]:
    descriptions = {
        "cn": {
            "memory_id": "压缩摘要中 COMPRESSION_RECALL 标记所给出的记忆 ID；省略则跨全部归档搜索。",
            "query": "用于匹配原始问答轮次和内容片段的检索文本。",
        },
        "en": {
            "memory_id": "Memory ID from the COMPRESSION_RECALL marker; omit to search across all archives.",
            "query": "Text used to match the original turn and its source chunks.",
        },
    }
    selected = descriptions.get(language, descriptions["cn"])
    return {
        "type": "object",
        "properties": {
            "memory_id": {"type": "string", "description": selected["memory_id"]},
            "query": {"type": "string", "description": selected["query"]},
        },
        "required": ["query"],
    }


class CompressionRecallMetadataProvider(ToolMetadataProvider):
    """Provide name, description, and input schema for the recall tool."""

    def get_name(self) -> str:
        return "recall_compressed_context"

    def get_description(self, language: str = "cn") -> str:
        return _DESCRIPTIONS.get(language, _DESCRIPTIONS["cn"])

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return _input_params(language)

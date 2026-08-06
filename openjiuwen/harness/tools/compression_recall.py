"""Harness tool for session-isolated compressed-context recall."""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Dict, Optional

from openjiuwen.core.context_engine.processor.forked.compressor.recall import (
    CompressionRecallError,
    recall_compressed_context,
)
from openjiuwen.core.foundation.tool.base import Tool
from openjiuwen.harness.prompts.tools import build_tool_card
from openjiuwen.harness.tools.base_tool import ToolOutput

_MISS_HINTS = {
    "cn": (
        "未找到匹配的原文片段。建议换关键词重试：使用同义词、换另一种语言、或使用原文中的标识符"
        "（如函数名、文件路径、错误码）。也可以自行读取归档目录 {archive_path}："
        "其中 turns.jsonl 是轮次索引，chunks/ 目录保存压缩前的原文。"
    ),
    "en": (
        "No matching source chunks found. Try rephrasing the query: use synonyms, another language, or "
        "identifiers from the original text (such as function names, file paths, or error codes). You can "
        "also inspect the archive directory {archive_path} yourself: turns.jsonl is the turn index and "
        "chunks/ holds the original pre-compression text."
    ),
}


class CompressionRecallTool(Tool):
    """Tool that recalls compressed context chunks from the current session archive."""

    def __init__(self, workspace_dir: str, *, language: str = "cn", agent_id: Optional[str] = None):
        super().__init__(
            build_tool_card(
                "recall_compressed_context",
                "CompressionRecallTool",
                language,
                agent_id=agent_id,
            )
        )
        self._workspace_dir = workspace_dir
        self._language = language

    async def invoke(self, inputs: Dict[str, Any], **kwargs: Any) -> ToolOutput:
        memory_id = str(inputs.get("memory_id") or "").strip()
        query = str(inputs.get("query") or "").strip()
        session = kwargs.get("session")
        get_session_id = getattr(session, "get_session_id", None)
        if not callable(get_session_id):
            return ToolOutput(success=False, error="compression recall requires the current runtime session")
        session_id = str(get_session_id() or "")
        if not session_id:
            return ToolOutput(success=False, error="compression recall requires the current runtime session")
        try:
            result = await asyncio.to_thread(
                recall_compressed_context,
                workspace_dir=self._workspace_dir,
                session_id=session_id,
                memory_id=memory_id,
                query=query,
            )
        except CompressionRecallError as exc:
            return ToolOutput(success=False, error=str(exc))
        if not result.get("chunks"):
            hint_template = _MISS_HINTS.get(self._language, _MISS_HINTS["cn"])
            result["hint"] = hint_template.format(archive_path=result.get("archive_path") or "")
        return ToolOutput(success=True, data=result)

    async def stream(self, inputs: Dict[str, Any], **kwargs: Any) -> AsyncIterator[Any]:
        return
        yield  # pragma: no cover


__all__ = ["CompressionRecallTool"]

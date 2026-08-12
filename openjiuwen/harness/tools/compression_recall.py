"""Harness tool for session-isolated compressed-context recall."""

from __future__ import annotations

import asyncio
from pathlib import Path
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
        "未找到匹配的原文片段。建议：1) 换关键词重试——使用同义词、另一种语言、或原文中的标识符"
        "（如函数名、文件路径、错误码）；2) 省略 memory_id，跨本 session 的全部 {archive_count} 个压缩归档搜索"
        "（{archive_list}）；3) 自行浏览归档根目录 {recall_root}——每个归档内 turns.jsonl 是轮次索引，"
        "chunks/ 保存压缩前的原文。"
    ),
    "en": (
        "No matching source chunks found. Suggestions: 1) retry with different keywords — synonyms, another "
        "language, or identifiers from the original text (function names, file paths, error codes); 2) omit "
        "memory_id to search across all {archive_count} compression archives of this session ({archive_list}); "
        "3) browse the archive root {recall_root} yourself — turns.jsonl is the turn index and chunks/ holds "
        "the original pre-compression text."
    ),
}


def _build_miss_hint(result: Dict[str, Any], language: str) -> str:
    archives = result.get("archives_in_session") or []
    archive_list = ", ".join(str(item.get("memory_id") or "") for item in archives) or "-"
    template = _MISS_HINTS.get(language, _MISS_HINTS["cn"])
    return template.format(
        archive_count=len(archives),
        archive_list=archive_list,
        recall_root=str(result.get("recall_root") or result.get("archive_path") or ""),
    )


def _render_recall_content(result: Dict[str, Any], language: str) -> str:
    """Render the recall result as compact text for the model-facing tool message.

    The full structured dict stays in ``ToolOutput.data`` for programmatic
    consumers; ``content`` is what the model actually reads, so it carries only
    the recalled text plus pointers to the archive for anything left out. The
    recall root holds every compression archive of the session and directory
    names are designed as ``{timestamp}_{query head}`` so the model can browse
    other archives by time and topic on its own.
    """
    chunks = result.get("chunks") or []
    if not chunks:
        return str(result.get("hint") or "")
    archive_path = str(result.get("archive_path") or "")
    recall_root = str(result.get("recall_root") or (str(Path(archive_path).parent) if archive_path else ""))
    archive_count = len(result.get("archives_in_session") or [])
    returned_tokens = result.get("returned_tokens", 0)
    truncated = bool(result.get("truncated"))
    source = str(result.get("memory_id") or "")
    if language == "cn":
        header = (
            f"已从压缩归档 {source or '（跨全部归档搜索）'} 召回 {len(chunks)} 个原文片段"
            f"（约 {returned_tokens} tokens{'，已达上限，归档中还有未返回的片段' if truncated else ''}）。\n"
            f"本 session 共有 {archive_count} 个压缩归档；当前归档目录：{archive_path}"
            "（turns.jsonl 为轮次索引，chunks/ 为该次压缩的原文）\n"
            f"归档根目录：{recall_root} —— 本会话所有压缩落盘都在此目录下，"
            f"子目录名格式为 {{时间戳}}_{{query前10字符}}，需要其他压缩内容时可按时间和主题自行浏览查找"
        )
        chunk_tpl = "\n\n--- 片段 {i} | 归档 {memory_id} | {turn_id} | {chunk_id} | 相关度 {score:.2f} ---\n{body}"
    else:
        header = (
            f"Recalled {len(chunks)} source chunk(s) from compression archive "
            f"{source or '(searched across all archives)'} "
            f"(~{returned_tokens} tokens"
            f"{', budget reached, more chunks remain in the archive' if truncated else ''}).\n"
            f"This session has {archive_count} compression archive(s); current archive: {archive_path} "
            f"(turns.jsonl is the turn index, chunks/ holds this compression's source text)\n"
            f"Archive root: {recall_root} — all compressed archives of this session live here; "
            f"subdirectory names are {{timestamp}}_{{first 10 chars of query}}, so you can browse "
            f"other archives by time and topic yourself"
        )
        chunk_tpl = "\n\n--- chunk {i} | archive {memory_id} | {turn_id} | {chunk_id} | score {score:.2f} ---\n{body}"
    parts = [header]
    for index, chunk in enumerate(chunks, 1):
        parts.append(
            chunk_tpl.format(
                i=index,
                memory_id=chunk.get("memory_id", ""),
                turn_id=chunk.get("turn_id", ""),
                chunk_id=chunk.get("chunk_id", ""),
                score=float(chunk.get("score") or 0.0),
                body=str(chunk.get("content") or "").strip(),
            )
        )
    return "".join(parts)


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
                memory_id=memory_id or None,
                query=query,
            )
        except CompressionRecallError as exc:
            return ToolOutput(success=False, error=str(exc))
        if not result.get("chunks"):
            result["hint"] = _build_miss_hint(result, self._language)
        # data 保留完整结构化结果；模型看到的 ToolMessage 只含渲染后的 content
        return ToolOutput(
            success=True,
            data={**result, "content": _render_recall_content(result, self._language)},
        )

    async def stream(self, inputs: Dict[str, Any], **kwargs: Any) -> AsyncIterator[Any]:
        return
        yield  # pragma: no cover


__all__ = ["CompressionRecallTool"]

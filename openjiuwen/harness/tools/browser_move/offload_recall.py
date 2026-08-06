# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Session-scoped recall for browser tool results persisted by context windowing."""

from __future__ import annotations

import asyncio
import json
import re
from os import PathLike
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncIterator, Dict, Optional

from openjiuwen.core.foundation.tool import Tool, ToolCard
from openjiuwen.harness.tools.base_tool import ToolOutput

if TYPE_CHECKING:
    from openjiuwen.harness.workspace.workspace import Workspace


_HANDLE_PATTERN = re.compile(r"^[0-9a-fA-F]{32}$")
_SESSION_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")
_OFFLOAD_FILE_PREFIX = "ToolResultWindowProcessor"
_DEFAULT_LIMIT = 8000
_MAX_LIMIT = 16000
_MAX_QUERY_LENGTH = 256
_MAX_OFFLOAD_FILE_SIZE = 8 * 1024 * 1024
_QUERY_CONTEXT_CHARS = 300

_RECALL_DESC_EN = (
    "Recall a bounded text fragment from an older browser probe or snapshot result when a "
    "<persisted-output> marker's preview is insufficient. Pass only its 32-character handle. "
    "The tool can read only ToolResultWindowProcessor output owned by the current browser session; "
    "it cannot read arbitrary files. Use query to find the first matching fragment at or after "
    "offset, or omit query to read a chunk. Recalled refs and selectors are stale evidence after "
    "navigation and must never be used for browser interaction."
)
_RECALL_DESC_CN = (
    "当旧浏览器 probe 或 snapshot 的 <persisted-output> 预览不足时，从落盘结果中恢复一段受限文本。"
    "只传入标记里的 32 位 handle。此工具只能读取当前 Browser session 所属的 "
    "ToolResultWindowProcessor 结果，不能读取任意文件。可用 query 从 offset 开始定位第一处匹配，"
    "不传 query 时按 offset 分块读取。页面导航后，恢复结果中的 ref 和 selector 已过期，"
    "只能作为文本证据，禁止用于浏览器交互。"
)
_RECALL_PARAMS: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "handle": {
            "type": "string",
            "description": "The 32-character handle from a persisted browser output marker.",
        },
        "query": {
            "type": "string",
            "description": "Optional case-insensitive text to locate inside the original result.",
        },
        "offset": {
            "type": "integer",
            "description": "Character offset for chunking or the query search start. Default 0.",
            "minimum": 0,
        },
        "limit": {
            "type": "integer",
            "description": "Maximum returned characters. Default 8000, hard-capped at 16000.",
            "minimum": 1,
            "maximum": _MAX_LIMIT,
        },
    },
    "required": ["handle"],
}


def _workspace_root(workspace: Optional[str | PathLike[str] | "Workspace"]) -> Path:
    root_path = getattr(workspace, "root_path", None) if workspace is not None else None
    if root_path is None:
        root_path = workspace or "./"
    return Path(root_path).resolve()


class BrowserOffloadRecallTool(Tool):
    """Read only this browser session's window-processor artifacts."""

    def __init__(
        self,
        workspace: Optional[str | PathLike[str] | "Workspace"],
        *,
        language: str = "cn",
    ) -> None:
        super().__init__(
            ToolCard(
                name="browser_recall_offload",
                description=_RECALL_DESC_EN if language == "en" else _RECALL_DESC_CN,
                input_params=_RECALL_PARAMS,
            )
        )
        self._workspace_root = _workspace_root(workspace)

    async def invoke(self, inputs: Dict[str, Any], **kwargs: Any) -> ToolOutput:
        try:
            handle = self._validated_handle(inputs)
            session_id = self._validated_session_id(kwargs.get("session"))
            query, offset, limit = self._validated_slice(inputs)
        except (TypeError, ValueError) as exc:
            return ToolOutput(success=False, error=str(exc))

        try:
            data = await asyncio.to_thread(
                self._recall,
                session_id=session_id,
                handle=handle,
                query=query,
                offset=offset,
                limit=limit,
            )
        except (OSError, ValueError) as exc:
            return ToolOutput(success=False, error=str(exc))
        return ToolOutput(success=True, data=data)

    @staticmethod
    def _validated_handle(inputs: Dict[str, Any]) -> str:
        handle = str(inputs.get("handle") or "").strip()
        if not _HANDLE_PATTERN.fullmatch(handle):
            raise ValueError("handle must be exactly 32 hexadecimal characters")
        return handle.lower()

    @staticmethod
    def _validated_session_id(session: Any) -> str:
        get_session_id = getattr(session, "get_session_id", None)
        if not callable(get_session_id):
            raise ValueError("browser offload recall requires the current runtime session")
        session_id = str(get_session_id() or "").strip()
        if (
            not session_id
            or not _SESSION_PATTERN.fullmatch(session_id)
            or session_id in {".", ".."}
        ):
            raise ValueError("invalid browser runtime session")
        return session_id

    @staticmethod
    def _validated_slice(inputs: Dict[str, Any]) -> tuple[str, int, int]:
        try:
            offset = max(0, int(inputs.get("offset", 0)))
            limit = max(1, min(_MAX_LIMIT, int(inputs.get("limit", _DEFAULT_LIMIT))))
        except (TypeError, ValueError) as exc:
            raise ValueError("offset and limit must be integers") from exc
        query = str(inputs.get("query") or "").strip()
        if len(query) > _MAX_QUERY_LENGTH:
            raise ValueError(f"query must not exceed {_MAX_QUERY_LENGTH} characters")
        return query, offset, limit

    def _recall(
        self,
        *,
        session_id: str,
        handle: str,
        query: str,
        offset: int,
        limit: int,
    ) -> Dict[str, Any]:
        message = self._load_message(session_id, handle)
        content = message.get("content")
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False, separators=(",", ":"))
        return self._build_fragment(
            message=message,
            content=content,
            handle=handle,
            query=query,
            offset=offset,
            limit=limit,
        )

    def _load_message(self, session_id: str, handle: str) -> Dict[str, Any]:
        offload_dir = (
            self._workspace_root
            / "context"
            / f"{session_id}_context"
            / "offload"
        ).resolve()
        if not offload_dir.is_relative_to(self._workspace_root):
            raise ValueError("browser offload directory escapes the configured workspace")

        candidate_path = offload_dir / f"{_OFFLOAD_FILE_PREFIX}_{handle}.json"
        if candidate_path.is_symlink():
            raise ValueError("browser offload handle was not found in the current session")
        offload_path = candidate_path.resolve()
        if offload_path.parent != offload_dir:
            raise ValueError("browser offload path is outside the current session")
        if not offload_path.is_file():
            raise ValueError("browser offload handle was not found in the current session")
        if offload_path.stat().st_size > _MAX_OFFLOAD_FILE_SIZE:
            raise ValueError("browser offload artifact exceeds the safe recall size")

        payload = json.loads(offload_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("browser offload artifact has an invalid payload")
        if payload.get("offload_handle") != handle:
            raise ValueError("browser offload handle does not match the stored artifact")
        messages = payload.get("messages")
        if not isinstance(messages, list) or not messages or not isinstance(messages[0], dict):
            raise ValueError("browser offload artifact does not contain a tool result")
        return messages[0]

    @staticmethod
    def _build_fragment(
        *,
        message: Dict[str, Any],
        content: str,
        handle: str,
        query: str,
        offset: int,
        limit: int,
    ) -> Dict[str, Any]:
        match_offset: Optional[int] = None
        fragment_offset = min(offset, len(content))
        if query:
            match_offset = content.casefold().find(query.casefold(), fragment_offset)
            if match_offset < 0:
                return {
                    "handle": handle,
                    "found": False,
                    "query": query,
                    "original_size": len(content),
                    "stale_interaction_refs": True,
                    "content": "",
                }
            fragment_offset = max(0, match_offset - _QUERY_CONTEXT_CHARS)

        fragment = content[fragment_offset:fragment_offset + limit]
        next_offset = fragment_offset + len(fragment)
        return {
            "handle": handle,
            "found": True,
            "query": query or None,
            "match_offset": match_offset,
            "tool_name": message.get("name"),
            "original_size": len(content),
            "offset": fragment_offset,
            "returned_chars": len(fragment),
            "has_more": next_offset < len(content),
            "next_offset": next_offset if next_offset < len(content) else None,
            "stale_interaction_refs": True,
            "content": fragment,
        }

    async def stream(self, inputs: Dict[str, Any], **kwargs: Any) -> AsyncIterator[Any]:
        del inputs, kwargs
        return
        yield  # pragma: no cover


__all__ = ["BrowserOffloadRecallTool"]

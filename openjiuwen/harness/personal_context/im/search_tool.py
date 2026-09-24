"""Read-only IM search tool for agent consumption (OJ-06).

Depends only on the storage-agnostic ``ImSearchPort`` (decision D10); the
host assembles it as ``ImSearchTool(SqliteImSearchStore(home))`` (OJ-10).
Tool inputs are LLM-facing: ``since``/``until`` accept ISO 8601 strings or
relative expressions (``7d`` / ``24h`` / ``30m``) instead of epoch
milliseconds, because raw ms values are error-prone for models.
"""

from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime
from typing import Any, AsyncIterator, Dict, Optional

from openjiuwen.core.foundation.tool.base import Tool
from openjiuwen.harness.personal_context.im.search import (
    ImSearchHit,
    ImSearchPort,
    ImSearchQuery,
)
from openjiuwen.harness.prompts.tools import build_tool_card
from openjiuwen.harness.tools.base_tool import ToolOutput

_RELATIVE_RE = re.compile(r"^(\d+)\s*([dhm])$", re.IGNORECASE)
_UNIT_SECONDS = {"d": 86_400, "h": 3_600, "m": 60}
_TIME_FORMAT_HINT = "ISO 8601 (e.g. 2026-09-01 or 2026-09-01T10:00:00) or relative (e.g. 7d, 24h, 30m)"


def _parse_time_to_ms(value: str, *, now_ms: Optional[int] = None) -> Optional[int]:
    """Parse ISO 8601 or a relative expression into epoch ms; None on failure."""
    text = value.strip()
    if not text:
        return None
    match = _RELATIVE_RE.match(text)
    if match:
        amount = int(match.group(1))
        unit = match.group(2).lower()
        base = now_ms if now_ms is not None else int(time.time() * 1000)
        return base - amount * _UNIT_SECONDS[unit] * 1000
    try:
        parsed = datetime.fromisoformat(text)  # 'Z' suffix supported on 3.11+
    except ValueError:
        return None
    if parsed.tzinfo is None:
        # Agent-facing times default to the host's local timezone.
        parsed = parsed.astimezone()
    return int(parsed.timestamp() * 1000)


def _hit_to_dict(hit: ImSearchHit) -> Dict[str, Any]:
    return {
        "message_id": hit.message_id,
        "channel_id": hit.channel_id,
        "conversation_id": hit.conversation_id,
        "conversation_title": hit.conversation_title,
        "sender_account": hit.sender_account,
        "sender_name": hit.sender_name,
        "is_self": hit.is_self,
        "sent_at": hit.sent_at,
        "content_text": hit.content_text,
    }


class ImSearchTool(Tool):
    """Search original IM messages within the learning scope (read-only)."""

    def __init__(self, search: ImSearchPort, language: str = "cn", agent_id: Optional[str] = None):
        super().__init__(build_tool_card("im_search", "ImSearchTool", language, agent_id=agent_id))
        self._search = search

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        keyword = inputs.get("keyword")
        if not isinstance(keyword, str) or not keyword.strip():
            return ToolOutput(success=False, error="keyword is required")

        since_ms, error = self._parse_bound(inputs.get("since"), "since")
        if error is not None:
            return ToolOutput(success=False, error=error)
        until_ms, error = self._parse_bound(inputs.get("until"), "until")
        if error is not None:
            return ToolOutput(success=False, error=error)

        conversation = inputs.get("conversation")
        refs = (conversation.strip(),) if isinstance(conversation, str) and conversation.strip() else ()
        sender = inputs.get("sender")
        sender_value = sender.strip() if isinstance(sender, str) and sender.strip() else None

        try:
            limit = max(1, min(50, int(inputs.get("limit", 20))))
            offset = max(0, int(inputs.get("offset", 0)))
        except (TypeError, ValueError):
            return ToolOutput(success=False, error="limit/offset must be integers")

        query = ImSearchQuery(
            keyword=keyword.strip(),
            conversation_refs=refs,
            sender=sender_value,
            since_ms=since_ms,
            until_ms=until_ms,
            limit=limit,
            offset=offset,
        )
        try:
            hits, total, truncated = await asyncio.to_thread(self._search.search, query)
        except Exception as exc:  # noqa: BLE001 - tool boundary reports, never raises
            return ToolOutput(success=False, error=str(exc))
        return ToolOutput(
            success=True,
            data={"total": total, "truncated": truncated, "hits": [_hit_to_dict(hit) for hit in hits]},
        )

    async def stream(self, inputs: Dict[str, Any], **kwargs) -> AsyncIterator[Any]:
        pass

    @staticmethod
    def _parse_bound(raw: Any, field: str) -> tuple[Optional[int], Optional[str]]:
        if raw is None:
            return None, None
        if not isinstance(raw, str) or not raw.strip():
            return None, f"invalid {field}: expected {_TIME_FORMAT_HINT}"
        parsed = _parse_time_to_ms(raw)
        if parsed is None:
            return None, f"invalid {field}: {raw!r}, expected {_TIME_FORMAT_HINT}"
        return parsed, None


__all__ = ["ImSearchTool"]

# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""read_execution_record (PA-18): page through the full log summary of an Execution this continuation traced.

The tool is declared for every Execution of a Spec that allows continuation, so tool definitions stay the
same throughout a Request. It answers only inside a continuation, and only for the Executions the
continuation reached through verified links (F_20 D-08). Every call checks and associates the whole log
again (PA-17) and only then pages the full summary, so a result outside the page is never read as missing.
A cursor names the Execution, the summary version and a character position inside one item.
"""

import base64
import binascii
import hashlib
import json
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from openjiuwen.core.foundation.tool import Tool, ToolCard
from openjiuwen.core.foundation.tool.schema import ToolOutput
from openjiuwen.harness.rails.base import DeepAgentRail

from .execution_log import RECORD_TOOL, record_blocks

if TYPE_CHECKING:
    from ..schema.build_context import PersistentBuildContext

# Largest tool result, in characters of the returned JSON: text, cursor, status and every other field.
PAGE_MAX_CHARS = 6000
_CURSOR_VERSION = 1
_RESET_NOTICE = "记录已变化，已从头返回"
_DESCRIPTION = (
    "分页读取本次续做所接续执行的完整执行记录（续做材料中执行链列出的执行）。只在续做时可用。"
    "第一次只填 execution_id；之后把上一页返回的 next_cursor 原样填入 cursor 继续读，next_cursor 为空表示已读完。"
    "每页的 segments 按条目给出片段：同一 item 的片段按顺序拼接即为该条目原文，各条目以换行连接即为完整记录。"
    "reset 为 true 表示记录在两次读取之间变化，本页是新记录的第一页，不能接在旧记录之后。"
)
_INPUT_PARAMS = {
    "type": "object",
    "properties": {
        "execution_id": {"type": "string", "description": "要读取的执行 ID，必须在续做材料的执行链中"},
        "cursor": {"type": "string", "description": "上一页返回的 next_cursor；从头读时不填"},
    },
    "required": ["execution_id"],
}
_FAILURES = {
    "invalid_arguments": "参数无效：execution_id 必须是字符串，cursor 不填或为上一页返回的字符串",
    "unavailable": "只有续做执行可以读取执行记录，且只能读取本次续做已核验的执行链",
    "not_in_chain": "该执行不在本次续做已核验的执行链中",
    "invalid_cursor": "游标无效：不是本工具返回的游标",
    "cursor_target": "游标不属于该执行",
    "cursor_position": "游标位置不存在",
    "page_too_small": "单页容量不足以返回有效片段",
}


class PageError(Exception):
    """Why a page cannot be returned; the status names one of _FAILURES."""

    def __init__(self, status: str):
        super().__init__(status)
        self.status = status


def _failure(status: str) -> ToolOutput:
    return ToolOutput(success=False, data={"status": status}, error=_FAILURES[status])


def _version(blocks: tuple[str, ...]) -> str:
    """Identifies the full summary a cursor was made for."""
    return hashlib.sha256("\n".join(blocks).encode("utf-8")).hexdigest()[:32]


def encode_cursor(execution_id: str, version: str, item: int, offset: int) -> str:
    """An opaque cursor for one position in one Execution's full summary."""
    raw = json.dumps([_CURSOR_VERSION, execution_id, version, item, offset], separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def _decode_cursor(cursor: str) -> tuple[str, str, int, int]:
    try:
        data = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8"))
    except (binascii.Error, UnicodeError, ValueError):
        raise PageError("invalid_cursor") from None
    if not (
        isinstance(data, list)
        and len(data) == 5
        and data[0] == _CURSOR_VERSION
        and all(isinstance(value, str) for value in data[1:3])
        and all(isinstance(value, int) and not isinstance(value, bool) and value >= 0 for value in data[3:])
    ):
        raise PageError("invalid_cursor")
    return data[1], data[2], data[3], data[4]


def _segment(item: int, text: str, offset: int, size: int) -> dict:
    """The part of one item a page carries; parts of the same item join back into it."""
    return {
        "item": item,
        "from": offset,
        "to": offset + size,
        "length": len(text),
        "text": text[offset : offset + size],
    }


def _longest_fit(fits, item: int, text: str, offset: int) -> int:
    """The most characters of the item from ``offset`` that still fit on the page; 0 when none does."""
    low, high = 0, len(text) - offset
    while low < high:
        middle = (low + high + 1) // 2
        if fits(_segment(item, text, offset, middle)):
            low = middle
        else:
            high = middle - 1
    return low


def _start(execution_id: str, blocks: tuple[str, ...], version: str, cursor: str | None) -> tuple[int, int, bool]:
    """Where a page starts, and whether the record changed since the cursor was made."""
    if cursor is None:
        return 0, 0, False
    target, made_for, item, offset = _decode_cursor(cursor)
    if target != execution_id:
        raise PageError("cursor_target")
    if made_for != version:
        return 0, 0, True
    if item >= len(blocks) or offset >= max(len(blocks[item]), 1):
        raise PageError("cursor_position")
    return item, offset, False


def page(execution_id: str, blocks: tuple[str, ...], cursor: str | None, max_chars: int = PAGE_MAX_CHARS) -> dict:
    """One page of the full summary from the cursor, measured as the whole returned JSON.

    A changed summary restarts from its first page with ``reset``; a malformed cursor, another
    Execution's cursor or a position that does not exist is refused. Every page advances; the last
    one has no next cursor.
    """
    version = _version(blocks)
    item, offset, reset = _start(execution_id, blocks, version, cursor)
    result: dict[str, Any] = {"status": "ok", "execution_id": execution_id, "reset": reset}
    if reset:
        result["notice"] = _RESET_NOTICE
    segments: list[dict] = []
    # No cursor of this record is longer than one with the largest item index and offset, so a page that
    # fits with it fits with its real next cursor.
    longest_cursor = encode_cursor(execution_id, version, len(blocks), max(len(block) for block in blocks))

    def fits(extra: dict) -> bool:
        trial = {**result, "segments": [*segments, extra], "next_cursor": longest_cursor}
        return len(json.dumps(trial, ensure_ascii=False)) <= max_chars

    while item < len(blocks):
        text = blocks[item]
        whole = _segment(item, text, offset, len(text) - offset)
        if fits(whole):
            segments.append(whole)
            item, offset = item + 1, 0
            continue
        size = _longest_fit(fits, item, text, offset)
        if size == 0 and not segments:
            raise PageError("page_too_small")
        if size:
            segments.append(_segment(item, text, offset, size))
            offset += size
        break
    result["segments"] = segments
    result["next_cursor"] = encode_cursor(execution_id, version, item, offset) if item < len(blocks) else None
    return result


def _arguments(inputs: Any) -> tuple[str, str | None] | None:
    if not isinstance(inputs, Mapping):
        return None
    execution_id, cursor = inputs.get("execution_id"), inputs.get("cursor")
    if not isinstance(execution_id, str) or not execution_id or not (cursor is None or isinstance(cursor, str)):
        return None
    return execution_id, cursor or None


class ExecutionRecordTool(Tool):
    """Read-only; answers only for the Executions this continuation verified."""

    def __init__(self, context: "PersistentBuildContext") -> None:
        super().__init__(
            ToolCard(
                id=f"PersistentExecutionRecordTool_{uuid4().hex}",
                name=RECORD_TOOL,
                description=_DESCRIPTION,
                input_params=_INPUT_PARAMS,
                idempotent=True,
            )
        )
        self._context = context

    async def invoke(self, inputs: Any, **kwargs: Any) -> Any:
        """Keep the core Tool boundary permissive; the internal reader has a concrete result type."""
        return await self._read(inputs)

    async def _read(self, inputs: Any) -> ToolOutput:
        arguments = _arguments(inputs)
        if arguments is None:
            return _failure("invalid_arguments")
        execution_id, cursor = arguments
        chain = self._context.binding.control.verified_chain
        if not chain:
            return _failure("unavailable")
        if execution_id not in chain:
            return _failure("not_in_chain")
        blocks, _missing = await record_blocks(self._context, execution_id)
        try:
            result = page(execution_id, blocks, cursor)
        except PageError as error:
            return _failure(error.status)
        return ToolOutput(success=True, data={"content": json.dumps(result, ensure_ascii=False)})

    async def stream(self, inputs: Any, **kwargs: Any) -> Any:
        """Expose the same read for callers that use the Tool stream API."""
        yield await self._read(inputs)


class ExecutionRecordRail(DeepAgentRail):
    """Declare read_execution_record for every Execution of a Spec that allows continuation."""

    priority = 100

    def __init__(self, context: "PersistentBuildContext") -> None:
        super().__init__()
        self.context = context
        self.tools: list[ExecutionRecordTool] = []

    def init(self, agent) -> None:
        if self.tools:
            return
        tool = ExecutionRecordTool(self.context)
        self.tools = [tool]
        agent.ability_manager.add_ability(tool.card, tool)

    def uninit(self, agent) -> None:
        for tool in self.tools:
            if hasattr(agent, "ability_manager") and agent.ability_manager.get(tool.card.name) is tool.card:
                agent.ability_manager.remove_ability(tool.card.name)
        self.tools = []


__all__ = ["PAGE_MAX_CHARS", "ExecutionRecordRail", "ExecutionRecordTool", "PageError", "encode_cursor", "page"]

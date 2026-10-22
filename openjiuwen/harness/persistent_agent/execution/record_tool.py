# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""read_execution_record (PA-18): page through the events an Execution of this continuation's chain saved.

The tool is declared for every Execution of a Spec that allows continuation, so tool definitions stay the
same throughout a Request. It answers only inside a continuation, and only from the log snapshots that
continuation took of the Executions it reached (F_20 D-08): the same reads the injected preview came from,
never repeated, so events saved later are not seen. The record is the header as line 0, then the events by
local_seq; a page holds whole lines, or one part of a line too long for a page, the header included.
``after_seq`` is the last line read in full and is left out until the header is; ``offset`` > 0 is the
character position reached inside the next line.
"""

import json
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from openjiuwen.core.foundation.tool import Tool, ToolCard
from openjiuwen.core.foundation.tool.schema import ToolOutput
from openjiuwen.harness.rails.base import DeepAgentRail

from .execution_log import ENCODING, RECORD_TOOL, LogSnapshot, record_lines

if TYPE_CHECKING:
    from ..schema.build_context import PersistentBuildContext

# Largest tool result, in characters of the returned JSON: header, events, part, position and the rest.
PAGE_MAX_CHARS = 6000
_DESCRIPTION = (
    "读取本次续做所接续执行的完整执行记录（续做材料中执行链列出的执行），只在续做时可用。"
    "记录是程序原样转出的事件，与续做材料来自同一次读取，不含之后才保存的事件。"
    "第一次只填 execution_id；之后把上一页 next 中的字段原样填入（还没读完记录说明时 next 只有 offset），"
    "next 为空表示已读完。log 是该执行的记录说明；events 中每项是一条完整事件。记录说明或单条事件超过一页时放在 "
    "part 中分段返回（local_seq 为 0 的分段是记录说明）：同一 local_seq 的各段 text 按 from 顺序拼接，即为它的 JSON。"
)
_INPUT_PARAMS = {
    "type": "object",
    "properties": {
        "execution_id": {"type": "string", "description": "要读取的执行 ID，必须在续做材料的执行链中"},
        "after_seq": {"type": "integer", "minimum": 0, "description": "上一页 next.after_seq；next 中没有时不填"},
        "offset": {"type": "integer", "minimum": 0, "description": "上一页 next.offset；从头读时不填"},
    },
    "required": ["execution_id"],
    "additionalProperties": False,
}
_ARGUMENTS = frozenset(("execution_id", "after_seq", "offset"))
_FAILURES = {
    "invalid_arguments": (
        "参数无效：只接受 execution_id、after_seq、offset（不再支持 cursor）；execution_id 必须是字符串，"
        "after_seq 与 offset 不填或为非负整数，不能为 null"
    ),
    "unavailable": "只有续做执行可以读取执行记录，且只能读取本次续做已核验的执行链",
    "not_in_chain": "该执行不在本次续做已核验的执行链中",
    "cursor_position": "位置不存在：offset 只用于 after_seq 之后的下一行，且须小于该行的长度",
    "page_too_small": "单页容量不足以返回有效片段",
}


class PageError(Exception):
    """Why a page cannot be returned; the status names one of _FAILURES."""

    def __init__(self, status: str):
        super().__init__(status)
        self.status = status


def _failure(status: str) -> ToolOutput:
    return ToolOutput(success=False, data={"status": status}, error=_FAILURES[status])


def _part(seq: int, line: str, start: int, size: int) -> dict:
    """The piece of one event's JSON line a page carries; pieces of the same event join back into it."""
    return {
        "local_seq": seq,
        "from": start,
        "to": start + size,
        "length": len(line),
        "text": line[start : start + size],
    }


def page(snapshot: LogSnapshot, after_seq: int | None = None, offset: int = 0, max_chars: int = PAGE_MAX_CHARS) -> dict:
    """One page of the record after ``after_seq`` (and ``offset`` inside the next line), sized as JSON.

    Without ``after_seq`` the page starts at the header. A position past the last line, or an offset outside
    the next line, is refused; every other page advances, and the last one has no next position.
    """
    rows = record_lines(snapshot)
    # The header is line 0, so a page without after_seq starts at it.
    index = next((number for number, (seq, _) in enumerate(rows) if after_seq is None or seq > after_seq), len(rows))
    if offset and (index == len(rows) or offset >= len(rows[index][1])):
        raise PageError("cursor_position")
    result: dict[str, Any] = {"status": "ok", "execution_id": snapshot.execution_id, "encoding": ENCODING}
    # No next position of this snapshot is longer than this one, so a page that fits with it fits.
    widest = {"after_seq": rows[-1][0], "offset": max(len(line) for _, line in rows)}
    events: list[Any] = []

    def fits(part: dict | None = None) -> bool:
        trial = {**result, "events": events, **({"part": part} if part else {}), "next": widest}
        return len(json.dumps(trial, ensure_ascii=False)) <= max_chars

    last, position = after_seq, offset
    while index < len(rows):
        seq, line = rows[index]
        if position == 0:
            _add(result, events, seq, line)
            if fits():
                last, index = seq, index + 1
                continue
            _remove(result, events, seq)
            if events or "log" in result:
                break
        size = _longest_part(fits, seq, line, position)
        if size == 0:
            raise PageError("page_too_small")
        result["part"] = _part(seq, line, position, size)
        position += size
        if position == len(line):
            last, index, position = seq, index + 1, 0
        break
    result["events"] = events
    if index == len(rows):
        result["next"] = None
    else:
        result["next"] = {"offset": position} if last is None else {"after_seq": last, "offset": position}
    return result


def _add(result: dict, events: list, seq: int, line: str) -> None:
    """A whole line on the page: the header as ``log``, an event in ``events``."""
    if seq == 0:
        result["log"] = json.loads(line)
    else:
        events.append(json.loads(line))


def _remove(result: dict, events: list, seq: int) -> None:
    if seq == 0:
        del result["log"]
    else:
        events.pop()


def _longest_part(fits, seq: int, line: str, start: int) -> int:
    """The most characters of the line from ``start`` that still fit on the page; 0 when none does."""
    low, high = 0, len(line) - start
    while low < high:
        middle = (low + high + 1) // 2
        if fits(_part(seq, line, start, middle)):
            low = middle
        else:
            high = middle - 1
    return low


_INVALID = object()


def _position(inputs: Mapping, name: str, absent: Any) -> Any:
    """A declared position: ``absent`` when left out; a non-negative integer when given; null is not one."""
    if name not in inputs:
        return absent
    value = inputs[name]
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return _INVALID


def _arguments(inputs: Any) -> tuple[str, int | None, int] | None:
    """Only the declared parameters; an old ``cursor`` or any other name is refused, not ignored."""
    if not isinstance(inputs, Mapping) or not set(inputs) <= _ARGUMENTS:
        return None
    execution_id = inputs.get("execution_id")
    after_seq, offset = _position(inputs, "after_seq", None), _position(inputs, "offset", 0)
    if not isinstance(execution_id, str) or not execution_id or _INVALID in (after_seq, offset):
        return None
    return execution_id, after_seq, offset


class ExecutionRecordTool(Tool):
    """Read-only; answers only from the snapshots this continuation took."""

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
        execution_id, after_seq, offset = arguments
        snapshots = self._context.binding.control.log_snapshots
        if not snapshots:
            return _failure("unavailable")
        if execution_id not in snapshots:
            return _failure("not_in_chain")
        try:
            result = page(snapshots[execution_id], after_seq, offset)
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


__all__ = ["PAGE_MAX_CHARS", "ExecutionRecordRail", "ExecutionRecordTool", "PageError", "page"]

# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Execution log summary (PA-17): read one earlier Execution's saved events and summarize them for the Agent.

Only reads and summarizes: no model call, no window change, no event. When and how the summary is used
for a continuation belongs to PA-18. ``detail="brief"`` keeps every write, outcome and gap but counts
read-only calls and leaves candidate bodies out; the full summary stays readable page by page through
the continuation's record tool (``record_blocks``).
"""

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import TypeAdapter, ValidationError

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import logger

from ..schema.build_context import PersistentBuildContext
from ..schema.events import AgentEvent, EventType
from ..schema.protocol import Identifier
from ..tools.effect_policy import is_read_only_tool_name
from .handoff import SHARE_FILE

# The tool a continuation reads full records with (execution/record_tool.py).
RECORD_TOOL = "read_execution_record"
_IDENTITY = ("namespace_id", "agent_id", "session_id", "request_id")
_DETAILS = ("full", "brief")
_EXECUTION_ID = TypeAdapter(Identifier)
_ENDINGS = frozenset({EventType.EXECUTION_PAUSED, EventType.EXECUTION_FINAL})
_LABELS = {"succeeded": "成功", "failed": "失败", "unknown": "结果不明"}
_FINAL_LABELS = {"completed": "已完成", "ignored": "已忽略", "cancelled": "已取消", "failed": "失败"}
_WRITE_HINTS = {
    None: "：结果不明，先查证，不能直接重做",
    "unknown": "：先查证，不能直接重做",
    "failed": "：失败不代表没有副作用，必要时查证",
}
_INDENT = "    "
_EMPTY = "log is empty"
_MISSING = (
    "上一次记录缺失：没有取得可用的执行日志。不能据此认为上一次没有执行；"
    "涉及写操作先查证实际效果，不能直接重做；查不清时可以询问发起人，是否询问由模型判断。"
)
_NO_ENDING = "未记录结束；无法仅凭日志确定执行是否已结束。"
_NOTHING = "日志中没有工具、结论、候选或换窗记录。"
_ARCHIVE_NOTE = "仅为日志中的归档引用，尚未按 execution_id 查询，可能不全"
_OMITTED = f"本摘要省略了只读操作的明细与候选正文，完整记录可用 {RECORD_TOOL} 按执行 ID 分页查询。"
_FOOTER = (
    "说明：以上由程序从执行日志整理；即使序号连续且有结束记录，也不代表日志完整。"
    "结果不明、没有结果或失败的写操作先查证实际效果，不能直接重做；"
    "查不清时可以询问发起人，是否询问由模型判断。"
)


@dataclass(frozen=True)
class ExecutionLogSummary:
    """The summary text for the Agent; ``missing`` means no usable log was obtained, not that nothing ran."""

    text: str
    missing: bool


@dataclass
class _Tool:
    """One tool call as the log shows it: an intent, a result, or both when they match uniquely."""

    seq: int
    name: str
    args: str
    operation_id: str | None
    call_id: str
    write: bool
    target: str | None = None
    status: str | None = None
    result: str | None = None
    refs: tuple[str, ...] = ()
    reason: str | None = None
    notes: list[str] = field(default_factory=list)


async def summarize_execution(
    context: PersistentBuildContext, execution_id: str, *, detail: Literal["full", "brief"] = "full"
) -> ExecutionLogSummary:
    """Read ``execution_id``'s saved events in this Request and summarize them.

    Unless a nonempty log passes the basic contract checks as a whole, the summary says the record is
    missing and no event content is used. Cancellation, invalid arguments and summarizing errors propagate.
    """
    blocks, missing = await _record(context, execution_id, detail)
    return ExecutionLogSummary("\n".join(blocks), missing=missing)


async def record_blocks(context: PersistentBuildContext, execution_id: str) -> tuple[tuple[str, ...], bool]:
    """The full summary as its lines and items, after the same whole-log checks, and whether it is missing.

    Joined with line breaks they are the full summary; a paged reader splits by them.
    """
    return await _record(context, execution_id, "full")


async def _record(context: PersistentBuildContext, execution_id: str, detail: str) -> tuple[tuple[str, ...], bool]:
    if detail not in _DETAILS:
        raise build_error(StatusCode.DEEPAGENT_INPUT_PARAM_ERROR, error_msg="detail must be 'full' or 'brief'")
    if not isinstance(context, PersistentBuildContext):
        raise build_error(StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR, error_msg="PersistentBuildContext required")
    try:
        _EXECUTION_ID.validate_python(execution_id, strict=True)
    except ValidationError:
        raise build_error(
            StatusCode.DEEPAGENT_INPUT_PARAM_ERROR, error_msg="execution_id must be an identifier"
        ) from None
    execution = context.execution
    try:
        returned = await context.ports.commit.read_execution_log(execution, execution_id)
    except Exception as exc:  # noqa: BLE001 - a failed read is a missing record; cancellation still propagates
        _diagnose(execution, execution_id, f"read failed ({type(exc).__name__})", broken=False)
        return (_missing(execution_id),), True
    events, rejected = _validated(returned, execution, execution_id)
    if rejected is not None:
        # Nothing saved can be real; any other rejection means the host broke the port contract.
        _diagnose(execution, execution_id, rejected, broken=rejected != _EMPTY)
        return (_missing(execution_id),), True
    return tuple(_blocks(events, execution_id, brief=detail == "brief")), False


def _diagnose(execution, execution_id: str, category: str, *, broken: bool) -> None:
    """Category and locating identity only: no event content, exception text or validation detail.

    A log that breaks the port contract is an error; a failed read or an empty log is a warning.
    """
    log = logger.error if broken else logger.warning
    log(
        "[PersistentAgent] execution log unusable: session=%s request=%s execution=%s reason=%s",
        execution.session_id,
        execution.request_id,
        execution_id,
        category,
    )


def _missing(execution_id: str) -> str:
    return f"上一次执行 {execution_id}：{_MISSING}"


def _validated(returned: Any, execution, execution_id: str) -> tuple[tuple, str | None]:
    """The whole log checked first, then deduplicated and ordered; or why none of it can be used."""
    events, rejected = _checked(returned, execution, execution_id)
    if rejected is not None:
        return (), rejected
    # event_id hashes the full invocation and local_seq, so one check per local_seq covers both.
    by_seq: dict[int, AgentEvent] = {}
    for event in events:
        if by_seq.setdefault(event.local_seq, event).proposal_digest() != event.proposal_digest():
            return (), "two different events share an event_id or local_seq"
    return tuple(sorted(by_seq.values(), key=lambda event: event.local_seq)), None


def _checked(returned: Any, execution, execution_id: str) -> tuple[list[AgentEvent], str | None]:
    """Every returned item revalidated and owned by the target Execution of this Request."""
    if not isinstance(returned, tuple) or not returned:
        return [], _EMPTY if isinstance(returned, tuple) else "log is not a tuple of events"
    events = []
    for item in returned:
        try:
            events.append(AgentEvent.model_validate(item))
        except (ValidationError, TypeError, ValueError):
            return [], "an event does not validate"
    if any(_foreign(event, execution, execution_id) for event in events):
        return [], "an event belongs to another invocation"
    # One Execution has one source, though it may differ from the current Execution's.
    if len({event.source_ref for event in events}) > 1:
        return [], "events of one Execution name different sources"
    return events, None


def _foreign(event: AgentEvent, execution, execution_id: str) -> bool:
    return event.execution_id != execution_id or any(
        getattr(event, name) != getattr(execution, name) for name in _IDENTITY
    )


def _gaps(events: tuple) -> list[str]:
    spans, expected = [], 1
    for event in events:
        if event.local_seq > expected:
            low, high = expected, event.local_seq - 1
            spans.append(str(low) if low == high else f"{low}～{high}")
        expected = event.local_seq + 1
    return spans


def _block(first: str, body: str | None) -> str:
    """One item; a text of several lines follows on indented lines, kept as it is."""
    if not body:
        return first.rstrip("：")
    lines = body.split("\n")
    if len(lines) == 1:
        return f"{first}{body}"
    return first.rstrip("：") + "：\n" + "\n".join(_INDENT + line for line in lines)


def _note(tool: _Tool, note: str) -> None:
    if note not in tool.notes:
        tool.notes.append(note)


class _Tools:
    """Merges a result into an intent only when nothing in the log leaves its owner in doubt.

    The operation_id is derived from the tool call ID, so a reused ID cannot tell a late result of an
    earlier call from a result of the later one. An intent is always recorded before its own result, so
    a second intent, or an intent after a result, means the ID was reused: no later result is merged
    for it. One instance per summary; ``intents``, ``pending`` (the one intent a result may still join),
    ``reused`` and ``resulted`` are keyed by operation_id.
    """

    def __init__(self, items: list):
        self.items = items
        self.intents: dict[str, list[_Tool]] = {}
        self.pending: dict[str, _Tool] = {}
        self.reused: set[str] = set()
        self.resulted: set[str] = set()

    def intent(self, event) -> None:
        """A write intent waits for its result unless its operation_id was used before."""
        payload = event.payload
        op = payload.operation_id
        tool = _Tool(
            event.local_seq, payload.tool_name, payload.args_summary, op, payload.tool_call_id, True, payload.target_ref
        )
        seen = self.intents.setdefault(op, [])
        seen.append(tool)
        if len(seen) > 1 or op in self.resulted:
            self.reused.add(op)
            self.pending.pop(op, None)
            for each in seen:
                if each.status is None:
                    _note(each, "operation_id 与其他调用重复，结果无法唯一关联")
        else:
            self.pending[op] = tool
        self.items.append(("tool", tool))

    def result(self, event) -> None:
        """Merged into the waiting intent when its call ID and tool name agree; otherwise its own item."""
        payload = event.payload
        op = payload.operation_id
        waiting = self.pending.get(op) if op else None
        agrees = waiting is not None and (waiting.call_id, waiting.name) == (payload.tool_call_id, payload.tool_name)
        if agrees:
            tool = self.pending.pop(op)
        else:
            write = op is not None or not is_read_only_tool_name(payload.tool_name)
            tool = _Tool(event.local_seq, payload.tool_name, payload.args_summary, op, payload.tool_call_id, write)
            if waiting is not None:
                _note(waiting, "有一条调用身份不一致的结果，未合并")
                _note(tool, "调用身份与意图不一致（tool_call_id 或工具名），未合并")
            elif op in self.reused:
                _note(tool, "operation_id 与其他调用重复，无法确定对应哪次意图，未合并")
            elif write and op not in self.intents:
                _note(tool, "未见对应的意图记录")
            elif op in self.resulted:
                _note(tool, "该 operation_id 已有一条结果，本条未合并")
            self.items.append(("tool", tool))
        if op is not None:
            self.resulted.add(op)
        tool.status, tool.result, tool.reason = payload.status, payload.result_summary, payload.reason
        tool.refs = payload.result_refs


def _tool_text(tool: _Tool) -> str:
    label = _LABELS[tool.status] if tool.status else "已登记，无结果"
    line = f"[{label}] {tool.name}({tool.args})"
    if tool.result:
        line += f" → {tool.result}"
    if tool.refs:
        line += f"（结果引用 {'、'.join(tool.refs)}）"
    if tool.reason:
        line += f"（原因：{tool.reason}）"
    facts = [f"调用 {tool.call_id}"]
    if tool.write:
        facts.insert(0, f"写操作 {tool.operation_id}" if tool.operation_id else "写操作")
    if tool.target:
        facts.append(f"目标 {tool.target}")
    line += f"（{'，'.join(facts)}）"
    if tool.write:
        line += _WRITE_HINTS.get(tool.status, "")
    if tool.notes:
        line += f"（{'；'.join(tool.notes)}）"
    return line


def _ending_text(event) -> str:
    payload = event.payload
    if event.event_type == EventType.EXECUTION_FINAL:
        label = _FINAL_LABELS.get(payload.status, payload.status)
        return f"{label}（{payload.reason}）" if payload.reason else label
    return f"暂停（{payload.reason}）" + (f"：{payload.detail}" if payload.detail else "")


def _refs(ending) -> str:
    refs = ending.payload.output_refs
    return f"；引用候选 {'、'.join(refs)}" if refs else ""


def _candidate_text(payload, segment: int, referenced_by: dict[str, list[str]], ended: bool, brief: bool) -> str:
    facts = [payload.output_id, f"目标 {payload.target_ref}", payload.share_mode]
    if payload.file_ref:
        facts.append(payload.file_ref)
    if payload.file_size is not None:
        facts.append(f"{payload.file_size} 字节")
    if payload.dependency_refs:
        facts.append(f"依赖 {'、'.join(payload.dependency_refs)}")
    if payload.share_mode == "message":
        what = f"最终回答第 {segment} 段（{'，'.join(facts)}）"
    else:
        what = f"文件 {payload.file_path}（{'，'.join(facts)}）" if payload.file_path else f"文件（{'，'.join(facts)}）"
    if not ended:
        state = "未见结束记录，无法判断是否被引用"
    elif payload.output_id in referenced_by:
        state = f"被结束记录“{'”“'.join(referenced_by[payload.output_id])}”引用（服务端负责投递，不代表已送达）"
    else:
        state = "未被结束记录引用，服务端不会投递"
    if brief:
        return f"[交出候选] {what}：{state}" + (f"（正文 {len(payload.text)} 字，未列出）" if payload.text else "")
    return _block(f"[交出候选] {what}：{state}：", payload.text)


def _blocks(events: tuple, execution_id: str, *, brief: bool) -> list[str]:
    items: list[tuple[str, Any]] = []
    tools = _Tools(items)
    archives: list[str] = []
    endings: list = []
    for event in events:
        kind = event.event_type
        if kind == EventType.TOOL_INTENT:
            tools.intent(event)
        elif kind == EventType.TOOL_COMPLETED:
            tools.result(event)
        elif kind in (EventType.PROGRESS_UPDATED, EventType.OUTPUT_PROPOSED):
            items.append((str(kind), event.payload))
        elif kind == EventType.WINDOW_SWITCHED:
            archives.append(event.payload.archive_ref)
        elif kind in _ENDINGS:
            endings.append(event)
    body, omitted = _body(items, endings, brief)
    header = _header(events, execution_id, brief)
    if omitted:
        header.append(_OMITTED)
    return header + body + _tail(items, endings, archives)


def _header(events: tuple, execution_id: str, brief: bool) -> list[str]:
    request = events[0].request_id
    title = (
        f"执行 {execution_id}（请求 {request}）的执行日志摘要："
        if brief
        else (f"上一次执行 {execution_id}（请求 {request}）的执行日志整理：")
    )
    header = [title]
    gaps = _gaps(events)
    if gaps:
        header.append(f"日志缺少第 {'、'.join(gaps)} 条记录。")
    return header


def _brief_drops(kind: str, value: Any) -> bool:
    """Read-only calls are only counted in a brief summary; share_file listings and writes are kept."""
    return kind == "tool" and not value.write and value.name != SHARE_FILE


def _body(items: list, endings: list, brief: bool) -> tuple[list[str], bool]:
    """Numbered items, and whether a brief summary left something out."""
    referenced_by: dict[str, list[str]] = {}
    for ending in endings:
        for output_id in ending.payload.output_refs:
            referenced_by.setdefault(output_id, []).append(_ending_text(ending))
    lines: list[str] = []
    counted: dict[str, int] = {}
    segment = 0
    omitted = False
    for kind, value in items:
        if kind == EventType.OUTPUT_PROPOSED:
            segment += value.share_mode == "message"
            omitted |= brief and bool(value.text)
        if brief and _brief_drops(kind, value):
            counted[value.name] = counted.get(value.name, 0) + 1
            continue
        if kind == "tool":
            text = _tool_text(value)
        elif kind == EventType.PROGRESS_UPDATED:
            text = _block("[结论] ", value.text)
        else:
            text = _candidate_text(value, segment, referenced_by, bool(endings), brief)
        lines.append(f"{len(lines) + 1}. {text}")
    if counted:
        calls = "、".join(f"{name} {count} 次" for name, count in counted.items())
        lines.append(f"{len(lines) + 1}. [只读操作] {calls}（明细未列出）")
        omitted = True
    return lines or [_NOTHING], omitted


def _tail(items: list, endings: list, archives: list[str]) -> list[str]:
    if not endings:
        tail = [f"结局：{_NO_ENDING}"]
    elif len(endings) == 1:
        tail = [f"结局：{_ending_text(endings[0])}{_refs(endings[0])}"]
    else:
        conflict = "；".join(f"“{_ending_text(ending)}{_refs(ending)}”" for ending in endings)
        tail = [f"结局记录冲突，无法确定以哪一条为准：{conflict}"]
    proposed = {value.output_id for kind, value in items if kind == EventType.OUTPUT_PROPOSED}
    unseen = [ref for ending in endings for ref in ending.payload.output_refs if ref not in proposed]
    if unseen:
        tail.append(f"结束记录引用了 {'、'.join(dict.fromkeys(unseen))}，但日志中未见对应候选。")
    listed = "、".join(dict.fromkeys(archives)) if archives else "日志中没有换窗记录"
    tail.append(f"归档：{listed}（{_ARCHIVE_NOTE}）")
    return tail + [_FOOTER]

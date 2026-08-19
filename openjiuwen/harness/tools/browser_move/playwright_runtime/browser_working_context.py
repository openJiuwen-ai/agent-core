# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Typed, session-backed working context for the browser subagent."""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from openjiuwen.core.foundation.llm import BaseMessage, ToolMessage

from .browser_logging import browser_agent_log_info, browser_agent_log_warning


BROWSER_WORKING_CONTEXT_STATE_KEY = "__browser_subagent_working_context__"
BROWSER_TOOL_MEMORY_METADATA_KEY = "browser_working_context_retention"
BROWSER_WORKING_MEMORY_RECORD_BEGIN = "---BEGIN WORKING MEMORY RECORD V1---"
BROWSER_WORKING_MEMORY_RECORD_END = "---END WORKING MEMORY RECORD V1---"

_ERROR_PREFIXES = (
    "ability execution error:",
    "tool execution error:",
    "workflow execution error:",
    "agent execution error:",
    "[interrupted]",
)
_WORKING_CONTEXT_INSTRUCTIONS = {
    "en": (
        "This is durable browser-agent working context, separate from the replaceable "
        "<browser_state> observation. When an assistant response does not call tools, append "
        "exactly one plain-text working-memory record delimited by "
        "---BEGIN WORKING MEMORY RECORD V1--- and ---END WORKING MEMORY RECORD V1---. "
        "When a response calls tools, do not emit the record; the framework carries the previous "
        "record forward and adds tool results. This record is plain assistant text for framework "
        "bookkeeping, not a tool, function, or ability. Never place it in tool_calls, and invoke "
        "only tools explicitly supplied by the runtime. In the first record for a request, create "
        'the task_list yourself. Every task must use status "pending" or "completed". In every '
        "later record, review and replace the complete object: task_list, errors, failures, blockers, "
        "key_facts, and important_information. Preserve relevant completed work across follow-ups "
        "and reconcile the list with the new request. Never put credentials, screenshots, image "
        "data, complete DOM snapshots, or large raw tool output in this record.\n"
        "Field meanings:\n"
        "- task_list: the complete ordered plan for the active request; keep completed tasks and "
        "label every task pending or completed.\n"
        "- errors: concise tool, page, or runtime error messages that matter for recovery.\n"
        "- failures: actions or approaches that did not achieve their intended result and should "
        "not be repeated unchanged.\n"
        "- blockers: unresolved conditions currently preventing progress on a pending task.\n"
        "- key_facts: verified task-relevant facts supported by browser or tool evidence.\n"
        "- important_information: other durable operational context needed by later steps or a "
        "replacement agent that does not fit another field.\n"
        "Use an empty list [] when a field has no relevant entries. Do not invent facts or mark a "
        "task completed without evidence.\n"
        "Required record JSON shape: "
    ),
    "cn": (
        "这是浏览器子代理的持久工作上下文，与每次替换的 <browser_state> 观察结果相互独立。"
        "当助理响应不调用工具时，必须在末尾追加且只能追加一个纯文本工作记忆记录，并使用 "
        "---BEGIN WORKING MEMORY RECORD V1--- 和 ---END WORKING MEMORY RECORD V1--- 作为分隔符。"
        "当响应调用工具时，不要输出该记录；框架会沿用上一份记录并加入工具结果。"
        "该记录只是供框架维护状态的助理文本，不是工具、函数或能力。不得将其放入 tool_calls，"
        "并且只能调用运行时明确提供的工具。收到请求后的第一份记录中，请自行创建 task_list。"
        "每项任务的 status 必须为 "
        '"pending" 或 "completed"。之后每份记录都必须检查并完整替换以下字段：'
        "task_list、errors、failures、blockers、key_facts 和 important_information。"
        "后续请求应保留相关的已完成工作，并根据新请求调整任务列表。"
        "不要在此记录中写入凭据、截图、图像数据、完整 DOM 快照或大段原始工具输出。\n"
        "字段含义：\n"
        "- task_list：当前请求的完整有序计划；保留已完成任务，并将每项任务标记为 pending 或 completed。\n"
        "- errors：与恢复有关的简明工具、页面或运行时错误消息。\n"
        "- failures：未达到预期结果且不应原样重复的操作或方法。\n"
        "- blockers：当前阻止某项待处理任务继续推进的未解决条件。\n"
        "- key_facts：有浏览器或工具证据支持、与任务相关且已核实的事实。\n"
        "- important_information：后续步骤或接替代理需要、但不适合放入其他字段的持久操作信息。\n"
        "字段没有相关内容时使用空列表 []。没有证据时，不得编造事实或将任务标记为 completed。\n"
        "记录中的 JSON 必须使用以下结构："
    ),
}
_WORKING_MEMORY_RECORD_SHAPE = (
    '{"task_list":[{"task":"...","status":"pending|completed"}],'
    '"errors":[],"failures":[],"blockers":[],"key_facts":[],'
    '"important_information":[]}'
)


class BrowserWorkingContextConfig(BaseModel):
    """Limits for browser working memory and prompt rendering."""

    language: Literal["cn", "en"] = "en"
    max_recent_steps: int = Field(default=6, ge=1)
    max_list_items: int = Field(default=20, ge=1)
    max_item_chars: int = Field(default=1_000, ge=128)
    max_one_step_chars: int = Field(default=8_000, ge=256)
    max_prompt_chars: int = Field(default=24_000, ge=2_000)


class BrowserTaskItem(BaseModel):
    """One model-authored browser task and its explicit status."""

    model_config = ConfigDict(extra="forbid")

    task: str
    status: Literal["pending", "completed"]


class BrowserWorkingMemory(BaseModel):
    """Complete replacement working-memory object authored once per model step."""

    model_config = ConfigDict(extra="forbid")

    task_list: list[BrowserTaskItem] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    failures: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    key_facts: list[str] = Field(default_factory=list)
    important_information: list[str] = Field(default_factory=list)


class BrowserToolMemory(BaseModel):
    """Prompt-safe retention selected from one complete diagnostic tool result."""

    tool_name: str
    tool_call_id: str
    durable_content: Optional[str] = None
    content_source: Optional[Literal["long_term_memory", "extracted_content"]] = None
    one_step_content: Optional[str] = None
    error: Optional[str] = None

    def has_prompt_content(self) -> bool:
        return bool(self.durable_content or self.one_step_content or self.error)


class BrowserPendingToolCall(BaseModel):
    """Tool identity retained until all results for one model step are available."""

    tool_name: str
    tool_call_id: str


class BrowserPendingStep(BaseModel):
    """Model-authored state waiting for the same step's tool results."""

    step_number: int
    model_memory: Optional[BrowserWorkingMemory] = None
    model_update_error: Optional[str] = None
    tool_calls: list[BrowserPendingToolCall] = Field(default_factory=list)


class BrowserStepRecord(BaseModel):
    """One durable record per model response, aggregating every tool result."""

    step_number: int
    model_memory: Optional[BrowserWorkingMemory] = None
    model_update_error: Optional[str] = None
    tool_memories: list[BrowserToolMemory] = Field(default_factory=list)


class BrowserWorkingContextState(BaseModel):
    """JSON-serializable durable state stored on the external agent Session."""

    model_config = ConfigDict(extra="ignore")

    version: int = 1
    request_sequence: int = 0
    request_kind: Literal["initial", "follow_up"] = "initial"
    active_request: str = ""
    current: BrowserWorkingMemory = Field(default_factory=BrowserWorkingMemory)
    recent_steps: list[BrowserStepRecord] = Field(default_factory=list)
    one_step_content: list[BrowserToolMemory] = Field(default_factory=list)
    next_step_number: int = 1
    pending_step: Optional[BrowserPendingStep] = None


def _bounded_text(value: Any, max_chars: int) -> str:
    """Normalize and hard-cap one retained text value."""

    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) > max_chars:
        omitted = len(text) - max_chars
        return f"{text[:max_chars].rstrip()} ...[truncated {omitted} characters]"
    return text


class BrowserWorkingContextStore:
    """Own the deterministic lifecycle of session-backed browser working context."""

    def __init__(self, config: BrowserWorkingContextConfig) -> None:
        self.config = config

    @staticmethod
    def load(session: Any) -> BrowserWorkingContextState:
        if session is None:
            return BrowserWorkingContextState()
        raw_state = session.get_state(BROWSER_WORKING_CONTEXT_STATE_KEY)
        if not isinstance(raw_state, dict):
            return BrowserWorkingContextState()
        try:
            return BrowserWorkingContextState.model_validate(raw_state)
        except ValidationError:
            browser_agent_log_warning("[BrowserWorkingContext] invalid stored state; starting with an empty state")
            return BrowserWorkingContextState()

    @staticmethod
    def save(session: Any, state: BrowserWorkingContextState) -> None:
        if session is None:
            return
        session.update_state(
            {
                BROWSER_WORKING_CONTEXT_STATE_KEY: state.model_dump(mode="json"),
            }
        )

    def begin_request(self, session: Any, query: Any) -> BrowserWorkingContextState:
        """Restore, classify, and reconcile a request without clearing memory."""

        state = self.load(session)
        request_text = _bounded_text(query, self.config.max_item_chars)
        if not request_text:
            return state
        state.request_kind = "follow_up" if state.request_sequence else "initial"
        state.request_sequence += 1
        state.active_request = request_text
        self._reconcile_request_task(state, request_text)
        self.save(session, state)
        browser_agent_log_info(
            "[BrowserWorkingContext] began %s request %d for session %s (tasks=%d, durable_steps=%d)",
            state.request_kind,
            state.request_sequence,
            getattr(session, "get_session_id", lambda: "")(),
            len(state.current.task_list),
            len(state.recent_steps),
        )
        return state

    def sanitize_memory(self, memory: BrowserWorkingMemory) -> BrowserWorkingMemory:
        """Bound and redact every model-authored field before persistence."""

        tasks: list[BrowserTaskItem] = []
        for item in memory.task_list[: self.config.max_list_items]:
            task = _bounded_text(item.task, self.config.max_item_chars)
            if task:
                tasks.append(BrowserTaskItem(task=task, status=item.status))

        return BrowserWorkingMemory(
            task_list=tasks,
            errors=self._sanitize_list(memory.errors),
            failures=self._sanitize_list(memory.failures),
            blockers=self._sanitize_list(memory.blockers),
            key_facts=self._sanitize_list(memory.key_facts),
            important_information=self._sanitize_list(memory.important_information),
        )

    def stage_model_step(
        self,
        session: Any,
        *,
        memory: Optional[BrowserWorkingMemory],
        model_update_error: Optional[str],
        tool_calls: Iterable[Any],
    ) -> None:
        """Stage one model-authored object and commit immediately if no tools follow."""

        state = self.load(session)
        if state.pending_step is not None:
            self._commit_pending_as_incomplete(state)

        sanitized_memory = self.sanitize_memory(memory) if memory is not None else None
        if sanitized_memory is None and state.current.task_list:
            # A missing/malformed update must not erase the last valid state.
            # The explicit model_update_error remains in the step record so
            # the next model call can repair the omission.
            sanitized_memory = self.sanitize_memory(state.current)
        sanitized_error = _bounded_text(
            model_update_error,
            self.config.max_item_chars,
        )
        pending_tool_calls = [
            BrowserPendingToolCall(
                tool_name=_bounded_text(
                    getattr(tool_call, "name", ""),
                    self.config.max_item_chars,
                ),
                tool_call_id=str(getattr(tool_call, "id", "") or ""),
            )
            for tool_call in tool_calls
        ]
        state.pending_step = BrowserPendingStep(
            step_number=state.next_step_number,
            model_memory=sanitized_memory,
            model_update_error=sanitized_error or None,
            tool_calls=pending_tool_calls,
        )
        if pending_tool_calls:
            self.save(session, state)
            return
        self._commit_pending(state, [])
        self.save(session, state)

    def commit_pending_from_messages(
        self,
        session: Any,
        messages: Iterable[BaseMessage],
    ) -> bool:
        """Commit a staged step only after every same-step tool message exists."""

        state = self.load(session)
        pending = state.pending_step
        if pending is None:
            return False
        if not pending.tool_calls:
            self._commit_pending(state, [])
            self.save(session, state)
            return True

        messages_by_call_id: Dict[str, ToolMessage] = {}
        for message in messages:
            if isinstance(message, ToolMessage):
                messages_by_call_id[str(message.tool_call_id)] = message

        if any(tool.tool_call_id not in messages_by_call_id for tool in pending.tool_calls):
            return False

        tool_memories = [
            self.tool_memory_from_message(
                tool_name=tool.tool_name,
                tool_call_id=tool.tool_call_id,
                message=messages_by_call_id[tool.tool_call_id],
            )
            for tool in pending.tool_calls
        ]
        self._commit_pending(state, tool_memories)
        self.save(session, state)
        return True

    def build_tool_retention(
        self,
        *,
        tool_name: str,
        tool_call_id: str,
        tool_result: Any,
    ) -> BrowserToolMemory:
        """Apply explicit tool retention precedence without copying raw observations."""

        long_term_memory = getattr(tool_result, "long_term_memory", None)
        extracted_content = getattr(tool_result, "extracted_content", None)
        one_step_only = bool(getattr(tool_result, "include_extracted_content_only_once", False))
        error = getattr(tool_result, "error", None)
        success = getattr(tool_result, "success", None)

        durable_content = None
        content_source = None
        if long_term_memory:
            durable_content = _bounded_text(
                long_term_memory,
                self.config.max_item_chars,
            )
            content_source = "long_term_memory"
        elif extracted_content and not one_step_only:
            durable_content = _bounded_text(
                extracted_content,
                self.config.max_item_chars,
            )
            content_source = "extracted_content"

        one_step_content = None
        if extracted_content and one_step_only:
            one_step_content = _bounded_text(
                extracted_content,
                self.config.max_one_step_chars,
            )

        retained_error = None
        if error or success is False:
            retained_error = _bounded_text(
                error or "Tool reported failure without an error message.",
                self.config.max_item_chars,
            )

        return BrowserToolMemory(
            tool_name=_bounded_text(tool_name, self.config.max_item_chars),
            tool_call_id=str(tool_call_id or ""),
            durable_content=durable_content or None,
            content_source=content_source,
            one_step_content=one_step_content or None,
            error=retained_error or None,
        )

    def tool_memory_from_message(
        self,
        *,
        tool_name: str,
        tool_call_id: str,
        message: ToolMessage,
    ) -> BrowserToolMemory:
        """Recover explicit retention metadata and independently retain tool errors."""

        payload = message.metadata.get(BROWSER_TOOL_MEMORY_METADATA_KEY)
        memory = BrowserToolMemory(
            tool_name=tool_name,
            tool_call_id=tool_call_id,
        )
        if isinstance(payload, dict):
            try:
                memory = BrowserToolMemory.model_validate(payload)
            except ValidationError:
                browser_agent_log_warning(
                    "[BrowserWorkingContext] invalid tool retention metadata for %s",
                    tool_name,
                )
        memory.tool_name = _bounded_text(
            tool_name,
            self.config.max_item_chars,
        )
        memory.tool_call_id = str(tool_call_id or "")

        if not memory.error:
            inferred_error = self._infer_tool_error(message.content)
            if inferred_error:
                memory.error = inferred_error
        if memory.has_prompt_content() and not isinstance(payload, dict):
            metadata = dict(message.metadata)
            metadata[BROWSER_TOOL_MEMORY_METADATA_KEY] = memory.model_dump(
                mode="json",
                exclude_none=True,
            )
            message.metadata = metadata
        return memory

    def render_and_consume_one_step(self, session: Any) -> str:
        """Render the bounded durable view and expire next-step-only content."""

        state = self.load(session)
        one_step_content = list(state.one_step_content)
        if one_step_content:
            state.one_step_content = []
            self.save(session, state)

        instructions = _WORKING_CONTEXT_INSTRUCTIONS[self.config.language] + _WORKING_MEMORY_RECORD_SHAPE
        rendered_state = {
            "request": {
                "sequence": state.request_sequence,
                "kind": state.request_kind,
                "active_request": state.active_request,
            },
            "current_model_authored_state": state.current.model_dump(mode="json"),
            "next_step_only_tool_content": [
                item.model_dump(mode="json", exclude_none=True) for item in one_step_content
            ],
            "recent_durable_steps": [step.model_dump(mode="json") for step in state.recent_steps],
        }
        body = json.dumps(rendered_state, ensure_ascii=False, indent=2)
        body = _bounded_text(body, self.config.max_prompt_chars)
        return f"<browser_working_context>\n{instructions}\n{body}\n</browser_working_context>"

    def _sanitize_list(self, values: Iterable[Any]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for value in list(values)[: self.config.max_list_items]:
            text = _bounded_text(value, self.config.max_item_chars)
            if text and text not in seen:
                seen.add(text)
                result.append(text)
        return result

    def _reconcile_request_task(
        self,
        state: BrowserWorkingContextState,
        request_text: str,
    ) -> None:
        """Preserve restored work and ensure the active request is actionable."""

        tasks = list(state.current.task_list)
        if any(item.task == request_text for item in tasks):
            return
        if len(tasks) >= self.config.max_list_items:
            retained_count = self.config.max_list_items - 1
            tasks = tasks[-retained_count:] if retained_count else []
        tasks.append(BrowserTaskItem(task=request_text, status="pending"))
        state.current = state.current.model_copy(update={"task_list": tasks})

    def _commit_pending(
        self,
        state: BrowserWorkingContextState,
        tool_memories: list[BrowserToolMemory],
    ) -> None:
        pending = state.pending_step
        if pending is None:
            return
        prompt_tool_memories = [memory for memory in tool_memories if memory.has_prompt_content()]
        durable_tool_memories = []
        for memory in prompt_tool_memories:
            durable_memory = memory.model_copy(update={"one_step_content": None})
            if durable_memory.has_prompt_content():
                durable_tool_memories.append(durable_memory)
        state.recent_steps.append(
            BrowserStepRecord(
                step_number=pending.step_number,
                model_memory=pending.model_memory,
                model_update_error=pending.model_update_error,
                tool_memories=durable_tool_memories,
            )
        )
        if pending.model_memory is not None:
            state.current = pending.model_memory
        state.one_step_content = [memory for memory in prompt_tool_memories if memory.one_step_content]
        state.next_step_number = max(
            state.next_step_number,
            pending.step_number + 1,
        )
        state.pending_step = None
        self._limit_history(state)

    def _commit_pending_as_incomplete(
        self,
        state: BrowserWorkingContextState,
    ) -> None:
        pending = state.pending_step
        if pending is None:
            return
        missing_tools = ", ".join(tool.tool_name for tool in pending.tool_calls)
        suffix = f" Missing diagnostic results for: {missing_tools}." if missing_tools else ""
        pending.model_update_error = _bounded_text(
            (pending.model_update_error or "")
            + " Previous model step was superseded before its tool results were committed."
            + suffix,
            self.config.max_item_chars,
        )
        self._commit_pending(state, [])

    def _limit_history(self, state: BrowserWorkingContextState) -> None:
        if len(state.recent_steps) > self.config.max_recent_steps:
            state.recent_steps = state.recent_steps[-self.config.max_recent_steps:]

    def _infer_tool_error(self, content: Any) -> Optional[str]:
        text = self._message_content_to_text(content)
        if not text:
            return None
        lowered = text.lower().strip()
        if lowered.startswith(_ERROR_PREFIXES):
            return _bounded_text(text, self.config.max_item_chars)

        try:
            parsed = json.loads(text)
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, dict):
            error = parsed.get("error")
            failure_markers = (
                parsed.get("success") is False,
                parsed.get("ok") is False,
                parsed.get("isError") is True,
            )
            if error and any(failure_markers):
                return _bounded_text(error, self.config.max_item_chars)

        if "success=false" in lowered and "error=" in lowered:
            return _bounded_text(text, self.config.max_item_chars)
        return None

    @staticmethod
    def _message_content_to_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
            return "\n".join(parts)
        return str(content or "")


__all__ = [
    "BROWSER_TOOL_MEMORY_METADATA_KEY",
    "BROWSER_WORKING_MEMORY_RECORD_BEGIN",
    "BROWSER_WORKING_MEMORY_RECORD_END",
    "BROWSER_WORKING_CONTEXT_STATE_KEY",
    "BrowserPendingStep",
    "BrowserStepRecord",
    "BrowserTaskItem",
    "BrowserToolMemory",
    "BrowserWorkingContextConfig",
    "BrowserWorkingContextState",
    "BrowserWorkingContextStore",
    "BrowserWorkingMemory",
]

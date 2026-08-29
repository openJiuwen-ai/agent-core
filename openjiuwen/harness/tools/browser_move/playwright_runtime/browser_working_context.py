# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Typed, session-backed working context for the browser subagent."""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from openjiuwen.core.foundation.llm import BaseMessage, ToolMessage, UserMessage

from .browser_logging import browser_agent_log_info, browser_agent_log_warning


BROWSER_WORKING_CONTEXT_STATE_KEY = "__browser_subagent_working_context__"
BROWSER_TASK_STATE_KEY = "__browser_phase_budget_state__"
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
        "This is the runtime-maintained browser task context. Treat task status, phase state, "
        "field coverage, blockers, evidence, and recent actions as authoritative. Do not rewrite "
        "or echo this context, and do not repeat an action whose semantic_delta shows no progress. "
        "The runtime updates it after browser tools. Only when a verified cross-page fact or user "
        "constraint cannot be represented by runtime evidence may a non-tool response append one "
        "optional plain-text note record delimited by ---BEGIN WORKING MEMORY RECORD V1--- and "
        "---END WORKING MEMORY RECORD V1---. Never include credentials, screenshots, DOM snapshots, "
        "or raw tool output. Optional note JSON shape: "
    ),
    "cn": (
        "这是由 runtime 维护的浏览器任务上下文。任务状态、阶段、字段覆盖率、阻断项、证据和"
        "最近动作均为权威信息；不要重写或复述这些内容，也不要重复 semantic_delta 显示无进展的动作。"
        "runtime 会在浏览器工具执行后自动更新。只有经过验证、但 runtime 证据无法表达的跨页面事实或"
        "用户约束，才允许在不调用工具的响应末尾追加一份可选纯文本记录，并使用 "
        "---BEGIN WORKING MEMORY RECORD V1--- 和 ---END WORKING MEMORY RECORD V1--- 分隔。"
        "不得写入凭据、截图、DOM 快照或原始工具输出。可选记录 JSON 结构："
    ),
}
_WORKING_MEMORY_RECORD_SHAPE = '{"key_facts":["..."],"important_information":["..."]}'
_EPHEMERAL_USER_MESSAGE_NAMES = frozenset(
    {
        "browser_working_context",
        "current_browser_state",
        "browser_state_progress",
    }
)
_EPHEMERAL_CONTEXT_METADATA_KEYS = (
    "browser_working_context",
    "browser_state_context",
    "browser_state_progress_context",
)
_TERMINAL_TASK_STATUSES = frozenset({"blocked", "partial", "completed"})


class BrowserWorkingContextConfig(BaseModel):
    """Limits for browser working memory and prompt rendering."""

    language: Literal["cn", "en"] = "en"
    max_recent_steps: int = Field(default=6, ge=1)
    max_list_items: int = Field(default=20, ge=1)
    max_item_chars: int = Field(default=1_000, ge=128)
    max_one_step_chars: int = Field(default=8_000, ge=256)
    max_prompt_chars: int = Field(default=12_000, ge=2_000)


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
        """Restore and classify a request while task progress remains runtime-owned."""

        state = self.load(session)
        request_text = _bounded_text(query, self.config.max_item_chars)
        if not request_text:
            return state
        state.request_kind = "follow_up" if state.request_sequence else "initial"
        state.request_sequence += 1
        state.active_request = request_text
        state.current = BrowserWorkingMemory(
            key_facts=self._sanitize_list(state.current.key_facts),
            important_information=self._sanitize_list(state.current.important_information),
        )
        self.save(session, state)
        browser_agent_log_info(
            "[BrowserWorkingContext] began %s request %d for session %s (tasks=%d, durable_steps=%d)",
            state.request_kind,
            state.request_sequence,
            getattr(session, "get_session_id", lambda: "")(),
            len(state.current.key_facts) + len(state.current.important_information),
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
        """Render one compact projection and expire next-step-only content."""

        state = self.load(session)
        one_step_content = list(state.one_step_content)
        if one_step_content:
            state.one_step_content = []
            self.save(session, state)

        instructions = _WORKING_CONTEXT_INSTRUCTIONS[self.config.language] + _WORKING_MEMORY_RECORD_SHAPE
        task_state = self.load_task_state(session)
        rendered_state = {
            "request": {
                "sequence": state.request_sequence,
                "kind": state.request_kind,
                "active_request": state.active_request,
            },
            "task_state": self._project_task_state(task_state),
            "runtime_directive": self._runtime_directive(task_state),
            "recent_actions": list(task_state.get("recent_actions") or [])[-self.config.max_recent_steps:],
            "model_notes": {
                "key_facts": state.current.key_facts[-self.config.max_list_items:],
                "important_information": state.current.important_information[-self.config.max_list_items:],
            },
            "retained_tool_evidence": self._compact_retained_tool_evidence(state.recent_steps),
            "next_step_only_tool_content": [
                item.model_dump(mode="json", exclude_none=True) for item in one_step_content
            ],
        }
        body = json.dumps(rendered_state, ensure_ascii=False, indent=2)
        body = _bounded_text(body, self.config.max_prompt_chars)
        return f"<browser_working_context>\n{instructions}\n{body}\n</browser_working_context>"

    def _compact_retained_tool_evidence(self, steps: Iterable[BrowserStepRecord]) -> list[Dict[str, Any]]:
        evidence: list[Dict[str, Any]] = []
        for step in list(steps)[-self.config.max_recent_steps:]:
            for memory in step.tool_memories:
                if not (memory.durable_content or memory.error):
                    continue
                evidence.append(
                    {
                        "step": step.step_number,
                        "tool": memory.tool_name,
                        "content": _bounded_text(
                            memory.durable_content or memory.error,
                            self.config.max_item_chars,
                        ),
                        "source": memory.content_source or ("error" if memory.error else "retained"),
                    }
                )
        return evidence[-self.config.max_recent_steps:]

    @staticmethod
    def load_task_state(session: Any) -> Dict[str, Any]:
        if session is None:
            return {}
        raw_state = session.get_state(BROWSER_TASK_STATE_KEY)
        return dict(raw_state) if isinstance(raw_state, dict) else {}

    @classmethod
    def sync_semantic_progress(cls, session: Any, progress: Any) -> bool:
        """Merge one browser observation into authoritative task state.

        Returns ``True`` only when a permitted replan trial produced observable
        semantic progress and the runtime gate may be cleared.
        """

        if session is None or not isinstance(progress, dict) or not progress:
            return False
        state = cls.load_task_state(session)
        if not state:
            return False
        revision = int(progress.get("revision") or 0)
        if revision <= int(state.get("semantic_revision") or 0):
            return False

        state["semantic_revision"] = revision
        cls._merge_semantic_observation(state, progress)
        if str(state.get("status") or "").strip().lower() in _TERMINAL_TASK_STATUSES:
            session.update_state({BROWSER_TASK_STATE_KEY: state})
            return False
        progress_name = str(progress.get("progress") or "unknown")
        recovered = bool(
            state.get("replan_trial_pending")
            and (progress.get("observable_progress") is True or progress_name == "progress")
        )
        cls._apply_replan_observation(state, progress, recovered=recovered)
        session.update_state({BROWSER_TASK_STATE_KEY: state})
        return recovered

    @staticmethod
    def _merge_semantic_observation(state: Dict[str, Any], progress: Dict[str, Any]) -> None:
        semantic_progress: Dict[str, Any] = {}
        semantic_keys = (
            "progress",
            "observable_progress",
            "consecutive_no_progress",
            "state_revisit_count",
            "aba_loop",
            "repeated_filter_state",
            "replan_required",
            "replan_reason",
        )
        for key in semantic_keys:
            if key in progress:
                semantic_progress[key] = progress.get(key)
        state["semantic_progress"] = semantic_progress
        semantic_state = progress.get("semantic_state")
        if isinstance(semantic_state, dict):
            coverage = semantic_state.get("field_coverage")
            if isinstance(coverage, list):
                merged_coverage = set(state.get("field_coverage") or [])
                merged_coverage.update(str(item) for item in coverage if str(item).strip())
                state["field_coverage"] = sorted(merged_coverage)
            url = str(semantic_state.get("url") or "").strip()
            if url:
                last_page = state.setdefault("last_page", {})
                last_page["url"] = url

        progress_name = str(progress.get("progress") or "unknown")
        recent_actions = state.get("recent_actions")
        if isinstance(recent_actions, list) and recent_actions:
            last_action = recent_actions[-1]
            if isinstance(last_action, dict) and last_action.get("semantic_delta") in (None, "", "pending"):
                last_action["semantic_delta"] = progress_name

    @classmethod
    def _apply_replan_observation(
        cls,
        state: Dict[str, Any],
        progress: Dict[str, Any],
        *,
        recovered: bool,
    ) -> None:
        if str(state.get("status") or "").strip().lower() in _TERMINAL_TASK_STATUSES:
            return
        if recovered:
            cls.mark_replan_recovered(state)
        elif state.get("replan_trial_pending"):
            trial_strategy = str(state.get("trial_strategy") or "")
            cls.record_failed_strategy(state, trial_strategy)
            state["replan_trial_pending"] = False
            state["replan_required"] = True
            state["blocked_strategy"] = trial_strategy
            state["status"] = "replan_required"
            state["next_action_class"] = "materially_different_strategy"
        elif progress.get("replan_required"):
            state["replan_required"] = True
            state["status"] = "replan_required"
            state["blocked_strategy"] = str(
                state.get("trial_strategy")
                or state.get("last_strategy_fingerprint")
                or state.get("last_action_class")
                or ""
            )
            cls.record_failed_strategy(state, str(state.get("blocked_strategy") or ""))
            state["next_action_class"] = "materially_different_strategy"

    @staticmethod
    def record_failed_strategy(state: Dict[str, Any], strategy: str) -> None:
        """Remember one action strategy that failed to produce semantic progress."""
        failed_strategies = state.setdefault("failed_strategies", [])
        if strategy and strategy not in failed_strategies:
            failed_strategies.append(strategy)

    @staticmethod
    def mark_replan_recovered(state: Dict[str, Any]) -> None:
        """Clear a task-level replan gate after verified semantic progress."""

        if str(state.get("status") or "").strip().lower() in _TERMINAL_TASK_STATUSES:
            return
        state["replan_required"] = False
        state["replan_trial_pending"] = False
        state["blocked_strategy"] = ""
        state["trial_strategy"] = ""
        state["replan_denial_count"] = 0
        state["status"] = "in_progress"
        state["next_action_class"] = ""

    @staticmethod
    def _project_task_state(state: Dict[str, Any]) -> Dict[str, Any]:
        phases = state.get("phases") if isinstance(state.get("phases"), dict) else {}
        compact_phases = {
            str(name): {
                "status": details.get("status"),
                "attempts": int(details.get("attempts") or 0),
                "budget": int(details.get("budget") or 0),
                "completion_condition": _bounded_text(details.get("completion_condition"), 240),
            }
            for name, details in phases.items()
            if isinstance(details, dict)
        }
        semantic_progress = state.get("semantic_progress")
        compact_semantic: Dict[str, Any] = {}
        if isinstance(semantic_progress, dict):
            semantic_keys = (
                "progress",
                "consecutive_no_progress",
                "state_revisit_count",
                "aba_loop",
                "repeated_filter_state",
                "replan_reason",
            )
            for key in semantic_keys:
                if key in semantic_progress:
                    compact_semantic[key] = semantic_progress.get(key)
        return {
            "task_id": state.get("task_id"),
            "goal": _bounded_text(state.get("goal") or state.get("task"), 1_000),
            "task_type": state.get("task_type"),
            "status": state.get("status", "in_progress"),
            "current_phase": state.get("current_phase"),
            "phases": compact_phases,
            "required_fields": list(state.get("required_fields") or [])[:32],
            "field_coverage": list(state.get("field_coverage") or [])[:32],
            "required_evidence_slots": [
                dict(slot) for slot in (state.get("required_evidence_slots") or [])[:12] if isinstance(slot, dict)
            ],
            "evidence_slots": [
                dict(slot) for slot in (state.get("evidence_slots") or [])[-12:] if isinstance(slot, dict)
            ],
            "blockers": list(state.get("blockers") or [])[:8],
            "replan_required": bool(state.get("replan_required")),
            "replan_count": int(state.get("replan_count") or 0),
            "failed_strategies": list(state.get("failed_strategies") or [])[:8],
            "next_action_class": state.get("next_action_class"),
            "semantic_progress": compact_semantic,
            "structured_evidence": BrowserWorkingContextStore.compact_evidence(state.get("structured_evidence")),
            "last_page": dict(state.get("last_page") or {}),
        }

    @staticmethod
    def compact_evidence(value: Any) -> list[Dict[str, Any]]:
        """Project structured evidence into a bounded model-facing form."""
        records = value if isinstance(value, list) else []
        compact_records: list[Dict[str, Any]] = []
        for record in records[-5:]:
            if not isinstance(record, dict):
                continue
            compact: Dict[str, Any] = {
                "kind": record.get("kind"),
                "generation_id": record.get("generation_id"),
                "fields": list(record.get("fields") or [])[:20],
            }
            values = record.get("values")
            if isinstance(values, dict):
                compact["values"] = {str(key): _bounded_text(item, 160) for key, item in list(values.items())[:12]}
            cards = record.get("cards")
            if isinstance(cards, list):
                compact["cards"] = [dict(card) for card in cards[:3] if isinstance(card, dict)]
            for key in ("preview", "target_count"):
                if record.get(key) not in (None, ""):
                    compact[key] = _bounded_text(record.get(key), 800)
            provenance = record.get("provenance")
            if isinstance(provenance, dict):
                compact["provenance"] = dict(list(provenance.items())[:8])
            compact_records.append(compact)
        return compact_records

    @staticmethod
    def _runtime_directive(state: Dict[str, Any]) -> str:
        status = str(state.get("status") or "in_progress")
        if status == "completed":
            return "must_finish"
        if status in {"blocked", "partial"}:
            return "return_partial_or_blocked"
        if state.get("replan_required"):
            return "replan_before_browser_action"
        return "continue"

    def _sanitize_list(self, values: Iterable[Any]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for value in list(values)[: self.config.max_list_items]:
            text = _bounded_text(value, self.config.max_item_chars)
            if text and text not in seen:
                seen.add(text)
                result.append(text)
        return result

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
        if pending.model_memory is not None or pending.model_update_error or durable_tool_memories:
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
        text = self.message_content_to_text(content)
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
    def message_content_to_text(content: Any) -> str:
        """Flatten supported message content into prompt-safe text."""
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
    "BROWSER_TASK_STATE_KEY",
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
    "latest_browser_user_request",
]


def latest_browser_user_request(messages: Iterable[BaseMessage]) -> str:
    """Return the newest real user request, excluding ephemeral browser context."""

    for message in reversed(list(messages)):
        if not isinstance(message, UserMessage):
            continue
        if message.name in _EPHEMERAL_USER_MESSAGE_NAMES:
            continue
        metadata = getattr(message, "metadata", {}) or {}
        is_ephemeral_context = False
        for key in _EPHEMERAL_CONTEXT_METADATA_KEYS:
            if metadata.get(key):
                is_ephemeral_context = True
                break
        if is_ephemeral_context:
            continue
        text = BrowserWorkingContextStore.message_content_to_text(message.content).strip()
        if text:
            return text
    return ""

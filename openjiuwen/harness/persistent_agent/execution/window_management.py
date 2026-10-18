# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""PA-11: prepare, validate and commit a new active context at a complete ReAct boundary."""

import json
from copy import deepcopy
from dataclasses import dataclass
from typing import TYPE_CHECKING

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm import AssistantMessage, ToolMessage, UserMessage

from ..schema.integration import (
    EXECUTION_END,
    EXECUTION_HEADER,
    HANDOFF_TEXT,
    TEMPORARY_BACKGROUND,
    WINDOW_PART_KEY,
    SessionCheckpoint,
)
from ..schema.window import WindowRollover, WindowSeed, WindowSwitchRequest
from ..tools.effect_policy import is_read_only_tool_name
from . import capacity
from .control import ExecutionControl
from .model_rail import PAUSE_MARKER

if TYPE_CHECKING:
    from openjiuwen.core.single_agent.rail.base import AgentCallbackContext

    from ..schema.build_context import PersistentBuildContext
    from ..schema.spec import PersistentAgentSpec
    from .emitter import EventEmitter

_SEED_PREFIX = "【窗口交接要点；事实与副作用状态仍需核实】\n"
_FALLBACK_NEXT_STEP = "根据原始任务和保留的工具结果继续；不要把交接文字当作操作成功证明。"
_PROMPT_ATTACHMENT_HISTORY = "_openjiuwen_prompt_attachment_history"
_PROTECTED_TOOL_NAMES = frozenset({"share_file"})


@dataclass(frozen=True)
class _MessageGroup:
    """A standalone message or a complete assistant tool-call batch with its results."""

    start: int
    end: int
    messages: tuple
    protected: bool
    write_call_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class _PreparedRollover:
    """An in-memory candidate awaiting an exact count of the rebuilt final request."""

    old_messages: tuple
    new_messages: tuple
    seed: WindowSeed
    trigger_kind: str
    retained_item_refs: tuple[str, ...]
    iteration_seq: int


class _ContextCountUnavailable(ValueError):
    """The exact count needed to decide whether a candidate is safe could not be obtained."""


class _RequiredContextUnfit(ValueError):
    """The task seed and other content that may not be discarded exceed the input limit."""


def _part(message) -> str | None:
    return (getattr(message, "metadata", None) or {}).get(WINDOW_PART_KEY)


def _as_text(message) -> str | None:
    content = getattr(message, "content", None)
    return content if isinstance(content, str) else None


def _prompt_attachment(message) -> bool:
    return bool((getattr(message, "metadata", None) or {}).get(_PROMPT_ATTACHMENT_HISTORY))


def _tool_calls(message) -> tuple:
    calls = getattr(message, "tool_calls", None) or ()
    return tuple(calls)


def _message_groups(messages: list, start: int, stop: int) -> list[_MessageGroup]:
    """Split a safe message range without separating a call from the ToolMessages that answer it."""
    groups = []
    index = start
    while index < stop:
        message = messages[index]
        group_start = index
        index += 1
        if isinstance(message, ToolMessage):
            raise ValueError("tool result is not adjacent to its assistant tool-call group")
        calls = _tool_calls(message) if isinstance(message, AssistantMessage) else ()
        if calls:
            call_names = {call.id: call.name for call in calls}
            answered = set()
            while index < stop and isinstance(messages[index], ToolMessage):
                result = messages[index]
                if result.tool_call_id not in call_names or result.tool_call_id in answered:
                    raise ValueError("tool result does not match its assistant tool-call group")
                answered.add(result.tool_call_id)
                index += 1
            if answered != set(call_names):
                raise ValueError("assistant tool-call group has no complete result set")
            write_call_ids = tuple(call_id for call_id, name in call_names.items() if not is_read_only_tool_name(name))
            protected = bool(write_call_ids or any(name in _PROTECTED_TOOL_NAMES for name in call_names.values()))
        else:
            write_call_ids = ()
            protected = False
        groups.append(
            _MessageGroup(
                group_start,
                index,
                tuple(messages[group_start:index]),
                protected,
                write_call_ids,
            )
        )
    return groups


def _header_and_request(messages: list, request_text: str | None) -> tuple[int, int | None, UserMessage]:
    """Find this active Request's header and original user input, including a confirmation resume."""
    headers = [index for index, message in enumerate(messages) if _part(message) == EXECUTION_HEADER]
    if not headers:
        raise ValueError("current execution header is missing")
    header_index = headers[-1]
    query_index = None
    for index in range(header_index + 1, len(messages)):
        message = messages[index]
        if _part(message) == EXECUTION_END:
            break
        if not isinstance(message, UserMessage) or _part(message) in (TEMPORARY_BACKGROUND, HANDOFF_TEXT):
            continue
        if _prompt_attachment(message):
            continue
        if request_text is None or _as_text(message) == request_text:
            query_index = index
            break
    if query_index is not None:
        original = messages[query_index]
        if request_text is not None and isinstance(original.content, str):
            original = UserMessage(content=request_text, metadata=dict(original.metadata or {}))
        else:
            original = deepcopy(original)
    elif request_text:
        original = UserMessage(content=request_text)
    else:
        raise ValueError("original Request input is missing")
    return header_index, query_index, original


def _message_ref(group: _MessageGroup) -> str:
    return f"messages:{group.start}-{group.end - 1}"


def _pause(reason: str, detail: str) -> dict:
    return {"result_type": PAUSE_MARKER, "reason": reason, "detail": detail, "output": ""}


def restore_rollover_candidate(
    old_messages: list,
    seed: WindowSeed,
    retained_item_refs: list[str],
) -> list:
    """Rebuild the committed candidate from the archived source window and exact retained references."""
    header_index, query_index, original_request = _header_and_request(old_messages, None)
    if query_index is None:
        raise ValueError("archived Request input is missing")
    group_start = query_index + 1
    group_end = next(
        (index for index in range(group_start, len(old_messages)) if _part(old_messages[index]) == EXECUTION_END),
        len(old_messages),
    )
    groups = [
        group
        for group in _message_groups(old_messages, group_start, group_end)
        if not any(_prompt_attachment(message) for message in group.messages)
    ]
    group_by_ref = {_message_ref(group): group for group in groups}
    attachments_by_ref = {
        f"message:{index}": deepcopy(message)
        for index, message in enumerate(old_messages[header_index + 1 : group_end], start=header_index + 1)
        if _prompt_attachment(message)
    }
    if len(retained_item_refs) != len(set(retained_item_refs)):
        raise ValueError("retained references contain duplicates")
    reference_set = set(retained_item_refs)
    if not set(attachments_by_ref).issubset(reference_set):
        raise ValueError("archived prompt attachment references are incomplete")
    if reference_set - set(group_by_ref) - set(attachments_by_ref):
        raise ValueError("retained reference does not identify an archived complete item")

    attachments = [attachments_by_ref[ref] for ref in retained_item_refs if ref in attachments_by_ref]
    selected_groups = sorted(
        (group_by_ref[ref] for ref in retained_item_refs if ref in group_by_ref), key=lambda group: group.start
    )
    seed_message = UserMessage(
        content=_SEED_PREFIX + json.dumps(seed.model_dump(mode="json"), ensure_ascii=False, sort_keys=True),
        metadata={WINDOW_PART_KEY: TEMPORARY_BACKGROUND},
    )
    base = [deepcopy(old_messages[header_index]), *attachments, seed_message, original_request]
    return [*base, *(deepcopy(message) for group in selected_groups for message in group.messages)]


class WindowSwitchManager:
    """One PA-11 coordinator for both Agent requests and exact-capacity fallback."""

    def __init__(
        self,
        context: "PersistentBuildContext",
        request_text: str | None,
        spec: "PersistentAgentSpec",
        state_store,
        emitter: "EventEmitter",
        *,
        last_group_message_id: str | None,
    ) -> None:
        self._context = context
        self._request_text = request_text
        self._spec = spec
        self._state_store = state_store
        self._emitter = emitter
        self._last_group_message_id = last_group_message_id
        self._limits = capacity.capacity_limits(spec)
        self._prepared: _PreparedRollover | None = None

    async def after_react_iteration(self, ctx: "AgentCallbackContext | ExecutionControl") -> None:
        """Mark a complete tool iteration; no persistence occurs while a batch is running."""
        control = ctx if isinstance(ctx, ExecutionControl) else self._context.binding.control
        iteration = getattr(ctx, "pending_iteration_seq", 0) if isinstance(ctx, ExecutionControl) else 0
        if not isinstance(ctx, ExecutionControl):
            iteration = getattr(ctx, "extra", {}).get("_react_iteration", 0)
            iteration = iteration or getattr(getattr(ctx, "inputs", None), "react_iteration", 0)
        control.pending_iteration_seq = max(control.pending_iteration_seq, int(iteration or 0))
        control.safe_boundary_pending = True

    async def before_model_request(self, ctx: "AgentCallbackContext") -> dict | None:
        """Checkpoint or switch after the prior batch, immediately before the next model call."""
        control = self._context.binding.control
        if self._prepared is not None:
            return await self._finish_prepared(ctx)
        if not control.safe_boundary_pending:
            return None

        # The final request belongs to the *next* iteration. Use the completed iteration
        # captured by AFTER_REACT_ITERATION, never that next-call number.
        iteration_seq = control.pending_iteration_seq
        if iteration_seq < 1:
            return _pause("error", "无法确认完整 ReAct 轮次，已暂停以避免不安全地保存或换窗。")

        request = control.pending_window_switch
        current_tokens = self._count_request(ctx)
        if request is None:
            if self._limits is None:
                await self._checkpoint(control, iteration_seq)
                return None
            if current_tokens is None:
                return _pause("error", "当前模型请求无法可靠计数，已暂停；不会按估算结果换窗。")
            if current_tokens <= self._limits.available_input:
                await self._checkpoint(control, iteration_seq)
                return None
            try:
                seed = self._fallback_seed(ctx)
            except Exception as exc:
                logger.warning(
                    "[PersistentAgent] PA-11 fallback seed preparation failed: execution=%s error=%s",
                    self._context.execution.execution_id,
                    type(exc).__name__,
                )
                return _pause("error", "无法安全生成容量兜底交接要点，已暂停；当前窗口未切换。")
            request = WindowSwitchRequest(trigger_kind="capacity_fallback", seed=seed)

        return await self._prepare(ctx, request, iteration_seq)

    async def _checkpoint(self, control: ExecutionControl, iteration_seq: int) -> None:
        execution = self._context.execution
        try:
            await self._state_store.checkpoint(
                self._context,
                SessionCheckpoint(
                    request_id=execution.request_id,
                    execution_id=execution.execution_id,
                    iteration_seq=iteration_seq,
                    kind="react_iteration",
                    last_group_message_id=self._last_group_message_id,
                ),
            )
        except Exception as exc:
            error = build_error(
                StatusCode.DEEPAGENT_RUNTIME_ERROR,
                cause=exc,
                error_msg="PA-10 ReAct 检查点写入失败；本轮之后不再调用模型",
            )
            control.mark_fatal(error)
            raise error from exc
        control.safe_boundary_pending = False
        control.pending_iteration_seq = 0

    def _count_request(self, ctx: "AgentCallbackContext") -> int | None:
        try:
            counter = ctx.context.token_counter() if ctx.context is not None else None
        except Exception:  # noqa: BLE001 - an unavailable counter means capacity is unknown
            return None
        request = self._spec.model.model_request_config if self._spec.model is not None else None
        model = str(getattr(request, "model_name", "") or "")
        return capacity.count_request(
            counter,
            list(ctx.inputs.messages or []),
            ctx.inputs.tools,
            model,
        )

    def _count_candidate(self, ctx: "AgentCallbackContext", messages: list) -> int | None:
        window = ctx.extra.get("_final_request_context_window")
        if window is None:
            return None
        try:
            counter = ctx.context.token_counter() if ctx.context is not None else None
        except Exception:  # noqa: BLE001 - an unavailable counter means capacity is unknown
            return None
        request = self._spec.model.model_request_config if self._spec.model is not None else None
        model = str(getattr(request, "model_name", "") or "")
        return capacity.count_request(
            counter,
            [*window.system_messages, *messages],
            window.tools,
            model,
        )

    def _fallback_seed(self, ctx: "AgentCallbackContext") -> WindowSeed:
        messages = list(ctx.context.get_messages())
        header, query, _ = _header_and_request(messages, self._request_text)
        end = query + 1 if query is not None else header + 1
        while end < len(messages) and _part(messages[end]) != EXECUTION_END:
            end += 1
        groups = _message_groups(messages, query + 1 if query is not None else header + 1, end)
        pending = [f"tool_call:{call_id}" for group in groups for call_id in group.write_call_ids]
        evidence = [_message_ref(group) for group in groups if group.protected]
        objective = self._request_text or (_as_text(messages[query]) if query is not None else None)
        if not objective:
            objective = "继续当前 Request；先核对下方保留的工具结果和未确认事项。"
        return WindowSeed(
            objective=objective[:2048],
            next_steps=[_FALLBACK_NEXT_STEP],
            constraints_and_decisions=["外部副作用状态以工具结果和宿主执行记录为准。"],
            pending_effects=pending[:32],
            evidence_refs=evidence[:32],
        )

    async def _prepare(
        self,
        ctx: "AgentCallbackContext",
        request: WindowSwitchRequest,
        iteration_seq: int,
    ) -> dict | None:
        control = self._context.binding.control
        old_messages = list(ctx.context.get_messages())
        try:
            candidate, refs = self._build_candidate(ctx, old_messages, request.seed)
        except _RequiredContextUnfit:
            control.pending_window_switch = None
            return _pause("context_unfit", "新窗口的任务和必须保留内容超过可用输入上限；未截断信息，也未切换。")
        except _ContextCountUnavailable:
            control.pending_window_switch = None
            return _pause("error", "新窗口必要内容无法可靠计数，已暂停；不会使用估算值继续。")
        except Exception as exc:
            logger.warning(
                "[PersistentAgent] PA-11 candidate preparation failed: execution=%s error=%s",
                self._context.execution.execution_id,
                type(exc).__name__,
            )
            control.pending_window_switch = None
            return _pause("error", "无法安全准备新窗口，已暂停；当前窗口与存储记录未切换。")

        needed = self._count_candidate(ctx, candidate)
        if needed is None:
            control.pending_window_switch = None
            return _pause("error", "新窗口请求无法可靠计数，已暂停；不会使用估算值继续。")
        if self._limits is None or needed > self._limits.available_input:
            control.pending_window_switch = None
            available = self._limits.available_input if self._limits is not None else 0
            return _pause(
                "context_unfit",
                f"新窗口的必要内容需要 {needed} 个输入 token，可用上限为 {available}；未删除必要信息，也未切换。",
            )

        self._prepared = _PreparedRollover(
            old_messages=tuple(old_messages),
            new_messages=tuple(candidate),
            seed=request.seed,
            trigger_kind=request.trigger_kind,
            retained_item_refs=tuple(refs),
            iteration_seq=iteration_seq,
        )
        # This is a provisional in-memory candidate only. The native loop rebuilds and
        # re-counts the exact request; failure restores old_messages before ending.
        ctx.context.set_messages(candidate)
        ctx.request_context_rebuild()
        return None

    def _build_candidate(
        self,
        ctx: "AgentCallbackContext",
        old_messages: list,
        seed: WindowSeed,
    ) -> tuple[list, list[str]]:
        if not old_messages:
            raise ValueError("current window is empty")
        header_index, query_index, original_request = _header_and_request(old_messages, self._request_text)
        group_start = query_index + 1 if query_index is not None else header_index + 1
        group_end = next(
            (index for index in range(group_start, len(old_messages)) if _part(old_messages[index]) == EXECUTION_END),
            len(old_messages),
        )
        groups = [
            group
            for group in _message_groups(old_messages, group_start, group_end)
            if not any(_prompt_attachment(message) for message in group.messages)
        ]
        attachments = [
            deepcopy(message) for message in old_messages[header_index + 1 : group_end] if _prompt_attachment(message)
        ]
        seed_message = UserMessage(
            content=_SEED_PREFIX + json.dumps(seed.model_dump(mode="json"), ensure_ascii=False, sort_keys=True),
            metadata={WINDOW_PART_KEY: TEMPORARY_BACKGROUND},
        )
        base = [deepcopy(old_messages[header_index]), *attachments, seed_message, original_request]
        protected = [group for group in groups if group.protected]
        optional = [group for group in groups if not group.protected]
        selected = list(protected)
        required = [*base, *(deepcopy(message) for group in selected for message in group.messages)]
        required_count = self._count_candidate(ctx, required)
        if required_count is None:
            raise _ContextCountUnavailable("required candidate content cannot be counted reliably")
        if self._limits is None or required_count > self._limits.available_input:
            raise _RequiredContextUnfit("required candidate content exceeds the configured input capacity")

        for group in reversed(optional):
            proposed = [*required, *(deepcopy(message) for message in group.messages)]
            measured = self._count_candidate(ctx, proposed)
            if measured is None:
                raise _ContextCountUnavailable("recent message group cannot be counted reliably")
            if measured > self._limits.available_input:
                break
            selected.append(group)
            required = proposed

        selected.sort(key=lambda group: group.start)
        candidate = [*base, *(deepcopy(message) for group in selected for message in group.messages)]
        refs = [_message_ref(group) for group in selected]
        refs.extend(f"message:{index}" for index, message in enumerate(old_messages) if _prompt_attachment(message))
        return candidate, refs

    async def _finish_prepared(self, ctx: "AgentCallbackContext") -> dict | None:
        prepared = self._prepared
        assert prepared is not None
        control = self._context.binding.control
        needed = self._count_request(ctx)
        if needed is None:
            self._restore_old_context(ctx, prepared)
            self._prepared = None
            control.pending_window_switch = None
            return _pause("error", "重建后的模型请求无法可靠计数，已暂停；换窗没有提交。")
        if self._limits is None or needed > self._limits.available_input:
            self._restore_old_context(ctx, prepared)
            self._prepared = None
            control.pending_window_switch = None
            available = self._limits.available_input if self._limits is not None else 0
            return _pause(
                "context_unfit",
                f"新窗口完整请求需要 {needed} 个输入 token，可用上限为 {available}；必要内容未被截断。",
            )

        token_usage = {
            "candidate_input_tokens": needed,
            "input_limit": self._limits.available_input,
            "context_window": self._limits.context_window,
            "output_reserve": self._limits.output_reserve,
            "safety_margin": self._limits.safety_margin,
        }
        transition = WindowRollover(
            trigger_kind=prepared.trigger_kind,
            seed=prepared.seed,
            old_messages=prepared.old_messages,
            new_messages=prepared.new_messages,
            retained_item_refs=prepared.retained_item_refs,
            token_usage=token_usage,
            iteration_seq=prepared.iteration_seq,
            last_group_message_id=self._last_group_message_id,
        )
        try:
            committed = await self._state_store.rollover(self._context, transition)
        except Exception as exc:
            self._restore_old_context(ctx, prepared)
            error = build_error(
                StatusCode.DEEPAGENT_RUNTIME_ERROR,
                cause=exc,
                error_msg="PA-11 旧窗口归档或新窗口状态提交失败；当前执行已停止",
            )
            control.mark_fatal(error)
            self._prepared = None
            raise error from exc

        control.safe_boundary_pending = False
        control.pending_iteration_seq = 0
        control.pending_window_switch = None
        control.rollover_committed = True
        self._prepared = None
        try:
            await self._emitter.emit(
                self._window_switched_event(),
                {"archive_ref": committed.archive_ref, "window_seq": committed.new_window_seq},
            )
        except Exception as exc:  # ordinary notification never rolls back a committed state
            logger.warning(
                "[PersistentAgent] window.switched notification failed after commit: execution=%s error=%s",
                self._context.execution.execution_id,
                type(exc).__name__,
            )
        return None

    def _restore_old_context(self, ctx: "AgentCallbackContext", prepared: _PreparedRollover) -> None:
        if ctx.context is not None:
            ctx.context.set_messages(list(prepared.old_messages))

    @staticmethod
    def _window_switched_event():
        from ..schema.events import EventType

        return EventType.WINDOW_SWITCHED

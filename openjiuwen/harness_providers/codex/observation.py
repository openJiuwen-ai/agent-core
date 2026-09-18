# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Observe the model requests of one Codex thread.

Codex App Server notifications stream a turn's output, but not the requests
behind it. Codex's opt-in rollout trace (``CODEX_ROLLOUT_TRACE_ROOT``) records
every inference with its ``inference_call_id``, exact wall-clock window, full
request payload and response payload; :class:`CodexRequestObserver` tails it
and reports each inference as one ``ModelRequestEvent``.

Tool items an inference caused are held back until that inference's request
event is out, so the observation stream stays in causal order. When no rollout
record arrives, the raw ``rawResponse/completed`` notifications still report
each response from the output side alone (``input_observed=False``).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from openjiuwen.harness_protocol import (
    ContentBlock,
    ItemEventKind,
    ItemLifecycleEvent,
    MessageRole,
    ModelRequestEvent,
    ModelRequestStatus,
    TurnError,
    TurnMessage,
    TurnUsage,
)
from openjiuwen.harness_providers.base import logger
from openjiuwen.harness_providers.codex.mapping import MappedCodexEvent
from openjiuwen.harness_providers.codex.rollout_trace import CodexRolloutTraceReader
from openjiuwen.harness_providers.jsonsafe import to_json_safe

EmitFn = Callable[[Any, str | None, tuple[str, ...], float | None], Awaitable[None]]

ROLLOUT_TRACE_ROOT_ENV = "CODEX_ROLLOUT_TRACE_ROOT"
_DATA_NAMESPACE = "codex"
_DEFAULT_WAIT_S = 5.0
_DRAIN_INTERVAL_S = 0.25
_TERMINAL_INFERENCE_TYPES = {
    "inference_completed": ModelRequestStatus.COMPLETED,
    "inference_failed": ModelRequestStatus.FAILED,
    "inference_cancelled": ModelRequestStatus.CANCELLED,
}
_TOOL_CALL_TYPES = frozenset(
    {"function_call", "custom_tool_call", "local_shell_call", "mcp_tool_call", "tool_search_call"},
)
_TOOL_OUTPUT_TYPES = frozenset(
    {"function_call_output", "custom_tool_call_output", "local_shell_call_output", "mcp_tool_call_output"},
)
_TEXT_PART_TYPES = frozenset({"input_text", "output_text", "text", "summary_text", "reasoning_text"})
_SYSTEM_ROLES = frozenset({"system", "developer"})
# Codex offers its tools as an input item rather than a request field; the
# catalogue is a tool definition, not something the model said.
_TOOL_CATALOGUE_TYPE = "additional_tools"
# Responses kept to rebuild a request that only sends the input added since
# its ``previous_response_id``; each chain only ever continues its latest one.
_RESPONSE_HISTORY_LIMIT = 8


@dataclass
class _Inference:
    """One rollout inference of the current turn."""

    call_id: str
    started: dict[str, Any] | None = None
    terminal: dict[str, Any] | None = None
    emitted: bool = False

    @property
    def started_ms(self) -> int:
        return int((self.started or {}).get("wall_time_unix_ms") or 0)


@dataclass
class _RawResponse:
    """Output-side view of one model response from raw notifications."""

    response_id: str
    started_at: float
    ended_at: float
    items: list[Any] = field(default_factory=list)
    usage: Any = None


@dataclass
class _HeldItem:
    """A tool item waiting for the request event of the inference that caused it."""

    payload: ItemLifecycleEvent
    item_id: str | None
    observed_at: float


class CodexRequestObserver:
    """Report each model request of a Codex thread as a protocol event.

    Args:
        wait_s: How long a tool item waits for the rollout record of the
            inference that caused it, and a turn for its last records.
    """

    def __init__(self, *, wait_s: float = _DEFAULT_WAIT_S) -> None:
        self._wait_s = wait_s
        self._reader: CodexRolloutTraceReader | None = None
        self._thread_id: str | None = None
        self._lock = asyncio.Lock()
        self._changed = asyncio.Event()
        self._emit: EmitFn | None = None
        self._drain_task: asyncio.Task[None] | None = None
        # Whether this thread's rollout trace has delivered anything yet, and
        # whether a response went unrecorded by it: a Codex build without
        # rollout tracing never writes one, and waiting on it every turn
        # would only delay reporting.
        self._rollout_seen = False
        self._rollout_silent = False
        self._response_history: dict[str, list[Any]] = {}
        self._reset_turn()

    @property
    def rollout_attached(self) -> bool:
        """Return whether rollout records are expected for this thread."""
        return self._reader is not None and (self._rollout_seen or not self._rollout_silent)

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    async def attach(self) -> dict[str, str]:
        """Start tailing a private rollout trace root.

        Returns:
            Env the App Server process needs to write its rollout trace; empty
            when the reader cannot start, in which case requests are reported
            from raw notifications alone.
        """
        try:
            self._reader = await CodexRolloutTraceReader.start(self._on_rollout_event)
        except Exception as exc:  # noqa: BLE001 - observation must not block startup
            logger.warning("[codex] rollout trace unavailable; reporting requests from raw events: %s", exc)
            self._reader = None
            return {}
        return {ROLLOUT_TRACE_ROOT_ENV: str(self._reader.root)}

    def bind_thread(self, thread_id: str | None) -> None:
        """Only accept rollout records of ``thread_id``."""
        self._thread_id = thread_id

    async def close(self) -> None:
        """Stop tailing and remove the rollout root."""
        await self._stop_drain()
        reader = self._reader
        self._reader = None
        if reader is not None:
            await reader.aclose()

    # ------------------------------------------------------------------
    # Turn lifecycle
    # ------------------------------------------------------------------

    def begin_turn(self, emit: EmitFn) -> None:
        """Start observing a turn whose events go out through ``emit``."""
        self._reset_turn()
        self._turn_started_ms = int(time.time() * 1000)
        self._emit = emit
        if self.rollout_attached:
            self._drain_task = asyncio.create_task(self._drain(), name="codex_request_observer_drain")

    async def observe(self, notification: Any, mapped: list[MappedCodexEvent]) -> None:
        """Emit the events one notification mapped to, holding tool items as needed."""
        now = time.time()
        async with self._lock:
            self._note_raw_notification(notification, now)
            for event in mapped:
                payload = event.payload
                if isinstance(payload, ItemLifecycleEvent) and payload.item_type == "tool":
                    self._held.append(_HeldItem(payload=payload, item_id=event.item_id, observed_at=now))
                else:
                    await self._emit_event(payload, event.item_id, (), None)
            await self._flush(force=False)

    async def end_turn(self, *, wait: bool) -> None:
        """Report everything the turn still owes before its terminal event.

        Args:
            wait: Give the rollout trace until the turn's end record (or the
                wait budget) to deliver its last inferences.
        """
        await self._stop_drain()
        if wait and self.rollout_attached:
            deadline = time.time() + self._wait_s
            while not self._rollout_turn_ended and time.time() < deadline:
                async with self._lock:
                    await self._flush(force=False)
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._changed.wait(), timeout=_DRAIN_INTERVAL_S)
                self._changed.clear()
        async with self._lock:
            await self._flush(force=True)
        self._emit = None

    # ------------------------------------------------------------------
    # Inputs
    # ------------------------------------------------------------------

    def _reset_turn(self) -> None:
        self._turn_started_ms = 0
        self._codex_turn_id: str | None = None
        self._rollout_turn_ended = False
        self._inferences: dict[str, _Inference] = {}
        self._response_ids: set[str] = set()
        self._emitted_requests: set[str] = set()
        self._tool_owners: dict[str, str] = {}
        self._tool_aliases: dict[str, str] = {}
        self._cell_calls: dict[str, str] = {}
        self._item_owners: dict[str, str | None] = {}
        self._raw_items: list[Any] = []
        self._raw_boundary = time.time()
        self._raw_responses: list[_RawResponse] = []
        self._held: deque[_HeldItem] = deque()

    def _on_rollout_event(self, event: dict[str, Any]) -> None:
        """Accept one rollout record; the reader delivers on the event loop."""
        payload = event.get("payload")
        if not isinstance(payload, dict):
            return
        thread_id = str(event.get("thread_id") or payload.get("thread_id") or "")
        if self._thread_id and thread_id and thread_id != self._thread_id:
            return
        if int(event.get("wall_time_unix_ms") or 0) < self._turn_started_ms:
            return
        codex_turn_id = str(event.get("codex_turn_id") or payload.get("codex_turn_id") or "")
        if self._codex_turn_id and codex_turn_id and codex_turn_id != self._codex_turn_id:
            return
        if codex_turn_id and self._codex_turn_id is None:
            self._codex_turn_id = codex_turn_id
        event_type = str(payload.get("type") or "")
        if event_type == "code_cell_started":
            self._note_code_cell(payload)
        elif event_type == "tool_call_started":
            self._note_tool_call(payload)
        elif event_type == "codex_turn_ended":
            self._rollout_turn_ended = True
        elif event_type == "inference_started" or event_type in _TERMINAL_INFERENCE_TYPES:
            call_id = str(payload.get("inference_call_id") or "")
            if not call_id:
                return
            inference = self._inferences.setdefault(call_id, _Inference(call_id=call_id))
            if event_type == "inference_started":
                inference.started = event
            else:
                inference.terminal = event
        else:
            return
        self._rollout_seen = True
        self._changed.set()

    def _note_code_cell(self, payload: dict[str, Any]) -> None:
        cell_id = str(payload.get("runtime_cell_id") or "")
        call_id = str(payload.get("model_visible_call_id") or "")
        if cell_id and call_id:
            self._cell_calls[cell_id] = call_id

    def _note_tool_call(self, payload: dict[str, Any]) -> None:
        """Join a runtime tool call to the model-visible call that requested it.

        The App Server names a tool item after its runtime call (``exec-...``),
        while the model asked for it under a ``call_...`` id; a tool run from
        a code cell names only the cell that ran it.
        """
        tool_call_id = str(payload.get("tool_call_id") or "")
        call_id = str(payload.get("model_visible_call_id") or "")
        requester = payload.get("requester")
        if not call_id and isinstance(requester, dict):
            call_id = self._cell_calls.get(str(requester.get("runtime_cell_id") or ""), "")
        if tool_call_id and call_id:
            self._tool_aliases[tool_call_id] = call_id

    def _note_raw_notification(self, notification: Any, now: float) -> None:
        method = str(getattr(notification, "method", "") or "")
        payload = getattr(notification, "payload", None)
        if method == "rawResponseItem/completed":
            self._raw_items.append(_raw_param(payload, "item"))
        elif method == "rawResponse/completed":
            response_id = str(_raw_param(payload, "responseId") or "")
            if response_id:
                self._raw_responses.append(
                    _RawResponse(
                        response_id=response_id,
                        started_at=self._raw_boundary,
                        ended_at=now,
                        items=self._raw_items,
                        usage=_raw_param(payload, "usage"),
                    )
                )
            self._raw_items = []
            self._raw_boundary = now

    # ------------------------------------------------------------------
    # Ordered emission
    # ------------------------------------------------------------------

    async def _drain(self) -> None:
        while True:
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._changed.wait(), timeout=_DRAIN_INTERVAL_S)
            self._changed.clear()
            try:
                async with self._lock:
                    await self._flush(force=False)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.debug("[codex] model request observation flush failed", exc_info=True)

    async def _stop_drain(self) -> None:
        task = self._drain_task
        self._drain_task = None
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _flush(self, *, force: bool) -> None:
        if self.rollout_attached:
            await self._emit_rollout_requests(force=force)
        await self._emit_raw_requests(force=force)
        await self._release_items(force=force)

    async def _emit_rollout_requests(self, *, force: bool) -> None:
        ordered = sorted(
            (inference for inference in self._inferences.values() if inference.started is not None),
            key=lambda inference: inference.started_ms,
        )
        for inference in ordered:
            if inference.emitted:
                continue
            if inference.terminal is None:
                if not force:
                    # A later inference must not be reported before an
                    # earlier one that is still running.
                    return
                continue
            inference.emitted = True
            terminal_payload = inference.terminal.get("payload")
            response_id = str(terminal_payload.get("response_id") or "") if isinstance(terminal_payload, dict) else ""
            if response_id and response_id in self._response_ids:
                # Already reported from raw events after the rollout record
                # ran late; reporting it again would duplicate the request.
                continue
            await self._emit_request(self._rollout_request(inference))
            await self._release_items(force=False)

    async def _emit_raw_requests(self, *, force: bool) -> None:
        now = time.time()
        remaining: list[_RawResponse] = []
        for raw in self._raw_responses:
            if raw.response_id in self._response_ids:
                continue
            overdue = raw.ended_at + self._wait_s <= now
            if self.rollout_attached and not force and not overdue:
                remaining.append(raw)
                continue
            if overdue and not self._rollout_seen:
                self._rollout_silent = True
            self._response_ids.add(raw.response_id)
            await self._emit_request(_raw_request(raw, self._tool_owners))
        self._raw_responses = remaining

    async def _emit_request(self, event: ModelRequestEvent) -> None:
        await self._emit_event(event, None, (), None)
        self._emitted_requests.add(event.request_id)

    async def _release_items(self, *, force: bool) -> None:
        while self._held:
            head = self._held[0]
            owner = self._owner_of(head)
            waiting = owner is None and head.payload.kind is ItemEventKind.STARTED
            if waiting and self.rollout_attached and not force and time.time() < head.observed_at + self._wait_s:
                return
            self._held.popleft()
            if head.payload.kind is ItemEventKind.STARTED:
                self._item_owners[head.item_id or ""] = owner
            causes = (owner,) if owner else ()
            await self._emit_event(head.payload, head.item_id, causes, head.observed_at)

    def _owner_of(self, held: _HeldItem) -> str | None:
        item_id = held.item_id or ""
        if held.payload.kind is not ItemEventKind.STARTED:
            return self._item_owners.get(item_id)
        owner = self._tool_owners.get(self._tool_aliases.get(item_id, item_id))
        return owner if owner in self._emitted_requests else None

    async def _emit_event(
        self,
        payload: Any,
        item_id: str | None,
        causation_ids: tuple[str, ...],
        timestamp: float | None,
    ) -> None:
        emit = self._emit
        if emit is None:
            return
        await emit(payload, item_id, causation_ids, timestamp)

    # ------------------------------------------------------------------
    # Request construction
    # ------------------------------------------------------------------

    def _rollout_request(self, inference: _Inference) -> ModelRequestEvent:
        started = inference.started or {}
        terminal = inference.terminal or {}
        started_payload = started.get("payload") if isinstance(started.get("payload"), dict) else {}
        terminal_payload = terminal.get("payload") if isinstance(terminal.get("payload"), dict) else {}
        request_payload = (started.get("resolved_payloads") or {}).get("request_payload")
        response_payload = (terminal.get("resolved_payloads") or {}).get("response_payload")
        status = _TERMINAL_INFERENCE_TYPES.get(str(terminal_payload.get("type") or ""), ModelRequestStatus.FAILED)
        started_at = inference.started_ms / 1000
        ended_at = max(int(terminal.get("wall_time_unix_ms") or 0) / 1000, started_at)
        response_id = str(terminal_payload.get("response_id") or "")
        if response_id:
            self._response_ids.add(response_id)
        output_items = _response_items(response_payload)
        _register_tool_owners(self._tool_owners, output_items, inference.call_id)
        request = request_payload if isinstance(request_payload, dict) else {}
        conversation = self._full_conversation(request, response_id, output_items)
        instructions = request.get("instructions")
        error = None
        if status is not ModelRequestStatus.COMPLETED:
            error = TurnError(
                message=str(terminal_payload.get("error") or f"Codex inference {status.value}"),
                category="sdk_error",
            )
        usage = response_payload.get("token_usage") if isinstance(response_payload, dict) else None
        return ModelRequestEvent(
            request_id=inference.call_id,
            status=status,
            started_at=started_at,
            ended_at=ended_at,
            model=str(started_payload.get("model") or "") or None,
            provider_name=str(started_payload.get("provider_name") or "") or None,
            system_instructions=(
                (ContentBlock(block_id=f"{inference.call_id}:instructions", kind="text", content=_text(instructions)),)
                if instructions
                else ()
            ),
            input_messages=tuple(_conversation(_without_tool_catalogue(conversation))),
            input_observed=conversation is not None,
            output_message=_output_message(response_id or inference.call_id, output_items),
            tool_definitions=_tool_definitions(request, conversation),
            request_parameters=_request_parameters(request),
            response_id=response_id or None,
            usage=_usage(usage),
            error=error,
            data={
                _DATA_NAMESPACE: {
                    "observation": "rollout",
                    "response_id": response_id or None,
                    "upstream_request_id": str(terminal_payload.get("upstream_request_id") or "") or None,
                    "codex_turn_id": self._codex_turn_id,
                },
            },
        )


    def _full_conversation(self, request: dict[str, Any], response_id: str, output_items: list[Any]) -> list[Any] | None:
        """Return the whole conversation a request continued, or ``None`` when unknown.

        A request chained on ``previous_response_id`` only sends the input
        added since that response; the conversation it continued is that
        response's own conversation plus its output.
        """
        items = request.get("input")
        if not isinstance(items, list):
            return None
        previous_id = request.get("previous_response_id")
        if previous_id:
            previous = self._response_history.get(str(previous_id))
            if previous is None:
                return None
            conversation = previous + items
        else:
            conversation = list(items)
        if response_id:
            self._response_history[response_id] = conversation + list(output_items)
            while len(self._response_history) > _RESPONSE_HISTORY_LIMIT:
                self._response_history.pop(next(iter(self._response_history)))
        return conversation


def _without_tool_catalogue(items: list[Any] | None) -> list[Any] | None:
    """Return the conversation without the tool-catalogue input items."""
    if items is None:
        return None
    return [item for item in items if not (isinstance(item, dict) and item.get("type") == _TOOL_CATALOGUE_TYPE)]


def _tool_definitions(request: dict[str, Any], conversation: list[Any] | None) -> Any:
    """Return the offered tools as flat ``{name, description, parameters}``.

    Codex states its tools either as a request field or as catalogue input
    items, and groups them into namespaces; a reader of a tool schema wants
    each callable tool, named as the model addresses it.
    """
    offered = request.get("tools")
    if not isinstance(offered, list) or not offered:
        offered = [
            tool
            for item in (conversation or [])
            if isinstance(item, dict) and item.get("type") == _TOOL_CATALOGUE_TYPE
            for tool in (item.get("tools") or [])
        ]
    definitions = _flatten_tools(offered, namespace="")
    return definitions or None


def _flatten_tools(tools: Any, *, namespace: str) -> list[dict[str, Any]]:
    definitions: list[dict[str, Any]] = []
    if not isinstance(tools, list):
        return definitions
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        name = str(tool.get("name") or "")
        if tool.get("type") == "namespace":
            definitions.extend(_flatten_tools(tool.get("tools"), namespace=name))
            continue
        if not name:
            continue
        schema = tool.get("parameters") or tool.get("input_schema") or {}
        definitions.append({
            "name": f"{namespace}.{name}" if namespace else name,
            "description": str(tool.get("description") or ""),
            "parameters": to_json_safe(schema),
        })
    return definitions


def _request_parameters(request: dict[str, Any]) -> dict[str, Any]:
    """Return the sampling parameters the request carried, under GenAI names."""
    parameters: dict[str, Any] = {}
    stream = request.get("stream")
    if stream is not None:
        parameters["stream"] = to_json_safe(stream)
    reasoning = request.get("reasoning")
    effort = reasoning.get("effort") if isinstance(reasoning, dict) else None
    if isinstance(effort, str) and effort:
        parameters["reasoning_level"] = effort
    for source, name in (("temperature", "temperature"), ("top_p", "top_p"), ("max_output_tokens", "max_tokens")):
        value = request.get(source)
        if value is not None:
            parameters[name] = to_json_safe(value)
    return parameters


def _raw_request(raw: _RawResponse, tool_owners: dict[str, str]) -> ModelRequestEvent:
    items = [to_json_safe(item) for item in raw.items]
    _register_tool_owners(tool_owners, items, raw.response_id)
    return ModelRequestEvent(
        request_id=raw.response_id,
        status=ModelRequestStatus.COMPLETED,
        started_at=raw.started_at,
        ended_at=max(raw.ended_at, raw.started_at),
        output_message=_output_message(raw.response_id, items),
        usage=_usage(to_json_safe(raw.usage)),
        data={_DATA_NAMESPACE: {"observation": "raw_events", "response_id": raw.response_id}},
    )


def _register_tool_owners(tool_owners: dict[str, str], items: list[Any], request_id: str) -> None:
    """Record which request asked for each tool call an output item names."""
    for item in items:
        if not isinstance(item, dict) or item.get("type") not in _TOOL_CALL_TYPES:
            continue
        for key in (item.get("call_id"), item.get("id")):
            if key:
                tool_owners[str(key)] = request_id


def _raw_param(payload: Any, name: str) -> Any:
    """Read one field from an SDK raw-event payload, camelCase or snake_case."""
    params = getattr(payload, "params", payload)
    if isinstance(params, dict):
        return params.get(name)
    value = getattr(params, name, None)
    if value is not None:
        return value
    snake_name = "".join(f"_{char.lower()}" if char.isupper() else char for char in name)
    return getattr(params, snake_name, None)


def _response_items(response_payload: Any) -> list[Any]:
    if not isinstance(response_payload, dict):
        return []
    items = response_payload.get("output_items")
    if not isinstance(items, list):
        items = response_payload.get("output")
    return items if isinstance(items, list) else []


def _usage(value: Any) -> TurnUsage | None:
    if not isinstance(value, dict):
        return None

    def counter(*names: str) -> int | None:
        for name in names:
            raw = value.get(name)
            if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0:
                return raw
        return None

    usage = TurnUsage(
        input_tokens=counter("input_tokens", "inputTokens"),
        output_tokens=counter("output_tokens", "outputTokens"),
        cached_input_tokens=counter("cached_input_tokens", "cachedInputTokens"),
        reasoning_output_tokens=counter("reasoning_output_tokens", "reasoningOutputTokens"),
        total_tokens=counter("total_tokens", "totalTokens"),
    )
    if usage.input_tokens is None and usage.output_tokens is None:
        return None
    return usage


def _conversation(items: Any) -> list[TurnMessage]:
    """Convert a Responses-style input item list into protocol messages."""
    if not isinstance(items, list):
        return []
    messages: list[TurnMessage] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        message_id = str(item.get("id") or "") or f"codex-context:{_identity(item)}"
        message = _item_message(message_id, item)
        if message is not None:
            messages.append(message)
    return messages


def _item_message(message_id: str, item: dict[str, Any]) -> TurnMessage | None:
    item_type = str(item.get("type") or "message")
    if item_type in _TOOL_OUTPUT_TYPES:
        block = ContentBlock(
            block_id=f"{message_id}:0",
            kind="tool_result",
            content=_tool_output(item.get("output", item.get("result"))),
            data={"call_id": str(item.get("call_id") or "")},
        )
        return TurnMessage(message_id=message_id, role=MessageRole.TOOL, content=(block,))
    blocks = _item_blocks(message_id, item)
    if not blocks:
        return None
    role = str(item.get("role") or "")
    if role in _SYSTEM_ROLES:
        message_role = MessageRole.SYSTEM
    elif role == "user":
        message_role = MessageRole.USER
    else:
        message_role = MessageRole.ASSISTANT
    return TurnMessage(message_id=message_id, role=message_role, content=tuple(blocks))


def _item_blocks(message_id: str, item: dict[str, Any]) -> list[ContentBlock]:
    item_type = str(item.get("type") or "message")
    if item_type in _TOOL_CALL_TYPES:
        arguments = item.get("arguments", item.get("input", item.get("action")))
        return [
            ContentBlock(
                block_id=f"{message_id}:0",
                kind="tool_call",
                content={"name": str(item.get("name") or item_type), "arguments": to_json_safe(arguments)},
                data={"call_id": str(item.get("call_id") or item.get("id") or "")},
            )
        ]
    if item_type == "reasoning":
        text = "\n".join(part for part in (_text(item.get("summary")), _text(item.get("content"))) if part)
        if not text:
            return []
        return [ContentBlock(block_id=f"{message_id}:0", kind="reasoning", content=text)]
    if item_type == "message":
        content = item.get("content")
        parts = content if isinstance(content, list) else [{"type": "text", "text": content}]
        blocks: list[ContentBlock] = []
        for index, part in enumerate(parts):
            block_id = f"{message_id}:{index}"
            if isinstance(part, dict) and part.get("type") in _TEXT_PART_TYPES:
                blocks.append(ContentBlock(block_id=block_id, kind="text", content=str(part.get("text") or "")))
            elif isinstance(part, str):
                blocks.append(ContentBlock(block_id=block_id, kind="text", content=part))
            elif isinstance(part, dict):
                blocks.append(ContentBlock(block_id=block_id, kind=str(part.get("type") or "unknown"), content=to_json_safe(part)))
        return blocks
    return [ContentBlock(block_id=f"{message_id}:0", kind=item_type, content=to_json_safe(item))]


def _output_message(message_id: str, items: list[Any]) -> TurnMessage | None:
    blocks: list[ContentBlock] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        for block in _item_blocks(f"{message_id}:{index}", item):
            blocks.append(block)
    if not blocks:
        return None
    return TurnMessage(message_id=message_id, role=MessageRole.ASSISTANT, content=tuple(blocks))


def _tool_output(output: Any) -> Any:
    if isinstance(output, dict) and "content" in output:
        output = output["content"]
    text = _text(output) if isinstance(output, list) else None
    return text if text else to_json_safe(output)


def _text(value: Any) -> str:
    """Join the text of a content value; structured non-text parts are dropped."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        texts = [
            str(part.get("text") or "")
            for part in value
            if isinstance(part, dict) and part.get("type") in _TEXT_PART_TYPES
        ]
        return "\n".join(text for text in texts if text)
    return ""


def _identity(item: dict[str, Any]) -> str:
    encoded = json.dumps(to_json_safe(item), ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:32]


__all__ = ["CodexRequestObserver", "EmitFn", "ROLLOUT_TRACE_ROOT_ENV"]

# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Observe the model requests of one Codex thread.

Codex App Server notifications stream a turn's output, but not the requests
behind it. Two provider-private channels fill that in, and they answer
different questions:

* The opt-in rollout trace (``CODEX_ROLLOUT_TRACE_ROOT``) is the **content**:
  every inference with its ``inference_call_id``, exact wall-clock window,
  full request payload and response payload. Nothing else carries the bodies.
* The CLI's own OTLP telemetry is the **facts**: what a tool call was given
  and returned, how long it ran, whether it succeeded, whether its output was
  truncated, how it was approved, and the settings the session resolved to.
  Where the CLI states a fact, that statement is used rather than one derived
  from the stream.

:class:`CodexRequestObserver` joins the two by ``call_id`` and reports each
inference as one ``ModelRequestEvent``. Tool items an inference caused are
held back until that inference's request event is out, so the observation
stream stays in causal order. When no rollout record arrives, the raw
``rawResponse/completed`` notifications still report each response from the
output side alone (``input_observed=False``).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, fields
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
    freeze_json_object,
    json_value_to_builtin,
)
from openjiuwen.harness_providers.base import logger
from openjiuwen.harness_providers.codex.mapping import MappedCodexEvent
from openjiuwen.harness_providers.codex.options import codex_otel_config_overrides, namespaced_tool_name
from openjiuwen.harness_providers.codex.rollout_trace import CodexRolloutTraceReader
from openjiuwen.harness_providers.jsonsafe import to_json_object, to_json_safe
from openjiuwen.harness_providers.telemetry.otlp_receiver import get_shared_otlp_receiver

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
_TOOL_SEARCH_OUTPUT_TYPE = "tool_search_output"
_TOOL_CALL_TYPES = frozenset(
    {"function_call", "custom_tool_call", "local_shell_call", "mcp_tool_call", "tool_search_call"},
)
_TOOL_OUTPUT_TYPES = frozenset(
    {
        "function_call_output",
        "custom_tool_call_output",
        "local_shell_call_output",
        "mcp_tool_call_output",
        # The CLI answers its own tool search; the result is still a tool
        # result, not something the assistant said.
        _TOOL_SEARCH_OUTPUT_TYPE,
    },
)
_TEXT_PART_TYPES = frozenset({"input_text", "output_text", "text", "summary_text", "reasoning_text"})
_SYSTEM_ROLES = frozenset({"system", "developer"})
# Codex offers its tools as an input item rather than a request field; the
# catalogue is a tool definition, not something the model said.
_TOOL_CATALOGUE_TYPE = "additional_tools"
# Resource attribute the CLI fills from ``otel.environment``; it is how one
# member claims its own events out of the process-wide receiver.
_OTEL_ENV_RESOURCE_KEY = "env"
_TOOL_RESULT_EVENT = "codex.tool_result"
_TOOL_DECISION_EVENT = "codex.tool_decision"
_CONVERSATION_STARTS_EVENT = "codex.conversation_starts"
# Settings the session resolved to, worth stating once per turn.
_SESSION_FACT_KEYS = (
    "provider_name",
    "reasoning_effort",
    "reasoning_summary",
    "context_window",
    "auto_compact_token_limit",
    "approval_policy",
    "sandbox_policy",
)
# Responses kept to rebuild a request that only sends the input added since
# its ``previous_response_id``; each chain only ever continues its latest one.
_RESPONSE_HISTORY_LIMIT = 8


@dataclass(frozen=True, slots=True)
class CodexObservationAttachment:
    """What one App Server session needs to report into both channels."""

    env: dict[str, str]
    config_overrides: tuple[str, ...]


@dataclass
class _ToolFacts:
    """What the CLI itself reported about one tool call.

    The CLI reports a tool call at both levels it runs one: the call the model
    made (``exec``, ``tool_search``, an MCP tool) and, when that call wraps a
    runtime invocation, the invocation inside it. Both are keyed by their own
    ``call_id``, so they never overwrite each other.
    """

    call_id: str
    tool_name: str | None = None
    namespace: str | None = None
    mcp_server: str | None = None
    arguments: Any = None
    output: Any = None
    duration_ms: int | None = None
    success: bool | None = None
    output_truncated: bool | None = None
    decision: str | None = None
    decision_source: str | None = None

    def merge(self, other: "_ToolFacts") -> None:
        """Fold a later report of the same call in, keeping what is stated."""
        for entry in fields(self):
            if entry.name == "call_id":
                continue
            value = getattr(other, entry.name)
            if value is not None:
                setattr(self, entry.name, value)


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
        # Telemetry channel: one loopback receiver serves every member, and
        # this id is what the CLI echoes back in the resource.
        self._source_id = f"openjiuwen-codex:{uuid.uuid4().hex}"
        self._subscriber_id: int | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._incoming_facts: list[Any] = []
        self._tool_facts: dict[str, _ToolFacts] = {}
        self._session_facts: dict[str, Any] = {}
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

    async def attach(self) -> "CodexObservationAttachment":
        """Open both observation channels for one App Server session.

        Neither channel is required: without the rollout trace requests are
        reported from raw notifications alone, and without the telemetry
        channel the facts fall back to what the notification stream shows.

        Returns:
            What the App Server process needs to report into both channels.
        """
        try:
            self._reader = await CodexRolloutTraceReader.start(self._on_rollout_event)
        except Exception as exc:  # noqa: BLE001 - observation must not block startup
            logger.warning("[codex] rollout trace unavailable; reporting requests from raw events: %s", exc)
            self._reader = None
        env = {ROLLOUT_TRACE_ROOT_ENV: str(self._reader.root)} if self._reader is not None else {}
        return CodexObservationAttachment(env=env, config_overrides=await self._attach_telemetry())

    async def _attach_telemetry(self) -> tuple[str, ...]:
        """Subscribe to the loopback receiver; empty when it cannot serve."""
        receiver = get_shared_otlp_receiver()
        subscriber_id = await receiver.subscribe(self._on_receiver_event)
        endpoint = receiver.base_url
        if subscriber_id is None or not endpoint:
            if subscriber_id is not None:
                receiver.unsubscribe(subscriber_id)
            logger.info("[codex] telemetry unavailable; reporting tool facts from the notification stream")
            return ()
        self._subscriber_id = subscriber_id
        self._loop = asyncio.get_running_loop()
        return codex_otel_config_overrides(endpoint=endpoint, source_id=self._source_id)

    def bind_thread(self, thread_id: str | None) -> None:
        """Only accept rollout records of ``thread_id``."""
        self._thread_id = thread_id

    async def close(self) -> None:
        """Stop both channels and remove the rollout root."""
        await self._stop_drain()
        subscriber_id = self._subscriber_id
        self._subscriber_id = None
        if subscriber_id is not None:
            get_shared_otlp_receiver().unsubscribe(subscriber_id)
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
        # Tool calls the model made, the results the conversation carried back
        # and the calls the App Server announced itself.
        self._model_calls: dict[str, dict[str, Any]] = {}
        self._tool_outputs: dict[str, Any] = {}
        self._covered_calls: set[str] = set()
        self._synthesized: set[str] = set()
        self._raw_items: list[Any] = []
        self._raw_boundary = time.time()
        self._raw_responses: list[_RawResponse] = []
        self._held: deque[_HeldItem] = deque()

    def _on_receiver_event(self, event: dict[str, Any]) -> None:
        """Accept one telemetry event; runs on the receiver's thread or loop."""
        resource = event.get("resource_attributes")
        if not isinstance(resource, dict) or resource.get(_OTEL_ENV_RESOURCE_KEY) != self._source_id:
            return
        raw = event.get("attributes")
        attributes = dict(raw) if isinstance(raw, dict) else {}
        name = str(attributes.get("event.name") or "")
        accepted = _telemetry_observation(name, attributes)
        if accepted is None:
            return
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(self._accept_facts, accepted)

    def _accept_facts(self, observation: Any) -> None:
        self._incoming_facts.append(observation)
        self._changed.set()

    def _absorb_facts(self) -> None:
        """Fold every telemetry event received since the last pass."""
        incoming, self._incoming_facts = self._incoming_facts, []
        for observation in incoming:
            if isinstance(observation, _ToolFacts):
                known = self._tool_facts.get(observation.call_id)
                if known is None:
                    self._tool_facts[observation.call_id] = observation
                else:
                    known.merge(observation)
            else:
                self._session_facts.update(observation)

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
        self._absorb_facts()
        if self.rollout_attached:
            await self._emit_rollout_requests(force=force)
        await self._emit_raw_requests(force=force)
        await self._release_items(force=force)
        await self._emit_uncovered_tools(force=force)

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
            event = self._rollout_request(inference)
            # The results this request carries close their calls before it is
            # reported, so a tool never completes after the request that read
            # its output.
            await self._emit_uncovered_tools(force=False)
            await self._emit_request(event)
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
                item_id = head.item_id or ""
                self._item_owners[item_id] = owner
                # The App Server announced this call, so it needs no stand-in.
                self._covered_calls.add(self._tool_aliases.get(item_id, item_id))
                self._covered_calls.add(item_id)
            causes = (owner,) if owner else ()
            await self._emit_event(self._named_as_the_model_called_it(head), head.item_id, causes, head.observed_at)

    def _named_as_the_model_called_it(self, held: _HeldItem) -> ItemLifecycleEvent:
        """Rename an announced tool item to the name the model asked for.

        The App Server names an item in its own vocabulary -- the runtime it
        ran (``shell``), or the MCP server and tool -- while the model asked
        for one tool by one name, which is also the name its definition
        carries. Naming the item after the call is what lets a reader match
        the two.
        """
        item_id = held.item_id or ""
        call = self._model_calls.get(self._tool_aliases.get(item_id, item_id))
        name = str((call or {}).get("name") or "")
        payload = held.payload
        data = json_value_to_builtin(payload.data)
        if not name or not isinstance(data, dict):
            return payload
        key = "name" if payload.kind is ItemEventKind.STARTED else "tool_name"
        if data.get(key) == name:
            return payload
        announced = str(data.get(key) or "")
        updated = {**data, key: name}
        if announced and announced != name:
            # What the App Server called it stays, since that is the name its
            # own notifications and logs use.
            updated["announced_name"] = announced
        return ItemLifecycleEvent(
            kind=payload.kind,
            item_type=payload.item_type,
            data=freeze_json_object(to_json_object(updated)),
        )

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
    # Tool calls the App Server does not announce
    # ------------------------------------------------------------------

    def _record_tool_calls(self, items: list[Any], *, owner: str, asked_at: float) -> None:
        """Remember every tool call one response made."""
        for item in items:
            if not isinstance(item, dict) or item.get("type") not in _TOOL_CALL_TYPES:
                continue
            call_id = str(item.get("call_id") or item.get("id") or "")
            if not call_id or call_id in self._model_calls:
                continue
            arguments = item.get("arguments", item.get("input", item.get("action")))
            self._model_calls[call_id] = {
                # Empty for a typed call item (a tool search), which names no
                # function; the CLI's own report names those.
                "name": _called_tool_name(item) if item.get("name") else "",
                "arguments": to_json_safe(arguments),
                "item_type": str(item.get("type") or ""),
                "owner": owner,
                "asked_at": asked_at,
            }

    def _record_tool_outputs(self, items: Any) -> None:
        """Remember the results a later request carries for earlier calls."""
        if not isinstance(items, list):
            return
        for item in items:
            if not isinstance(item, dict) or item.get("type") not in _TOOL_OUTPUT_TYPES:
                continue
            call_id = str(item.get("call_id") or "")
            if call_id:
                self._tool_outputs[call_id] = _tool_output(_tool_output_payload(item))

    async def _emit_uncovered_tools(self, *, force: bool) -> None:
        """Report the tool calls the App Server never announced as items.

        Codex answers some calls itself -- its tool search, a code cell that
        invokes nothing -- and reports no thread item for them. Their only
        record is the call in one response and the result in the next request,
        so without this the turn would show the model asking for a tool and
        nothing ever running it.
        """
        for call_id, call in self._model_calls.items():
            if call_id in self._covered_calls or call_id in self._synthesized:
                continue
            owner = str(call.get("owner") or "")
            if owner not in self._emitted_requests:
                continue
            result = self._tool_outputs.get(call_id)
            facts = self._tool_facts.get(call_id)
            settled = result is not None or (facts is not None and facts.output is not None)
            if not settled and not force:
                continue
            self._synthesized.add(call_id)
            await self._emit_uncovered_tool(call_id, call, result, facts)

    async def _emit_uncovered_tool(
        self,
        call_id: str,
        call: dict[str, Any],
        result: Any,
        facts: _ToolFacts | None,
    ) -> None:
        """Emit one unannounced tool call as a started / completed pair.

        The call names itself, since that is how the model addressed the tool
        and how its definition is named; a typed call item (a tool search)
        names no function, and there the CLI's own report names it. Every
        other fact -- how long it ran, whether it succeeded -- is the CLI's.
        """
        name = str(call.get("name") or "")
        if not name and facts is not None and facts.tool_name:
            name = namespaced_tool_name(facts.namespace or "", facts.tool_name)
        name = name or str(call.get("item_type") or "tool")
        started_at = float(call.get("asked_at") or 0.0) or None
        duration_ms = facts.duration_ms if facts is not None else None
        ended_at = started_at
        if started_at is not None and duration_ms is not None:
            ended_at = started_at + duration_ms / 1000
        started: dict[str, Any] = {
            "name": name,
            "arguments": call.get("arguments"),
            "item_type": call.get("item_type"),
            "announced": False,
        }
        if facts is not None and facts.namespace:
            started["tool_namespace"] = facts.namespace
        if facts is not None and facts.mcp_server:
            started["server"] = facts.mcp_server
        if facts is not None and facts.decision:
            started["decision"] = facts.decision
            started["decision_source"] = facts.decision_source
        await self._emit_event(
            ItemLifecycleEvent(kind=ItemEventKind.STARTED, item_type="tool", data=freeze_json_object(started)),
            call_id,
            (str(call.get("owner") or ""),),
            started_at,
        )
        if result is None and facts is not None:
            result = facts.output
        succeeded = facts.success if facts is not None else None
        completed: dict[str, Any] = {
            "tool_name": name,
            "result": to_json_safe(result),
            "item_type": call.get("item_type"),
            "is_error": succeeded is False,
        }
        if facts is not None and facts.output_truncated is not None:
            completed["output_truncated"] = facts.output_truncated
        if duration_ms is not None:
            completed["duration_ms"] = duration_ms
        if succeeded is False:
            completed["error"] = {"status": "failed"}
        await self._emit_event(
            ItemLifecycleEvent(kind=ItemEventKind.COMPLETED, item_type="tool", data=freeze_json_object(completed)),
            call_id,
            (),
            ended_at,
        )

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
        self._record_tool_calls(output_items, owner=inference.call_id, asked_at=ended_at)
        self._record_tool_outputs(conversation if conversation is not None else request.get("input"))
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
                    # What the session actually resolved to, as the CLI stated
                    # it: the effective context window, policies and effort.
                    **to_json_safe(self._session_facts),
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


def _telemetry_observation(name: str, attributes: dict[str, Any]) -> Any:
    """Read one CLI telemetry event; ``None`` for events we observe nothing from.

    The CLI stamps the signed-in account and the terminal on every event. A
    trajectory records what the agent did, never who was logged in, so only
    the named fields below are taken.
    """
    if name in (_TOOL_RESULT_EVENT, _TOOL_DECISION_EVENT):
        call_id = str(attributes.get("call_id") or "")
        if not call_id:
            return None
        if name == _TOOL_DECISION_EVENT:
            return _ToolFacts(
                call_id=call_id,
                tool_name=_text_attribute(attributes.get("tool_name")),
                namespace=_text_attribute(attributes.get("tool_namespace")),
                decision=_text_attribute(attributes.get("decision")),
                decision_source=_text_attribute(attributes.get("source")),
            )
        return _ToolFacts(
            call_id=call_id,
            tool_name=_text_attribute(attributes.get("tool_name")),
            namespace=_text_attribute(attributes.get("tool_namespace")),
            mcp_server=_text_attribute(attributes.get("mcp_server")),
            arguments=_json_attribute(attributes.get("arguments")),
            output=_json_attribute(attributes.get("output")),
            duration_ms=_int_attribute(attributes.get("duration_ms")),
            success=_bool_attribute(attributes.get("success")),
            output_truncated=_bool_attribute(attributes.get("output_truncated")),
        )
    if name == _CONVERSATION_STARTS_EVENT:
        facts = {key: attributes[key] for key in _SESSION_FACT_KEYS if attributes.get(key) is not None}
        return facts or None
    return None


def _text_attribute(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _int_attribute(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _bool_attribute(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    return None


def _json_attribute(value: Any) -> Any:
    """Decode an attribute the CLI rendered as JSON, else keep the text."""
    if not isinstance(value, str):
        return to_json_safe(value) if value is not None else None
    text = value.strip()
    if not text:
        return None
    if text[0] in "{[":
        try:
            return json.loads(text)
        except ValueError:
            return value
    return value


def _without_tool_catalogue(items: list[Any] | None) -> list[Any] | None:
    """Return the conversation without the tool-catalogue input items."""
    if items is None:
        return None
    return [item for item in items if not (isinstance(item, dict) and item.get("type") == _TOOL_CATALOGUE_TYPE)]


def _tool_definitions(request: dict[str, Any], conversation: list[Any] | None) -> Any:
    """Return the tools this request could call, flat and named as its items are.

    Codex states its tools either as a request field or as catalogue input
    items, and groups them into namespaces. It also defers MCP tools: they are
    absent from the catalogue until the model finds them with its tool search,
    and from then on the search result is the only statement of their schemas
    -- so a request whose conversation carries one offers those tools too.
    """
    offered = request.get("tools")
    if not isinstance(offered, list) or not offered:
        offered = [
            tool
            for item in (conversation or [])
            if isinstance(item, dict) and item.get("type") == _TOOL_CATALOGUE_TYPE
            for tool in (item.get("tools") or [])
        ]
    definitions = _flatten_tools(offered)
    known = {definition["name"] for definition in definitions}
    for item in conversation or ():
        if not isinstance(item, dict) or item.get("type") != _TOOL_SEARCH_OUTPUT_TYPE:
            continue
        for definition in _flatten_tools(item.get("tools")):
            if definition["name"] not in known:
                known.add(definition["name"])
                definitions.append(definition)
    return definitions or None


def _flatten_tools(tools: Any, *, namespace: str = "") -> list[dict[str, Any]]:
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
            # The model addresses a tool by its namespace and name, which is
            # how its calls arrive and how a tool item is named here.
            "name": namespaced_tool_name(namespace, name),
            "description": str(tool.get("description") or ""),
            "parameters": to_json_safe(schema),
        })
    return definitions


def _called_tool_name(item: dict[str, Any]) -> str:
    """Return the name a tool call item states, namespace included."""
    name = str(item.get("name") or item.get("type") or "tool")
    return namespaced_tool_name(str(item.get("namespace") or ""), name)


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
            content=_tool_output(_tool_output_payload(item)),
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
                content={"name": _called_tool_name(item), "arguments": to_json_safe(arguments)},
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


def _tool_output_payload(item: dict[str, Any]) -> Any:
    """Return what one tool output item carried back.

    A tool search answers with the catalogue it found, under ``tools``; every
    other tool output carries ``output`` (or ``result``). Reading only the
    latter left a search looking like it returned nothing.
    """
    if item.get("type") == _TOOL_SEARCH_OUTPUT_TYPE and item.get("tools") is not None:
        return item.get("tools")
    return item.get("output", item.get("result"))


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

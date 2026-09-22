# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Observe the model requests of one Claude Code session.

The Claude Agent SDK message stream carries each assistant reply, but not the
request that produced it: the system prompt, the conversation actually sent
and the tools offered live only inside the CLI. Claude Code logs both sides of
every Messages API call as ``api_request_body`` / ``api_response_body`` OTLP
log events; :class:`ClaudeRequestObserver` points that export at the
process-wide loopback receiver, pairs each assembled response with the SDK
assistant message of the same ``msg_`` id and with the request that produced
it, and reports the pair as one ``ModelRequestEvent``.

Tool items a reply caused are held back until that reply's request event is
out, so the observation stream stays in causal order. A request whose logs do
not arrive in time is still reported, built from the SDK message alone with
``input_observed=False``.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import shutil
import tempfile
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from openjiuwen.harness_protocol import (
    ContentBlock,
    ItemEventKind,
    ItemLifecycleEvent,
    MessageRole,
    MonetaryAmount,
    ModelRequestEvent,
    ModelRequestStatus,
    TurnError,
    TurnMessage,
    TurnUsage,
    json_value_to_builtin,
)
from openjiuwen.harness_providers.base import logger
from openjiuwen.harness_providers.claudecode.mapping import ClaudeTurnAccumulator, MappedClaudeEvent, claude_turn_usage
from openjiuwen.harness_providers.claudecode.options import claude_request_log_env
from openjiuwen.harness_providers.jsonsafe import to_json_safe
from openjiuwen.harness_providers.telemetry.otlp_receiver import OTEL_RESOURCE_SOURCE_ID, get_shared_otlp_receiver

EmitFn = Callable[[Any, str | None, tuple[str, ...], float | None], Awaitable[None]]

_REQUEST_BODY_EVENT = "claude_code.api_request_body"
_RESPONSE_BODY_EVENT = "claude_code.api_response_body"
_BODY_EVENTS = frozenset({_REQUEST_BODY_EVENT, _RESPONSE_BODY_EVENT})
# The per-request span of Claude Code's enhanced telemetry: the only place the
# CLI states time-to-first-token, attempts and the exact request window.
_LLM_REQUEST_SPAN = "claude_code.llm_request"
# What the CLI states about one tool call: the execution window on the span,
# the outcome and the permission decision on its logs.
_TOOL_SPAN = "claude_code.tool"
_TOOL_RESULT_EVENT = "claude_code.tool_result"
_TOOL_DECISION_EVENT = "claude_code.tool_decision"
# One model request as the CLI accounts for it: cost and reasoning effort are
# stated nowhere else.
_API_REQUEST_EVENT = "claude_code.api_request"
# The id both body events of one model call carry: what pairs a request log
# with the response log that answers it.
_REQUEST_BODY_ID = "request_body_id"
# How far a log may sit outside a request span's window and still belong to
# it. The CLI logs the response body and its accounting microseconds before
# it closes the span, but the three travel in different OTLP batches, so the
# timestamps are compared with slack rather than exactly.
_UNKEYED_MATCH_WINDOW_NS = 1_000_000_000
# How many unkeyed spans and accounting logs are kept waiting for the reply
# they belong to. A call the CLI abandoned never logs a response body, so its
# span would otherwise sit here for the whole session.
_UNKEYED_HISTORY_LIMIT = 64
# How many replies' conversations are kept for the calls that continue them.
# A thread is linear, so only the newest is ever read; the rest are slack for
# a retry that continues from an earlier reply.
_THREAD_HISTORY_LIMIT = 32
_MODEL_PROVIDER = "anthropic"
_DATA_NAMESPACE = "claude-code"
_DRAIN_INTERVAL_S = 0.25
_DEFAULT_WAIT_S = 5.0
_OMITTED_KEYS = frozenset({"cache_control"})
# Content the CLI redacts before logging a body: a block that states nothing.
_REDACTED_CONTENT = "<REDACTED>"
# Control payloads the CLI attaches to a user turn beside the text that already
# describes them (the tool-availability notice repeats every added tool).
_CONTROL_BLOCK_TYPES = frozenset({"tool_addition", "tool_removal"})
# Claude Code puts its own billing/telemetry header in the first system block.
# It is request metadata rather than instructions, and it carries per-request
# ids that would otherwise make the system prompt read as rewritten every time.
_BILLING_HEADER_PREFIX = "x-anthropic-billing-header:"
# Messages API sampling parameters, mapped to their GenAI names.
_REQUEST_PARAMETERS = {
    "temperature": "temperature",
    "top_p": "top_p",
    "top_k": "top_k",
    "max_tokens": "max_tokens",
    "stop_sequences": "stop_sequences",
    "stream": "stream",
}


@dataclass
class _BodyEvent:
    """One raw API body log event, read from disk on demand."""

    name: str
    time_ns: int
    attributes: dict[str, Any]
    parsed: Any = None
    loaded: bool = False


@dataclass
class _NativeRequestSpan:
    """One ``claude_code.llm_request`` span.

    Builds that state an API request id on the span and on the body logs are
    paired by it. Recent builds state it on neither, leaving ``request_id``
    empty, and the span is then paired by the window it covers.
    """

    request_id: str
    start_ns: int
    end_ns: int
    attributes: dict[str, Any]

    def covers(self, time_ns: int) -> bool:
        """Report whether a log written at ``time_ns`` belongs to this span."""
        if self.start_ns <= 0 or self.end_ns < self.start_ns:
            return False
        return self.start_ns <= time_ns <= self.end_ns + _UNKEYED_MATCH_WINDOW_NS


@dataclass
class _ToolFacts:
    """What Claude Code states about one tool call, keyed by its call id."""

    tool_use_id: str
    started_at: float | None = None
    ended_at: float | None = None
    success: bool | None = None
    decision: str | None = None
    decision_source: str | None = None

    @property
    def settled(self) -> bool:
        """Report whether the CLI stated how the call ended."""
        return self.ended_at is not None or self.success is not None


@dataclass
class _ReplySnapshot:
    """What the SDK stream showed of one top-level assistant reply."""

    message_id: str
    started_at: float
    ended_at: float
    deadline: float
    model: str | None = None
    usage: TurnUsage | None = None
    blocks: list[ContentBlock] = field(default_factory=list)
    error: TurnError | None = None
    emitted: bool = False


@dataclass
class _HeldItem:
    """A tool item waiting for the request event of the reply that caused it."""

    payload: ItemLifecycleEvent
    item_id: str | None
    owner: str | None
    observed_at: float


class ClaudeRequestObserver:
    """Report each model request of a Claude Code session as a protocol event.

    Args:
        sdk: The loaded ``claude_agent_sdk`` module.
        wait_s: How long a reply waits for its request logs before it is
            reported from the SDK message alone.
    """

    def __init__(self, *, sdk: Any, wait_s: float = _DEFAULT_WAIT_S) -> None:
        self._sdk = sdk
        self._wait_s = wait_s
        self._source_id = uuid.uuid4().hex
        self._subscriber_id: int | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._body_dir: Path | None = None
        self._incoming: list[_BodyEvent] = []
        self._incoming_observations: list[Any] = []
        self._spans: dict[str, _NativeRequestSpan] = {}
        self._api_requests: dict[str, dict[str, Any]] = {}
        # What a build stating no request id leaves to be paired by time,
        # oldest first: the CLI runs its calls one at a time, so a response
        # body belongs to the one span whose window covers it.
        self._timed_spans: list[_NativeRequestSpan] = []
        self._timed_api_requests: list[tuple[int, dict[str, Any]]] = []
        # Whether this session has seen an unkeyed request span. A reply whose
        # body names no request id waits for its span only once one has
        # arrived this way, so a build exporting none never spends the wait.
        self._timed_spans_seen = False
        self._tool_facts: dict[str, _ToolFacts] = {}
        self._changed = asyncio.Event()
        self._lock = asyncio.Lock()
        self._requests: list[_BodyEvent] = []
        self._responses: dict[str, tuple[_BodyEvent, dict[str, Any]]] = {}
        # The conversation each reply leaves behind, by its message id. A
        # threaded request states only what is new, so the call that continues
        # from a reply is the reply's conversation plus its own delta.
        self._threads: dict[str, tuple[TurnMessage, ...]] = {}
        self._thread_tools: Any = None
        # The system prompt the thread was opened with. Claude Code states it
        # on the call that opens a thread and on any call that changes it, and
        # leaves it out of every continuation, so the last one stated is the
        # one in force.
        self._thread_system: tuple[ContentBlock, ...] = ()
        self._last_output_identity = ""
        self._emit: EmitFn | None = None
        self._drain_task: asyncio.Task[None] | None = None
        self._reset_turn()

    @property
    def logs_attached(self) -> bool:
        """Return whether request logs are being received for this session."""
        return self._subscriber_id is not None

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    async def attach(self, *, resource_attributes: str = "") -> dict[str, str]:
        """Start receiving this session's request logs.

        Args:
            resource_attributes: ``OTEL_RESOURCE_ATTRIBUTES`` the CLI process
                would otherwise carry.

        Returns:
            Env the CLI subprocess needs to export its request logs; empty
            when the loopback receiver is unavailable, in which case requests
            are reported from the SDK stream alone.
        """
        receiver = get_shared_otlp_receiver()
        subscriber_id = await receiver.subscribe(self._on_receiver_event)
        endpoint = receiver.grpc_endpoint
        if subscriber_id is None or not endpoint:
            if subscriber_id is not None:
                receiver.unsubscribe(subscriber_id)
            logger.info("[claude-code] model request logs unavailable; reporting requests from the SDK stream")
            return {}
        self._subscriber_id = subscriber_id
        self._loop = asyncio.get_running_loop()
        self._body_dir = Path(tempfile.mkdtemp(prefix="openjiuwen-claude-bodies-"))
        return claude_request_log_env(
            endpoint=endpoint,
            body_dir=str(self._body_dir),
            source_id=self._source_id,
            resource_attributes=resource_attributes,
        )

    async def close(self) -> None:
        """Stop receiving request logs and remove the body directory."""
        await self._stop_drain()
        subscriber_id = self._subscriber_id
        self._subscriber_id = None
        if subscriber_id is not None:
            get_shared_otlp_receiver().unsubscribe(subscriber_id)
        body_dir = self._body_dir
        self._body_dir = None
        if body_dir is not None:
            await asyncio.to_thread(shutil.rmtree, body_dir, True)

    # ------------------------------------------------------------------
    # Turn lifecycle
    # ------------------------------------------------------------------

    def begin_turn(self, emit: EmitFn) -> None:
        """Start observing a turn whose events go out through ``emit``."""
        self._reset_turn()
        self._emit = emit
        if self.logs_attached:
            self._drain_task = asyncio.create_task(self._drain(), name="claude_request_observer_drain")

    async def observe(self, message: Any, mapped: list[MappedClaudeEvent], accumulator: ClaudeTurnAccumulator) -> None:
        """Emit the events one SDK message mapped to, holding tool items as needed.

        Args:
            message: The SDK message just consumed.
            mapped: The events the accumulator mapped it to, in order.
            accumulator: The turn accumulator, whose last message is the
                normalized form of an assistant ``message``.
        """
        now = time.time()
        self._note_message(message, accumulator, now)
        async with self._lock:
            for event in mapped:
                payload = event.payload
                if isinstance(payload, ItemLifecycleEvent):
                    self._held.append(
                        _HeldItem(payload=payload, item_id=event.item_id, owner=self._item_owner(event), observed_at=now)
                    )
                else:
                    await self._emit_event(payload, event.item_id, (), None)
            await self._flush(force=False)

    async def end_turn(self, *, wait: bool) -> None:
        """Report everything the turn still owes before its terminal event.

        Args:
            wait: Give outstanding replies until their deadline for request
                logs; ``False`` (an aborted or failed turn) reports at once.
        """
        await self._stop_drain()
        if wait and self.logs_attached:
            while True:
                async with self._lock:
                    await self._flush(force=False)
                    outstanding = [snapshot for snapshot in self._replies.values() if not snapshot.emitted]
                if not outstanding or time.time() >= max(snapshot.deadline for snapshot in outstanding):
                    break
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._changed.wait(), timeout=_DRAIN_INTERVAL_S)
                self._changed.clear()
        async with self._lock:
            await self._flush(force=True)
            await asyncio.to_thread(self._discard_bodies, self._requests)
            self._requests = []
            self._responses.clear()
        self._emit = None

    # ------------------------------------------------------------------
    # SDK stream
    # ------------------------------------------------------------------

    def _reset_turn(self) -> None:
        self._replies: dict[str, _ReplySnapshot] = {}
        self._order: list[str] = []
        self._emitted_count = 0
        self._held: deque[_HeldItem] = deque()
        self._call_owners: dict[str, str | None] = {}
        self._current_owner: str | None = None
        self._next_started_at: float | None = None

    def _note_message(self, message: Any, accumulator: ClaudeTurnAccumulator, now: float) -> None:
        if getattr(message, "parent_tool_use_id", None):
            return
        event = getattr(message, "event", None)
        if isinstance(event, Mapping):
            if event.get("type") == "message_start" and self._next_started_at is None:
                self._next_started_at = now
            return
        if not isinstance(message, self._sdk.AssistantMessage) or not accumulator.messages:
            return
        normalized = accumulator.messages[-1]
        message_id = normalized.message_id
        snapshot = self._replies.get(message_id)
        if snapshot is None:
            started_at = self._next_started_at if self._next_started_at is not None else now
            snapshot = _ReplySnapshot(message_id=message_id, started_at=started_at, ended_at=now, deadline=now)
            self._replies[message_id] = snapshot
            self._order.append(message_id)
            self._next_started_at = None
        snapshot.blocks.extend(normalized.content)
        snapshot.ended_at = now
        snapshot.model = str(getattr(message, "model", "") or "") or snapshot.model
        snapshot.usage = claude_turn_usage(getattr(message, "usage", None)) or snapshot.usage
        if getattr(message, "error", None):
            snapshot.error = accumulator.pending_error or TurnError(message=str(message.error))
        waits = self.logs_attached and snapshot.error is None
        snapshot.deadline = now + self._wait_s if waits else now
        self._current_owner = message_id

    def _item_owner(self, event: MappedClaudeEvent) -> str | None:
        payload = event.payload
        item_id = event.item_id or ""
        if payload.kind is ItemEventKind.STARTED:
            data = json_value_to_builtin(payload.data)
            nested = isinstance(data, dict) and bool(data.get("parent_tool_use_id"))
            owner = None if nested else self._current_owner
            self._call_owners[item_id] = owner
            return owner
        return self._call_owners.get(item_id)

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
                logger.debug("[claude-code] model request observation flush failed", exc_info=True)

    async def _stop_drain(self) -> None:
        task = self._drain_task
        self._drain_task = None
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _flush(self, *, force: bool) -> None:
        """Emit ready request events in reply order, releasing the items they caused."""
        await self._absorb_bodies()
        await self._release_items()
        while self._emitted_count < len(self._order):
            snapshot = self._replies[self._order[self._emitted_count]]
            ready = self._reply_ready(snapshot)
            if not ready and not force and time.time() < snapshot.deadline:
                return
            event = await self._native_request(snapshot) if ready else self._stream_request(snapshot)
            await self._emit_event(event, None, (), None)
            snapshot.emitted = True
            self._emitted_count += 1
            await self._release_items()
        if force:
            await self._release_items(force=True)

    def _merge_tool_facts(self, facts: _ToolFacts) -> None:
        known = self._tool_facts.setdefault(facts.tool_use_id, _ToolFacts(tool_use_id=facts.tool_use_id))
        for name in ("started_at", "ended_at", "success", "decision", "decision_source"):
            value = getattr(facts, name)
            if value is not None:
                setattr(known, name, value)

    def _reply_ready(self, snapshot: _ReplySnapshot) -> bool:
        """Report whether this reply's records have all arrived.

        The response body names the reply; its ``claude_code.llm_request``
        span states the request window and its timing. They export on the same
        interval, so waiting for both costs nothing in the normal case.

        A build stating the API request id on both is paired by it. One that
        states it on neither is paired by time instead, and waits only once a
        span has shown that this build exports them at all.
        """
        entry = self._responses.get(snapshot.message_id)
        if entry is None:
            return False
        response_event = entry[0]
        request_id = str(response_event.attributes.get("request_id") or "")
        if request_id:
            return request_id in self._spans
        if not self._timed_spans_seen:
            return True
        return self._covering_span_index(response_event.time_ns) is not None

    async def _release_items(self, *, force: bool = False) -> None:
        while self._held:
            head = self._held[0]
            owner = self._replies.get(head.owner) if head.owner else None
            if owner is not None and not owner.emitted and not force:
                return
            facts = self._tool_facts.get(head.item_id or "")
            completing = head.payload.kind is ItemEventKind.COMPLETED
            if completing and not force and (facts is None or not facts.settled):
                # The CLI states the execution window and the outcome on its
                # own span; wait for them rather than timing the SDK stream.
                if time.time() < head.observed_at + self._wait_s:
                    return
            self._held.popleft()
            causes = (head.owner,) if owner is not None and owner.emitted else ()
            payload, timestamp = self._tool_observation(head, facts)
            await self._emit_event(payload, head.item_id, causes, timestamp)

    def _tool_observation(self, held: _HeldItem, facts: _ToolFacts | None) -> tuple[ItemLifecycleEvent, float]:
        """State a tool item the way Claude Code accounted for it."""
        payload = held.payload
        if facts is None:
            return payload, held.observed_at
        data = dict(json_value_to_builtin(payload.data) or {})
        if payload.kind is ItemEventKind.STARTED:
            return payload, facts.started_at or held.observed_at
        if facts.success is not None:
            data["is_error"] = not facts.success
        if facts.decision:
            data["decision"] = facts.decision
        if facts.decision_source:
            data["decision_source"] = facts.decision_source
        return replace(payload, data=data), facts.ended_at or held.observed_at

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
    # Request logs
    # ------------------------------------------------------------------

    def _on_receiver_event(self, event: dict[str, Any]) -> None:
        """Accept one receiver event; runs on the receiver's thread or loop."""
        resource = event.get("resource_attributes")
        if not isinstance(resource, dict) or resource.get(OTEL_RESOURCE_SOURCE_ID) != self._source_id:
            return
        attributes = event.get("attributes")
        attributes = dict(attributes) if isinstance(attributes, dict) else {}
        signal = event.get("signal")
        name = str(event.get("name") or "")
        tool_use_id = str(attributes.get("tool_use_id") or "")
        accepted: Any
        if signal == "log" and name in _BODY_EVENTS:
            accepted = _BodyEvent(
                name=name,
                time_ns=int(event.get("time_ns") or time.time_ns()),
                attributes=attributes,
            )
        elif signal == "trace" and name == _LLM_REQUEST_SPAN:
            accepted = _NativeRequestSpan(
                request_id=str(attributes.get("request_id") or ""),
                start_ns=int(event.get("start_time_ns") or 0),
                end_ns=int(event.get("end_time_ns") or 0),
                attributes=attributes,
            )
        elif signal == "log" and name == _API_REQUEST_EVENT:
            accepted = (
                "api_request",
                str(attributes.get("request_id") or ""),
                int(event.get("time_ns") or 0),
                attributes,
            )
        elif signal == "trace" and name == _TOOL_SPAN and tool_use_id:
            accepted = _ToolFacts(
                tool_use_id=tool_use_id,
                started_at=_optional_seconds(event.get("start_time_ns")),
                ended_at=_optional_seconds(event.get("end_time_ns")),
            )
        elif signal == "log" and name == _TOOL_RESULT_EVENT and tool_use_id:
            accepted = _ToolFacts(tool_use_id=tool_use_id, success=_flag(attributes.get("success")))
        elif signal == "log" and name == _TOOL_DECISION_EVENT and tool_use_id:
            accepted = _ToolFacts(
                tool_use_id=tool_use_id,
                decision=str(attributes.get("decision") or "") or None,
                decision_source=str(attributes.get("source") or "") or None,
            )
        else:
            return
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(self._accept_observation, accepted)

    def _accept_observation(self, observation: Any) -> None:
        if isinstance(observation, _BodyEvent):
            self._incoming.append(observation)
        else:
            self._incoming_observations.append(observation)
        self._changed.set()

    async def _absorb_bodies(self) -> None:
        observations, self._incoming_observations = self._incoming_observations, []
        for observation in observations:
            if isinstance(observation, _NativeRequestSpan):
                if observation.request_id:
                    self._spans[observation.request_id] = observation
                else:
                    self._timed_spans_seen = True
                    self._timed_spans.append(observation)
                    self._timed_spans.sort(key=lambda span: span.start_ns)
                    del self._timed_spans[:-_UNKEYED_HISTORY_LIMIT]
            elif isinstance(observation, _ToolFacts):
                self._merge_tool_facts(observation)
            else:
                _kind, request_id, time_ns, attributes = observation
                if request_id:
                    self._api_requests[request_id] = attributes
                else:
                    self._timed_api_requests.append((time_ns, attributes))
                    del self._timed_api_requests[:-_UNKEYED_HISTORY_LIMIT]
        incoming, self._incoming = self._incoming, []
        for body_event in incoming:
            if body_event.name == _REQUEST_BODY_EVENT:
                self._requests.append(body_event)
                continue
            response = await self._load_body(body_event, keep_file=False)
            message_id = response.get("id") if isinstance(response, dict) else None
            if isinstance(message_id, str) and message_id:
                self._responses[message_id] = (body_event, response)

    async def _load_body(self, body_event: _BodyEvent, *, keep_file: bool) -> Any:
        if body_event.loaded:
            return body_event.parsed
        body_event.loaded = True
        inline = body_event.attributes.get("body")
        text: str | None = inline if isinstance(inline, str) and inline else None
        path = self._body_path(body_event)
        if text is None and path is not None:
            text = await asyncio.to_thread(_read_text, path)
        if path is not None and not keep_file:
            await asyncio.to_thread(_unlink, path)
        if text is None:
            return None
        try:
            body_event.parsed = json.loads(text)
        except ValueError:
            logger.debug("[claude-code] unparseable {} body", body_event.name)
        return body_event.parsed

    def _body_path(self, body_event: _BodyEvent) -> Path | None:
        body_dir = self._body_dir
        reference = body_event.attributes.get("body_ref")
        if body_dir is None or not isinstance(reference, str) or not reference:
            return None
        candidate = Path(reference)
        path = candidate if candidate.is_absolute() else body_dir / candidate
        try:
            path.resolve().relative_to(body_dir.resolve())
        except ValueError:
            # A body reference outside the directory this session owns is
            # never read or deleted.
            return None
        return path

    def _discard_bodies(self, body_events: list[_BodyEvent]) -> None:
        for body_event in body_events:
            path = self._body_path(body_event)
            if path is not None:
                _unlink(path)

    async def _pick_request(self, response_event: _BodyEvent) -> _BodyEvent | None:
        """Return the request log of the call this response answers.

        A build that states ``request_body_id`` on both body events has said
        which pair belongs together, and that is taken. Without it the pair is
        inferred: requests from sub-agents and side queries interleave with
        the main conversation, and a main-conversation request states the
        previous main-conversation reply in its history, which rules the
        others out.
        """
        body_id = str(response_event.attributes.get(_REQUEST_BODY_ID) or "")
        if body_id:
            return await self._stated_request(body_id)
        return await self._inferred_request(response_event)

    async def _stated_request(self, body_id: str) -> _BodyEvent | None:
        """Return the request whose body id the response names."""
        for index, candidate in enumerate(self._requests):
            if str(candidate.attributes.get(_REQUEST_BODY_ID) or "") != body_id:
                continue
            chosen = self._requests.pop(index)
            await self._load_body(chosen, keep_file=True)
            await asyncio.to_thread(self._discard_bodies, [chosen])
            return chosen if isinstance(chosen.parsed, dict) else None
        return None

    async def _inferred_request(self, response_event: _BodyEvent) -> _BodyEvent | None:
        """Return the newest request that carries the previous reply."""
        candidates = sorted(
            (item for item in self._requests if item.time_ns <= response_event.time_ns),
            key=lambda item: item.time_ns,
            reverse=True,
        )
        chosen: _BodyEvent | None = None
        for candidate in candidates:
            request = await self._load_body(candidate, keep_file=True)
            if not isinstance(request, dict) or not isinstance(request.get("messages"), list):
                continue
            if self._last_output_identity and self._last_output_identity not in _assistant_identities(request):
                continue
            chosen = candidate
            break
        if chosen is None:
            return None
        consumed = [item for item in self._requests if item.time_ns <= chosen.time_ns]
        self._requests = [item for item in self._requests if item.time_ns > chosen.time_ns]
        await asyncio.to_thread(self._discard_bodies, consumed)
        return chosen

    def _thread_prefix(self, request: dict[str, Any]) -> tuple[tuple[TurnMessage, ...], bool]:
        """Return the conversation this request continues, and whether it is known.

        Claude Code threads a conversation server-side: only the first call
        carries it, and every later one states just what is new beside a
        ``previous_message_id``. Without the prefix the request would read as
        a conversation that had lost everything before it.
        """
        thread = request.get("thread")
        if not isinstance(thread, dict) or thread.get("type") != "continue":
            return (), True
        previous = str(thread.get("previous_message_id") or "")
        known = self._threads.get(previous)
        if known is None:
            return (), False
        return known, True

    def _remember_thread(self, message_id: str, conversation: tuple[TurnMessage, ...]) -> None:
        """Keep the conversation a reply leaves behind for the call that continues it."""
        if not message_id:
            return
        self._threads[message_id] = conversation
        while len(self._threads) > _THREAD_HISTORY_LIMIT:
            self._threads.pop(next(iter(self._threads)))

    def _covering_span_index(self, time_ns: int) -> int | None:
        """Return the index of the unkeyed span covering a log written then."""
        for index, span in enumerate(self._timed_spans):
            if span.covers(time_ns):
                return index
        return None

    def _take_native_span(self, response_event: _BodyEvent) -> _NativeRequestSpan | None:
        """Return the request span of the call this response answers.

        A build stating the API request id on the span and on the response
        body has said which two belong together. Recent builds state it on
        neither, so the span is found by its window: the CLI closes it just
        after it logs the body, and its calls run one at a time, so exactly
        one window covers that log.
        """
        request_id = str(response_event.attributes.get("request_id") or "")
        if request_id:
            return self._spans.pop(request_id, None)
        index = self._covering_span_index(response_event.time_ns)
        if index is None:
            return None
        span = self._timed_spans[index]
        # Spans before it answered calls that logged no body — an attempt the
        # CLI retried — and no later response can belong to them.
        del self._timed_spans[: index + 1]
        return span

    def _take_accounting(self, response_event: _BodyEvent) -> dict[str, Any]:
        """Return the CLI's own accounting of the call this response answers.

        The ``claude_code.api_request`` log is written as the call finishes,
        in the same millisecond as the response body, so an unkeyed one is
        paired with the body it sits closest to.
        """
        request_id = str(response_event.attributes.get("request_id") or "")
        if request_id:
            return self._api_requests.pop(request_id, {})
        chosen: int | None = None
        closest = _UNKEYED_MATCH_WINDOW_NS
        for index, (time_ns, _attributes) in enumerate(self._timed_api_requests):
            distance = abs(time_ns - response_event.time_ns)
            if distance <= closest:
                closest = distance
                chosen = index
        if chosen is None:
            return {}
        return self._timed_api_requests.pop(chosen)[1]

    async def _native_request(self, snapshot: _ReplySnapshot) -> ModelRequestEvent:
        response_event, response = self._responses.pop(snapshot.message_id)
        request_event = await self._pick_request(response_event)
        request = request_event.parsed if request_event is not None else None
        input_messages: tuple[TurnMessage, ...] = ()
        system_instructions: tuple[ContentBlock, ...] = ()
        tool_definitions: Any = None
        request_parameters: dict[str, Any] = {}
        billing_header = ""
        input_observed = False
        if isinstance(request, dict):
            prefix, prefix_known = self._thread_prefix(request)
            input_messages = prefix + tuple(self._conversation(request.get("messages")))
            input_observed = prefix_known
            if "system" in request:
                self._thread_system, billing_header = _system_instructions(request.get("system"))
            system_instructions = self._thread_system
            tools = request.get("tools")
            if isinstance(tools, list):
                # Only the call that opens a thread carries the catalogue; the
                # rest of the thread is offered the same tools.
                self._thread_tools = _tool_definitions(tools)
            tool_definitions = self._thread_tools
            request_parameters = _request_parameters(request)
        # Identified by its content, like every other conversation message: a
        # thread the CLI reopens after a restart replays this reply inside a
        # request body, where that is the only id there is.
        output = _message(
            self._message_id("assistant", response.get("content")),
            MessageRole.ASSISTANT,
            response.get("content"),
        )
        if input_observed:
            self._last_output_identity = _identity("assistant", response.get("content"))
            self._remember_thread(snapshot.message_id, input_messages + (output,))
        native = self._take_native_span(response_event)
        if native is not None and native.start_ns > 0 and native.end_ns >= native.start_ns:
            started_at = native.start_ns / 1e9
            ended_at = native.end_ns / 1e9
        else:
            started_at = request_event.time_ns / 1e9 if request_event is not None else snapshot.started_at
            ended_at = max(response_event.time_ns / 1e9, started_at)
        accounting = self._take_accounting(response_event)
        effort = accounting.get("effort")
        if isinstance(effort, str) and effort:
            request_parameters["reasoning_level"] = effort
        stop_reason = response.get("stop_reason")
        return ModelRequestEvent(
            request_id=snapshot.message_id,
            status=ModelRequestStatus.COMPLETED,
            started_at=started_at,
            ended_at=ended_at,
            model=str(response.get("model") or snapshot.model or "") or None,
            provider_name=_MODEL_PROVIDER,
            system_instructions=system_instructions,
            input_messages=input_messages,
            input_observed=input_observed,
            output_message=output,
            tool_definitions=tool_definitions,
            request_parameters=request_parameters,
            response_id=str(response.get("id") or "") or None,
            time_to_first_chunk=_seconds(native.attributes.get("ttft_ms")) if native is not None else None,
            finish_reasons=(stop_reason,) if isinstance(stop_reason, str) and stop_reason else (),
            usage=claude_turn_usage(response.get("usage")) or snapshot.usage,
            cost=_cost(accounting.get("cost_usd_micros")),
            data={
                _DATA_NAMESPACE: {
                    "observation": "api_bodies",
                    "billing_header": billing_header or None,
                    **_native_diagnostics(native),
                    "query_source": accounting.get("query_source"),
                },
            },
        )

    def _stream_request(self, snapshot: _ReplySnapshot) -> ModelRequestEvent:
        failed = snapshot.error is not None
        return ModelRequestEvent(
            request_id=snapshot.message_id,
            status=ModelRequestStatus.FAILED if failed else ModelRequestStatus.COMPLETED,
            started_at=snapshot.started_at,
            ended_at=max(snapshot.ended_at, snapshot.started_at),
            model=snapshot.model or None,
            provider_name=_MODEL_PROVIDER,
            output_message=TurnMessage(
                message_id=snapshot.message_id,
                role=MessageRole.ASSISTANT,
                content=tuple(snapshot.blocks),
            ),
            usage=snapshot.usage,
            error=snapshot.error,
            data={_DATA_NAMESPACE: {"observation": "sdk_stream"}},
        )

    def _conversation(self, messages: Any) -> list[TurnMessage]:
        """Convert the request's conversation into protocol messages.

        A user turn of Claude Code carries independent blocks: the CLI's own
        reminders and notices beside what the host actually said. They are
        separate statements to a reader, so each becomes its own message; an
        assistant turn stays whole, because its reasoning, answer and tool
        calls are one reply.
        """
        result: list[TurnMessage] = []
        if not isinstance(messages, list):
            return result
        for message in messages:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role") or "user")
            content = message.get("content")
            if role == "assistant":
                result.append(_message(self._message_id(role, content), MessageRole.ASSISTANT, content))
                continue
            for block in _content_list(content):
                if not isinstance(block, dict) or block.get("type") in _CONTROL_BLOCK_TYPES:
                    continue
                result.append(_message(self._message_id(role, [block]), MessageRole.USER, [block]))
        return result

    @staticmethod
    def _message_id(role: str, content: Any) -> str:
        """Return the stable id of one conversation message.

        The id is derived from the message alone, never from what this
        observer happens to have seen. A member that restarts mid-conversation
        gets a fresh observer while the conversation carries on: an id that
        depended on a remembered reply would change for every message already
        in the window, and a reader would see the whole history restated.
        """
        return f"claude-context:{_identity(role, content)}"


def _content_list(content: Any) -> list[Any]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        return content
    return []


def _message(message_id: str, role: MessageRole, content: Any) -> TurnMessage:
    blocks = [
        _content_block(f"{message_id}:{index}", block)
        for index, block in enumerate(_content_list(content))
        if isinstance(block, dict)
    ]
    return TurnMessage(message_id=message_id, role=role, content=tuple(block for block in blocks if block is not None))


def _content_block(block_id: str, block: dict[str, Any]) -> ContentBlock | None:
    block_type = str(block.get("type") or "unknown")
    if block_type == "text":
        return ContentBlock(block_id=block_id, kind="text", content=str(block.get("text") or ""))
    if block_type == "thinking":
        thinking = str(block.get("thinking") or "")
        if not thinking or thinking == _REDACTED_CONTENT:
            return _withheld_reasoning(block_id)
        return ContentBlock(block_id=block_id, kind="reasoning", content=thinking)
    if block_type == "redacted_thinking":
        return _withheld_reasoning(block_id)
    if block_type in ("tool_use", "server_tool_use", "mcp_tool_use"):
        return ContentBlock(
            block_id=block_id,
            kind="tool_call",
            content={"name": str(block.get("name") or ""), "arguments": to_json_safe(block.get("input"))},
            data={"call_id": str(block.get("id") or "")},
        )
    if block_type.endswith("tool_result"):
        return ContentBlock(
            block_id=block_id,
            kind="tool_result",
            content=_tool_result_content(block.get("content")),
            data={"call_id": str(block.get("tool_use_id") or ""), "is_error": bool(block.get("is_error"))},
        )
    return ContentBlock(block_id=block_id, kind=block_type, content=_sanitize(block))


def _tool_result_content(content: Any) -> Any:
    if isinstance(content, list) and all(isinstance(item, dict) and item.get("type") == "text" for item in content):
        return "\n".join(str(item.get("text") or "") for item in content)
    return _sanitize(content)


def _withheld_reasoning(block_id: str) -> ContentBlock:
    """State that the model reasoned here and the CLI withheld the text.

    Claude Code redacts thinking everywhere it can be read -- the raw body log
    writes ``<REDACTED>`` and keeps only the signature, and the SDK stream
    hands over an empty ``ThinkingBlock`` -- while the token count survives in
    usage. Dropping the block made a turn that reasoned look like one that did
    not; this states which it was without inventing the text.
    """
    return ContentBlock(
        block_id=block_id,
        kind="reasoning",
        content=_REDACTED_CONTENT,
        data={"redacted": True},
    )


def _system_instructions(system: Any) -> tuple[tuple[ContentBlock, ...], str]:
    """Split the system prompt from Claude Code's billing header block.

    Returns:
        The instruction blocks, and the billing header when the request
        carried one.
    """
    blocks: list[ContentBlock] = []
    billing_header = ""
    for index, block in enumerate(_content_list(system)):
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        text = str(block.get("text") or "")
        if text.startswith(_BILLING_HEADER_PREFIX):
            billing_header = text
            continue
        blocks.append(ContentBlock(block_id=f"claude-system:{index}", kind="text", content=text))
    return tuple(blocks), billing_header


def _tool_definitions(tools: list[Any]) -> list[dict[str, Any]]:
    """State the offered tools the way every reader of a tool schema expects."""
    definitions: list[dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict) or not tool.get("name"):
            continue
        definitions.append({
            "name": str(tool["name"]),
            "description": str(tool.get("description") or ""),
            "parameters": _sanitize(tool.get("input_schema") or {}),
        })
    return definitions


def _request_parameters(request: dict[str, Any]) -> dict[str, Any]:
    """Return the sampling parameters the request carried, under GenAI names."""
    parameters: dict[str, Any] = {}
    for source, name in _REQUEST_PARAMETERS.items():
        value = request.get(source)
        if value is not None:
            parameters[name] = to_json_safe(value)
    return parameters


def _sanitize(value: Any) -> Any:
    """Drop request-shaping keys and inline binary data from a body fragment."""
    if isinstance(value, dict):
        source = value.get("source")
        result = {key: _sanitize(item) for key, item in value.items() if key not in _OMITTED_KEYS}
        if isinstance(source, dict) and source.get("type") == "base64":
            data = source.get("data")
            size = len(data) if isinstance(data, str) else 0
            result["source"] = {**result["source"], "data": f"<{size} base64 characters omitted>"}
        return result
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    return to_json_safe(value)


def _identity(role: str, content: Any) -> str:
    """Return a content identity of a message that survives cache-marker moves.

    Claude Code moves ``cache_control`` markers between requests and may
    re-serialize blocks, so only the facts that identify a block count.
    """
    facts: list[Any] = [role]
    for block in _content_list(content):
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text":
            facts.append(["text", block.get("text")])
        elif block_type == "thinking":
            facts.append(["thinking", block.get("thinking")])
        elif block_type in ("tool_use", "server_tool_use", "mcp_tool_use"):
            facts.append(["tool_use", block.get("id")])
        elif isinstance(block_type, str) and block_type.endswith("tool_result"):
            facts.append(["tool_result", block.get("tool_use_id"), _tool_result_content(block.get("content"))])
        elif block_type != "redacted_thinking":
            facts.append(_sanitize(block))
    encoded = json.dumps(facts, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:32]


def _assistant_identities(request: dict[str, Any]) -> set[str]:
    """Return the content identities of the assistant turns a request states."""
    messages = request.get("messages")
    if not isinstance(messages, list):
        return set()
    return {
        _identity("assistant", message.get("content"))
        for message in messages
        if isinstance(message, dict) and message.get("role") == "assistant"
    }


def _native_diagnostics(native: _NativeRequestSpan | None) -> dict[str, Any]:
    """Return the request facts only the CLI's own span states."""
    if native is None:
        return {}
    facts: dict[str, Any] = {}
    for key in ("attempt", "speed", "success", "api_request_id", "client_request_id", "llm_request.context"):
        value = native.attributes.get(key)
        if isinstance(value, (str, bool, int, float)):
            facts[key.replace(".", "_")] = value
    # Builds that key their spans state the id here; those that do not leave
    # whatever the span itself named, rather than an empty id.
    if native.request_id:
        facts["api_request_id"] = native.request_id
    return facts


def _cost(micros: Any) -> MonetaryAmount | None:
    """Return what the CLI charged for one request, in exact micros."""
    if isinstance(micros, bool) or not isinstance(micros, (int, float)) or micros < 0:
        return None
    return MonetaryAmount(micros=int(micros))


def _flag(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in ("true", "false"):
        return value.lower() == "true"
    return None


def _optional_seconds(nanoseconds: Any) -> float | None:
    if isinstance(nanoseconds, bool) or not isinstance(nanoseconds, (int, float)) or nanoseconds <= 0:
        return None
    return float(nanoseconds) / 1e9


def _seconds(milliseconds: Any) -> float | None:
    if isinstance(milliseconds, bool) or not isinstance(milliseconds, (int, float)):
        return None
    return max(float(milliseconds) / 1000, 0.0)


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _unlink(path: Path) -> None:
    with contextlib.suppress(OSError):
        path.unlink()


__all__ = ["ClaudeRequestObserver", "EmitFn"]

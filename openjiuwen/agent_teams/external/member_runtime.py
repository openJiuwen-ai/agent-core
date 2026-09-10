# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Bridge the public external-harness SPI to AgentTeam ``MemberRuntime``."""

from __future__ import annotations

import asyncio
import inspect
import json
from typing import Any, AsyncIterator, Awaitable, Callable, Optional, Protocol, runtime_checkable

from openjiuwen.agent_teams.external.protocol import (
    AbortMode,
    DeliveryMode,
    ExternalHarnessContext,
    ExternalHarnessInput,
    ExternalHarnessProtocol,
    ExternalHarnessStateError,
    HarnessCapability,
    ItemEventKind,
    ItemLifecycleEvent,
    OutputChannel,
    OutputEvent,
    OutputOperation,
    StateChangedEvent,
    TurnEventKind,
    TurnLifecycleEvent,
    UnsupportedHarnessCapabilityError,
    json_value_to_builtin,
)
from openjiuwen.agent_teams.harness.outputs import _END, _OutputIterator
from openjiuwen.agent_teams.harness.state import HarnessState
from openjiuwen.agent_teams.team_context import TeamContextTracker
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.runner.callback.framework import AsyncCallbackFramework
from openjiuwen.core.session.stream.base import OutputSchema

_EVENT_STATE = "harness.state"
_EVENT_ROUND = "harness.round"
_EVENT_NAMESPACE = "external_harness_runtime"

ContextFactory = Callable[
    [Any | None],
    ExternalHarnessContext | Awaitable[ExternalHarnessContext],
]


@runtime_checkable
class TeamContextAwareRuntime(Protocol):
    """Behavior used by coordination handlers to push roster announcements."""

    @property
    def state(self) -> HarnessState:
        """Return the current harness state."""
        ...

    async def announce_team_context(self) -> None:
        """Push the pending team roster announcement to the member."""
        ...


class ExternalHarnessMemberRuntime:
    """Project ``ExternalHarnessProtocol`` onto the internal team runtime seam.

    ``immediate=True`` is capability-aware: it starts normally from IDLE,
    steers only when the provider declares STEER, and otherwise becomes an
    explicit follow-up.  This preserves input delivery for providers such as
    DSH without falsely advertising mid-turn steering.
    """

    def __init__(
        self,
        *,
        harness: ExternalHarnessProtocol,
        context: ExternalHarnessContext | ContextFactory,
        team_context_tracker: TeamContextTracker | None = None,
        stop_on_unsupported_force_abort: bool = False,
    ) -> None:
        self._harness = harness
        self._context_source = context
        self._team_context_tracker = team_context_tracker
        self._stop_on_unsupported_force_abort = stop_on_unsupported_force_abort
        if isinstance(context, ExternalHarnessContext):
            self._member_name = context.member_name
            self._member_agent_id = context.member_agent_id
        else:
            self._member_name = harness.card.name
            self._member_agent_id = None
        self._member_session: Any = None
        self._events = AsyncCallbackFramework(enable_metrics=False, enable_logging=False)
        self._output_queue: asyncio.Queue[Any] = asyncio.Queue()
        self._event_task: asyncio.Task[None] | None = None
        self._stopped = True
        self._output_index = 0
        self._output_text: dict[str, str] = {}
        self._context_delivery_lock = asyncio.Lock()
        self._lifecycle_lock = asyncio.Lock()

    @property
    def state(self) -> HarnessState:
        return self._harness.state

    @property
    def session_id(self) -> str | None:
        return self._harness.session_id

    async def start(self, *, team_session: Optional[Any] = None) -> None:
        """Start the provider cycle and its single continuous event pump."""

        async with self._lifecycle_lock:
            await self._start(team_session)

    async def _start(self, team_session: Any | None) -> None:
        if not self._stopped:
            raise ExternalHarnessStateError("external harness member runtime is already started")
        async with self._context_delivery_lock:
            await self._finalize_member_session()
        context = await self._resolve_context(team_session)
        self._member_name = context.member_name
        self._member_agent_id = context.member_agent_id
        await self._ensure_member_session(team_session)
        self._output_queue = asyncio.Queue()
        self._output_index = 0
        self._output_text.clear()
        try:
            await self._harness.start(context)
            cursor = self._harness.events()
        except Exception:
            try:
                await self._harness.stop()
            except Exception:
                team_logger.exception("external harness cleanup failed after start")
            try:
                async with self._context_delivery_lock:
                    await self._finalize_member_session()
            except Exception:
                team_logger.exception("external harness member session cleanup failed after start")
            raise
        self._stopped = False
        self._event_task = asyncio.create_task(
            self._pump_events(cursor),
            name=f"external_harness_events[{context.member_name}]",
        )

    async def stop(self) -> None:
        """Stop the provider and close the projected MemberRuntime output."""

        if asyncio.current_task() is self._event_task:
            raise ExternalHarnessStateError(
                "event callbacks must schedule external member runtime stop from a separate task"
            )
        async with self._lifecycle_lock:
            await self._stop()

    async def _stop(self) -> None:
        if self._stopped:
            async with self._context_delivery_lock:
                await self._finalize_member_session()
            return
        await self._harness.stop()
        task = self._event_task
        self._event_task = None
        if task is not None and task is not asyncio.current_task():
            try:
                await task
            except Exception:
                team_logger.exception("external harness event pump failed during stop")
        self._output_queue.put_nowait(_END)
        await self._events.unregister_namespace(_EVENT_NAMESPACE)
        self._stopped = True
        async with self._context_delivery_lock:
            await self._finalize_member_session()

    async def dispose(self) -> None:
        await self.stop()

    def outputs(self) -> AsyncIterator[Any]:
        return _OutputIterator(self._output_queue)

    async def send(self, content: Any, *, immediate: bool = False) -> Any:
        """Send input with capability-aware steer/follow-up selection."""

        async with self._context_delivery_lock:
            external_input = _external_input(content)
            pending_context = await self._pending_team_context()
            if pending_context:
                external_input = _prepend_context(external_input, pending_context)

            mode = self._delivery_mode(immediate=immediate)
            try:
                receipt = await self._harness.send(external_input, mode=mode)
            except ExternalHarnessStateError:
                # A terminal event may win the race after a RUNNING snapshot but
                # before provider STEER acceptance.  Retry only when the provider
                # confirms it is now IDLE; a rejected command was not accepted.
                if mode is not DeliveryMode.STEER or self._harness.state is not HarnessState.IDLE:
                    raise
                receipt = await self._harness.send(external_input, mode=DeliveryMode.AUTO)
            if pending_context:
                await self._commit_team_context()
            return receipt

    async def announce_team_context(self) -> None:
        async with self._context_delivery_lock:
            pending = await self._pending_team_context()
            if not pending:
                return
            mode = self._delivery_mode(immediate=False)
            await self._harness.send(ExternalHarnessInput(content=pending), mode=mode)
            await self._commit_team_context()

    async def abort(self, *, immediate: bool = False) -> None:
        capability = HarnessCapability.FORCE_ABORT if immediate else HarnessCapability.GRACEFUL_ABORT
        if self._harness.card.supports(capability):
            mode = AbortMode.FORCE if immediate else AbortMode.GRACEFUL
            await self._harness.abort(mode=mode)
            return
        if self._harness.state is not HarnessState.RUNNING:
            return
        if immediate and self._stop_on_unsupported_force_abort:
            await self.stop()
            return
        raise UnsupportedHarnessCapabilityError(
            f"external harness {self._harness.card.name!r} does not support {capability.value}"
        )

    async def pause(self) -> None:
        if self._harness.state is not HarnessState.RUNNING:
            return
        if not self._harness.card.supports(HarnessCapability.PAUSE_RESUME):
            raise UnsupportedHarnessCapabilityError(
                f"external harness {self._harness.card.name!r} does not support pause/resume"
            )
        await self._harness.pause()

    async def resume(self, *, query: Any | None = None) -> None:
        if self._harness.state is not HarnessState.PAUSED and query is None:
            return
        if not self._harness.card.supports(HarnessCapability.PAUSE_RESUME):
            raise UnsupportedHarnessCapabilityError(
                f"external harness {self._harness.card.name!r} does not support pause/resume"
            )
        external_query = None if query is None else _external_input(query)
        await self._harness.resume(query=external_query)

    async def subscribe(
        self,
        *,
        on_state: Callable[..., Any] | None = None,
        on_round: Callable[..., Any] | None = None,
    ) -> None:
        if on_state is not None:
            await self._events.register(_EVENT_STATE, on_state, namespace=_EVENT_NAMESPACE)
        if on_round is not None:
            await self._events.register(_EVENT_ROUND, on_round, namespace=_EVENT_NAMESPACE)

    def bind_team_context_tracker(self, tracker: TeamContextTracker | None) -> None:
        self._team_context_tracker = tracker

    async def _pump_events(self, cursor: Any) -> None:
        try:
            async for envelope in cursor:
                payload = envelope.event
                if isinstance(payload, StateChangedEvent):
                    await self._events.trigger(
                        _EVENT_STATE,
                        old=payload.old,
                        new=payload.new,
                        session_id=envelope.session_id,
                    )
                elif isinstance(payload, TurnLifecycleEvent):
                    kind = _member_round_kind(payload.kind)
                    await self._events.trigger(
                        _EVENT_ROUND,
                        kind=kind,
                        round_id=envelope.turn_id,
                        result=payload.result,
                    )
                elif isinstance(payload, OutputEvent):
                    chunk = self._project_output(payload)
                    if chunk is not None:
                        await self._output_queue.put(chunk)
                elif isinstance(payload, ItemLifecycleEvent):
                    chunk = self._project_item(envelope.item_id, payload)
                    if chunk is not None:
                        await self._output_queue.put(chunk)
        finally:
            await cursor.aclose()

    def _project_output(self, output: OutputEvent) -> OutputSchema | None:
        value = json_value_to_builtin(output.content)
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        previous = self._output_text.get(output.output_id, "")
        if output.operation is OutputOperation.DELTA:
            emitted = text
            self._output_text[output.output_id] = previous + text
        elif not previous:
            emitted = text
            self._output_text[output.output_id] = text
        elif text.startswith(previous):
            overlap = len(previous)
            emitted = text[overlap:]
            self._output_text[output.output_id] = text
        else:
            # Internal OutputSchema has append-only semantics.  The public
            # protocol event remains authoritative; avoid duplicating a FINAL
            # snapshot after its deltas on the legacy stream projection.
            return None
        if not emitted:
            return None
        chunk_type = "llm_reasoning" if output.channel is OutputChannel.REASONING else "llm_output"
        chunk = OutputSchema(
            type=chunk_type,
            index=self._next_output_index(),
            payload={
                "content": emitted,
                "result_type": "answer",
                "output_id": output.output_id,
                "operation": output.operation.value,
            },
        )
        return chunk

    def _project_item(self, item_id: str | None, item: ItemLifecycleEvent) -> OutputSchema | None:
        if item.item_type != "tool":
            return None
        builtin_data = json_value_to_builtin(item.data)
        if not isinstance(builtin_data, dict):
            return None
        data = builtin_data
        if item.kind is ItemEventKind.STARTED:
            arguments = data.get("arguments")
            if not isinstance(arguments, str):
                arguments = json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
            return OutputSchema(
                type="tool_call",
                index=self._next_output_index(),
                payload={
                    "name": data.get("name") or data.get("tool_name") or "unknown",
                    "arguments": arguments,
                    "tool_call_id": item_id or "",
                },
            )
        if item.kind is ItemEventKind.COMPLETED:
            return OutputSchema(
                type="tool_result",
                index=self._next_output_index(),
                payload={
                    "tool_name": data.get("tool_name") or data.get("name") or "unknown",
                    "result": data.get("result"),
                    "tool_call_id": item_id or "",
                },
            )
        return None

    def _next_output_index(self) -> int:
        index = self._output_index
        self._output_index += 1
        return index

    def _delivery_mode(self, *, immediate: bool) -> DeliveryMode:
        if self._harness.state is not HarnessState.RUNNING:
            return DeliveryMode.AUTO
        if immediate and self._harness.card.supports(HarnessCapability.STEER):
            return DeliveryMode.STEER
        return DeliveryMode.FOLLOW_UP

    async def _resolve_context(self, team_session: Any | None) -> ExternalHarnessContext:
        source = self._context_source
        if isinstance(source, ExternalHarnessContext):
            return source
        context = source(team_session)
        if inspect.isawaitable(context):
            context = await context
        if not isinstance(context, ExternalHarnessContext):
            raise TypeError("external harness context factory must return ExternalHarnessContext")
        return context

    async def _ensure_member_session(self, team_session: Any | None) -> Any:
        if self._member_session is not None:
            return self._member_session
        if team_session is None or not self._member_agent_id or not hasattr(team_session, "create_agent_session"):
            return None
        member_session = team_session.create_agent_session(
            agent_id=self._member_agent_id,
            share_stream_writer=False,
        )
        await member_session.pre_run()
        self._member_session = member_session
        return member_session

    async def _finalize_member_session(self) -> None:
        member_session = self._member_session
        if member_session is None:
            return
        await member_session.post_run()
        if self._member_session is member_session:
            self._member_session = None

    async def _pending_team_context(self) -> str | None:
        if self._team_context_tracker is None or self._member_session is None:
            return None
        return await self._team_context_tracker.pending_text(self._member_session)

    async def _commit_team_context(self) -> None:
        if self._team_context_tracker is None or self._member_session is None:
            return
        await self._team_context_tracker.commit(self._member_session)

    # External harnesses own their own rails, memory, workspace, and tools.
    @staticmethod
    def init_cwd_for_round() -> None:
        return None

    @staticmethod
    def has_pending_interrupt() -> bool:
        return False

    @staticmethod
    def is_pending_interrupt_resume_valid(user_input: Any) -> bool:
        _ = user_input
        return False

    @staticmethod
    def find_rails(rail_type: type) -> list[Any]:
        _ = rail_type
        return []

    async def register_rail(self, rail: Any) -> None:
        _ = rail

    async def unregister_rail(self, rail: Any) -> None:
        _ = rail

    @staticmethod
    def register_member_tools(memory_manager: Any) -> None:
        _ = memory_manager

    async def inject_member_memory(self, memory_manager: Any, query: str) -> None:
        _ = memory_manager, query

    @staticmethod
    def set_background_task_controller(controller: Any) -> None:
        _ = controller

    @property
    def workspace(self) -> Optional[Any]:
        return None

    @property
    def sys_operation(self) -> Optional[Any]:
        return None


def _external_input(content: Any) -> ExternalHarnessInput:
    if isinstance(content, ExternalHarnessInput):
        return content
    if isinstance(content, (str, int, float, bool, list, tuple, dict)) or content is None:
        return ExternalHarnessInput(content=content)
    return ExternalHarnessInput(content=str(content))


def _prepend_context(content: ExternalHarnessInput, prefix: str) -> ExternalHarnessInput:
    value = json_value_to_builtin(content.content)
    if isinstance(value, str):
        combined: Any = f"{prefix}\n\n{value}" if value else prefix
    elif isinstance(value, list) and all(isinstance(block, dict) for block in value):
        combined = [{"type": "text", "text": prefix}, *value]
    else:
        combined = f"{prefix}\n\n{json.dumps(value, ensure_ascii=False)}"
    return ExternalHarnessInput(content=combined, metadata=content.metadata)


def _member_round_kind(kind: TurnEventKind) -> str:
    return {
        TurnEventKind.STARTED: "started",
        TurnEventKind.PAUSED: "paused",
        TurnEventKind.RESUMED: "started",
        TurnEventKind.FINISHED: "finished",
        TurnEventKind.ABORTED: "aborted",
        TurnEventKind.FAILED: "failed",
    }[kind]


__all__ = [
    "ContextFactory",
    "ExternalHarnessMemberRuntime",
    "TeamContextAwareRuntime",
]

# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Serialized-turn skeleton shared by the built-in ``HarnessProtocol`` providers.

Every provider in this package drives one external agent session where
accepted inputs run one at a time: an input becomes a pending turn, the
supervisor executes pending turns in acceptance order, and each turn emits
exactly one ``STARTED`` and one terminal ``TurnLifecycleEvent``.  The
skeleton owns the lifecycle state machine, the bounded observation buffer,
the follow-up queue, interaction bookkeeping and checkpoint publishing;
subclasses only translate their SDK into ``_open_session`` /
``_execute_turn`` / ``_steer`` / ``_interrupt_turn`` / ``_close_session``.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Mapping

from openjiuwen.core.common.logging import LazyLogger, LogManager
from openjiuwen.harness_protocol import (
    AbortMode,
    CheckpointConflictError,
    CheckpointReason,
    DeliveryMode,
    EventBufferConfig,
    EventOverflowPolicy,
    HarnessCapability,
    HarnessCard,
    HarnessCheckpoint,
    HarnessContext,
    HarnessError,
    HarnessEvent,
    HarnessEventCursor,
    HarnessInput,
    HarnessInteractionRequest,
    HarnessInteractionResponse,
    HarnessProtocolError,
    HarnessState,
    HarnessStateError,
    HostCapability,
    InteractionCancelReason,
    InteractionResponseStatus,
    JsonObject,
    ProviderInteractionRequest,
    SendReceipt,
    StateChangedEvent,
    TurnError,
    TurnEventKind,
    TurnLifecycleEvent,
    TurnResult,
    TurnStatus,
    TurnTermination,
    TurnTerminationKind,
    UnsupportedHarnessCapabilityError,
    validate_interaction_response,
)
from openjiuwen.harness_providers.stream import BoundedEventBuffer

logger = LazyLogger(lambda: LogManager.get_logger("harness_providers"))


class ProviderStartupError(HarnessError):
    """Raised when a provider runtime cannot start.

    ``error`` carries the provider-neutral failure so a host can classify the
    startup failure without parsing the exception text.
    """

    def __init__(self, message: str, *, error: TurnError) -> None:
        super().__init__(message)
        self.error = error


@dataclass(slots=True)
class PendingTurn:
    """One accepted input waiting for, or currently under, execution."""

    content: HarnessInput
    message_id: str
    turn_id: str
    accepted_mode: DeliveryMode
    abort_requested: bool = False
    abort_mode: AbortMode | None = None
    stop_requested: bool = False


@dataclass(frozen=True, slots=True)
class TurnTiming:
    """Wall-clock and monotonic anchors captured when a turn starts."""

    started_at: float = field(default_factory=time.time)
    started_monotonic: float = field(default_factory=time.monotonic)

    @staticmethod
    def completed_at() -> float:
        """Return the wall-clock completion timestamp."""
        return time.time()

    def duration_ms(self) -> int:
        """Return the non-negative monotonic elapsed time in milliseconds."""
        return max(0, int((time.monotonic() - self.started_monotonic) * 1000))


class SerializedTurnHarness(ABC):
    """Base class implementing ``HarnessProtocol`` over a serialized turn loop.

    Subclasses declare ``card`` as a class attribute and implement the
    provider hooks.  Command methods are concurrency-safe: state changes are
    serialized through ``_command_lock`` and the single supervisor task.
    """

    card: HarnessCard

    def __init__(self, *, event_buffer_capacity: int = 1024) -> None:
        self._buffer_config = EventBufferConfig(
            capacity=event_buffer_capacity,
            overflow=EventOverflowPolicy.BLOCK,
        )
        self._state = HarnessState.TERMINATED
        self._context: HarnessContext | None = None
        self._session_id: str | None = None
        self._event_buffer: BoundedEventBuffer | None = None
        self._sequence = 0
        self._pending: deque[PendingTurn] = deque()
        self._active_turn: PendingTurn | None = None
        self._supervisor_task: asyncio.Task[None] | None = None
        self._stop_task: asyncio.Task[None] | None = None
        self._cycle_started = False
        self._stopping = False
        self._command_lock = asyncio.Lock()
        self._lifecycle_lock = asyncio.Lock()
        self._pending_interactions: dict[str, Any] = {}
        self._latest_checkpoint: HarnessCheckpoint | None = None
        self._checkpoint_sequence = 0
        self._checkpoint_storage_revision: str | None = None

    # ------------------------------------------------------------------
    # Read-only surface
    # ------------------------------------------------------------------

    @property
    def state(self) -> HarnessState:
        return self._state

    @property
    def provider_session_id(self) -> str | None:
        return self._session_id

    @property
    def event_buffer_config(self) -> EventBufferConfig:
        return self._buffer_config

    @property
    def context(self) -> HarnessContext | None:
        """Return the context of the active cycle, or ``None`` between cycles."""
        return self._context

    @property
    def active_turn(self) -> PendingTurn | None:
        """Return the turn currently under execution, if any."""
        return self._active_turn

    # ------------------------------------------------------------------
    # Provider hooks
    # ------------------------------------------------------------------

    def _validate_context(self, context: HarnessContext) -> None:
        """Fail fast on host incompatibility; subclasses extend with SDK limits."""
        self.card.validate_host(
            protocol_version=context.protocol_version,
            capabilities=context.host_capabilities,
        )

    @abstractmethod
    async def _open_session(self, context: HarnessContext) -> str | None:
        """Start the provider runtime and return its native session id."""

    @abstractmethod
    async def _close_session(self) -> None:
        """Release the provider runtime; must unblock an executing turn."""

    @abstractmethod
    async def _execute_turn(self, turn: PendingTurn) -> tuple[TurnEventKind, TurnResult]:
        """Run one accepted input to its terminal result, emitting observations."""

    async def _steer(self, turn: PendingTurn, content: HarnessInput) -> None:
        """Inject ``content`` into the active turn when STEER is declared."""
        _ = turn, content
        raise UnsupportedHarnessCapabilityError(f"{self.card.name} does not support steering")

    async def _interrupt_turn(self, turn: PendingTurn, mode: AbortMode) -> None:
        """Ask the provider to stop the active turn when abort is declared."""
        _ = turn, mode
        raise UnsupportedHarnessCapabilityError(f"{self.card.name} does not support turn abort")

    # ------------------------------------------------------------------
    # HarnessProtocol: lifecycle
    # ------------------------------------------------------------------

    async def start(self, context: HarnessContext) -> None:
        """Validate the host, open the provider session and settle in IDLE."""

        async with self._lifecycle_lock:
            if self._cycle_started:
                raise HarnessStateError(f"{self.card.name} harness is already started")
            self._validate_context(context)
            self._context = context
            self._event_buffer = BoundedEventBuffer(self._buffer_config.capacity)
            self._sequence = 0
            self._pending.clear()
            self._active_turn = None
            self._supervisor_task = None
            self._stop_task = None
            self._stopping = False
            self._pending_interactions.clear()
            self._latest_checkpoint = None
            self._checkpoint_sequence = 0
            self._checkpoint_storage_revision = None
            try:
                self._session_id = await self._open_session(context)
            except BaseException:
                await self._rollback_start()
                raise
            self._cycle_started = True
            await self._transition(HarnessState.IDLE)

    async def _rollback_start(self) -> None:
        try:
            await self._close_session()
        except Exception:
            logger.debug("[%s] session rollback after failed start raised", self.card.name, exc_info=True)
        self._event_buffer = None
        self._context = None
        self._session_id = None

    async def stop(self) -> None:
        """Stop the provider, terminate accepted turns and close the event stream."""

        async with self._lifecycle_lock:
            if not self._cycle_started:
                return
            if self._stop_task is None:
                async with self._command_lock:
                    self._stopping = True
                    active = self._active_turn
                    if active is not None:
                        active.abort_requested = True
                        active.stop_requested = True
                self._stop_task = asyncio.create_task(self._do_stop(), name=f"{self.card.name}_harness_stop")
            stop_task = self._stop_task
        # One cancelled waiter must not cancel the shared teardown.
        await asyncio.shield(stop_task)

    async def _do_stop(self) -> None:
        await self._cancel_pending_interactions(InteractionCancelReason.HARNESS_STOPPED)
        try:
            await self._close_session()
        except Exception:
            logger.exception("[%s] provider session close failed during stop", self.card.name)

        supervisor = self._supervisor_task
        if supervisor is not None and supervisor is not asyncio.current_task():
            try:
                await supervisor
            except Exception as exc:
                logger.debug("[%s] supervisor task failed during stop: %s", self.card.name, exc)

        async with self._command_lock:
            queued = tuple(self._pending)
            self._pending.clear()
            self._active_turn = None
            self._supervisor_task = None
        if queued:
            await self._abort_queued_turns(queued)
        await self._transition(HarnessState.TERMINATED)
        buffer = self._event_buffer
        if buffer is not None:
            await buffer.close()
        self._context = None
        self._cycle_started = False

    # ------------------------------------------------------------------
    # HarnessProtocol: observation
    # ------------------------------------------------------------------

    def events(self) -> HarnessEventCursor:
        buffer = self._event_buffer
        if buffer is None:
            raise HarnessStateError(f"{self.card.name} harness has no active or completed event cycle")
        return buffer.cursor()

    def turn_events(self, turn_id: str | None = None) -> HarnessEventCursor:
        buffer = self._event_buffer
        if buffer is None:
            raise HarnessStateError(f"{self.card.name} harness has no active or completed event cycle")
        return buffer.cursor(turn_id=turn_id, per_turn=True)

    # ------------------------------------------------------------------
    # HarnessProtocol: commands
    # ------------------------------------------------------------------

    async def send(
        self,
        content: HarnessInput,
        *,
        mode: DeliveryMode = DeliveryMode.AUTO,
    ) -> SendReceipt:
        """Accept an input: steer the active turn or queue a serialized turn."""

        if mode is DeliveryMode.STEER:
            if not self.card.supports(HarnessCapability.STEER):
                raise UnsupportedHarnessCapabilityError(f"{self.card.name} does not support steering")
            async with self._command_lock:
                self._require_accepting()
                active = self._active_turn
                if active is None or self._state is not HarnessState.RUNNING:
                    raise HarnessStateError("there is no active turn to steer")
            await self._steer(active, content)
            return SendReceipt(
                message_id=f"message-{uuid.uuid4().hex}",
                turn_id=active.turn_id,
                accepted_mode=DeliveryMode.STEER,
            )

        async with self._command_lock:
            self._require_accepting()
            has_earlier_turn = self._active_turn is not None or bool(self._pending)
            accepted_mode = DeliveryMode.FOLLOW_UP if mode is DeliveryMode.AUTO and has_earlier_turn else mode
            pending = PendingTurn(
                content=content,
                message_id=f"message-{uuid.uuid4().hex}",
                turn_id=f"turn-{uuid.uuid4().hex}",
                accepted_mode=accepted_mode,
            )
            self._pending.append(pending)
            if self._supervisor_task is None or self._supervisor_task.done():
                supervisor = asyncio.create_task(
                    self._supervise_turns(),
                    name=f"{self.card.name}_turn_supervisor",
                )
                self._supervisor_task = supervisor
                supervisor.add_done_callback(self._clear_supervisor_task)
        return SendReceipt(
            message_id=pending.message_id,
            turn_id=pending.turn_id,
            accepted_mode=pending.accepted_mode,
        )

    def _require_accepting(self) -> None:
        if not self._cycle_started or self._stopping or self._state is HarnessState.TERMINATED:
            raise HarnessStateError(f"cannot send to a stopped {self.card.name} harness")

    async def abort(self, *, mode: AbortMode = AbortMode.GRACEFUL) -> None:
        """Abort the active turn; a no-op when nothing is running."""

        capability = HarnessCapability.FORCE_ABORT if mode is AbortMode.FORCE else HarnessCapability.GRACEFUL_ABORT
        if not self.card.supports(capability):
            raise UnsupportedHarnessCapabilityError(f"{self.card.name} does not support {capability.value}")
        async with self._command_lock:
            active = self._active_turn
            if active is None:
                return
            active.abort_requested = True
            active.abort_mode = mode
        await self._cancel_pending_interactions(InteractionCancelReason.TURN_ABORTED)
        await self._interrupt_turn(active, mode)

    async def pause(self) -> None:
        raise UnsupportedHarnessCapabilityError(f"{self.card.name} does not support pause/resume")

    async def resume(self, *, query: HarnessInput | None = None) -> None:
        _ = query
        raise UnsupportedHarnessCapabilityError(f"{self.card.name} does not support pause/resume")

    async def export_checkpoint(self) -> HarnessCheckpoint | None:
        return self._latest_checkpoint

    # ------------------------------------------------------------------
    # Supervisor
    # ------------------------------------------------------------------

    async def _supervise_turns(self) -> None:
        while True:
            async with self._command_lock:
                if self._stopping:
                    queued = tuple(self._pending)
                    self._pending.clear()
                    active = None
                elif not self._pending:
                    return
                else:
                    active = self._pending.popleft()
                    queued = ()
                    self._active_turn = active

            if active is None:
                await self._abort_queued_turns(queued)
                return

            await self._transition(HarnessState.RUNNING)
            await self._emit(TurnLifecycleEvent(kind=TurnEventKind.STARTED), turn=active)
            try:
                terminal_kind, result = await self._execute_turn(active)
            except Exception as exc:
                logger.exception("[%s] turn %s crashed inside the provider", self.card.name, active.turn_id)
                terminal_kind, result = self._crash_result(active, exc)
            await self._emit(TurnLifecycleEvent(kind=terminal_kind, result=result), turn=active)

            async with self._command_lock:
                self._active_turn = None
                if self._stopping:
                    queued = tuple(self._pending)
                    self._pending.clear()
                else:
                    queued = ()
                    if not self._pending:
                        # IDLE is a whole-chain quiescence signal, not a gap
                        # between serialized follow-up turns.
                        await self._transition(HarnessState.IDLE)
            if queued:
                await self._abort_queued_turns(queued)
                return
            if self._stopping:
                return

    def _crash_result(self, turn: PendingTurn, exc: BaseException) -> tuple[TurnEventKind, TurnResult]:
        if turn.abort_requested:
            kind = TurnTerminationKind.HARNESS_STOP if turn.stop_requested else TurnTerminationKind.USER_ABORT
            return TurnEventKind.ABORTED, TurnResult(
                status=TurnStatus.INTERRUPTED,
                termination=TurnTermination(kind=kind, message=f"{self.card.name} turn was interrupted"),
            )
        return TurnEventKind.FAILED, TurnResult(
            status=TurnStatus.FAILED,
            error=TurnError(
                message=f"{self.card.name} turn crashed: {type(exc).__name__}",
                code=type(exc).__name__,
                category="sdk_error",
            ),
        )

    def _clear_supervisor_task(self, task: asyncio.Task[None]) -> None:
        if self._supervisor_task is task:
            self._supervisor_task = None

    async def _abort_queued_turns(self, queued: tuple[PendingTurn, ...]) -> None:
        for turn in queued:
            await self._emit(TurnLifecycleEvent(kind=TurnEventKind.STARTED), turn=turn)
            await self._emit(
                TurnLifecycleEvent(kind=TurnEventKind.ABORTED, result=build_queued_stop_result(self.card.name)),
                turn=turn,
            )

    # ------------------------------------------------------------------
    # Emission helpers
    # ------------------------------------------------------------------

    async def _transition(self, new_state: HarnessState) -> None:
        old_state = self._state
        if old_state is new_state:
            return
        self._state = new_state
        await self._emit(StateChangedEvent(old=old_state, new=new_state))

    async def _emit(
        self,
        payload: Any,
        *,
        turn: PendingTurn | None = None,
        item_id: str | None = None,
        provider_session_id: str | None = None,
    ) -> None:
        context = self._context
        buffer = self._event_buffer
        if context is None or buffer is None:
            raise HarnessProtocolError(f"cannot emit a {self.card.name} event outside an active cycle")
        self._sequence += 1
        await buffer.put(
            HarnessEvent(
                sequence=self._sequence,
                timestamp=time.time(),
                event=payload,
                host_session_id=context.host_session_id,
                agent_id=context.agent_id,
                provider_session_id=provider_session_id or self._session_id,
                turn_id=turn.turn_id if turn else None,
                item_id=item_id,
                correlation_id=turn.message_id if turn else None,
                causation_ids=(turn.message_id,) if turn else (),
            )
        )

    # ------------------------------------------------------------------
    # Interactions
    # ------------------------------------------------------------------

    def _has_host_interactions(self) -> bool:
        context = self._context
        return context is not None and context.interactions is not None

    async def _request_interaction(self, request: HarnessInteractionRequest) -> HarnessInteractionResponse | None:
        """Await the host response for ``request``; ``None`` when no handler exists."""

        context = self._context
        if context is None or context.interactions is None:
            return None
        handler = context.interactions
        self._pending_interactions[request.request_id] = handler
        try:
            response = await handler.handle(request)
        finally:
            self._pending_interactions.pop(request.request_id, None)
        return validate_interaction_response(request, response)

    async def _confirm_provider_extension(self, request_type: str, payload: Mapping[str, Any]) -> bool:
        """Ask the host to ratify a provider-specific decision, if it can.

        Sends a ``ProviderInteractionRequest`` when the host declared
        ``PROVIDER_INTERACTION`` and supplied a handler; ``True`` means the host
        completed the request. Without such a host the decision is the
        provider's own and the call returns ``True``.
        """

        context = self._context
        if context is None or context.interactions is None:
            return True
        if HostCapability.PROVIDER_INTERACTION not in context.host_capabilities:
            return True
        active = self._active_turn
        request = ProviderInteractionRequest(
            request_id=f"{self.card.name}:{request_type}:{uuid.uuid4().hex}",
            provider=self.card.name,
            request_type=request_type,
            schema_version="1",
            payload=payload,
            provider_session_id=self._session_id,
            turn_id=active.turn_id if active is not None else None,
        )
        response = await self._request_interaction(request)
        return response is not None and response.status is InteractionResponseStatus.COMPLETED

    async def _cancel_pending_interactions(self, reason: InteractionCancelReason) -> None:
        pending = dict(self._pending_interactions)
        self._pending_interactions.clear()
        for request_id, handler in pending.items():
            try:
                await handler.cancel(request_id, reason=reason)
            except Exception:
                logger.exception("[%s] failed to cancel interaction %s", self.card.name, request_id)

    # ------------------------------------------------------------------
    # Checkpoints
    # ------------------------------------------------------------------

    async def _publish_checkpoint(
        self,
        data: Mapping[str, Any],
        *,
        reason: CheckpointReason,
        schema_version: str = "1",
    ) -> HarnessCheckpoint | None:
        """Record the latest checkpoint and push it to the host sink, best effort."""

        context = self._context
        if context is None:
            return None
        self._checkpoint_sequence += 1
        checkpoint = HarnessCheckpoint(
            provider=self.card.name,
            schema_version=schema_version,
            agent_id=context.agent_id,
            host_session_id=context.host_session_id,
            checkpoint_id=uuid.uuid4().hex,
            sequence=self._checkpoint_sequence,
            data=data,
            provider_session_id=self._session_id,
        )
        self._latest_checkpoint = checkpoint
        sink = context.checkpoint_sink
        if sink is None:
            return checkpoint
        try:
            receipt = await sink.save(
                checkpoint,
                reason=reason,
                expected_storage_revision=self._checkpoint_storage_revision,
            )
        except CheckpointConflictError:
            logger.warning("[%s] checkpoint %s rejected as stale", self.card.name, checkpoint.checkpoint_id)
            return checkpoint
        except Exception:
            logger.exception("[%s] checkpoint sink failed", self.card.name)
            return checkpoint
        self._checkpoint_storage_revision = receipt.storage_revision
        return checkpoint

    def _restored_checkpoint_data(self, context: HarnessContext) -> JsonObject | None:
        """Return checkpoint data owned by this provider, validating the scope."""

        checkpoint = context.checkpoint
        if checkpoint is None:
            return None
        if checkpoint.provider != self.card.name:
            raise HarnessProtocolError(
                f"checkpoint provider {checkpoint.provider!r} does not belong to {self.card.name!r}"
            )
        if checkpoint.agent_id != context.agent_id or checkpoint.host_session_id != context.host_session_id:
            raise HarnessProtocolError("checkpoint scope does not match the harness context")
        self._checkpoint_sequence = max(self._checkpoint_sequence, checkpoint.sequence)
        self._checkpoint_storage_revision = checkpoint.revision
        return checkpoint.data


def build_queued_stop_result(provider_name: str) -> TurnResult:
    """Return the terminal result for an accepted turn stopped before execution."""

    now = time.time()
    return TurnResult(
        status=TurnStatus.INTERRUPTED,
        termination=TurnTermination(
            kind=TurnTerminationKind.HARNESS_STOP,
            message=f"{provider_name} stopped before the queued turn started",
        ),
        started_at=now,
        completed_at=now,
        duration_ms=0,
    )


def interrupted_result(
    turn: PendingTurn,
    *,
    provider_name: str,
    timing: TurnTiming,
    messages: tuple[Any, ...] = (),
    final_output: Any = None,
    usage: Any = None,
) -> TurnResult:
    """Build the INTERRUPTED result matching how ``turn`` was stopped."""

    if turn.stop_requested:
        kind = TurnTerminationKind.HARNESS_STOP
        message = f"{provider_name} stopped before the turn completed"
    else:
        kind = TurnTerminationKind.USER_ABORT
        message = f"{provider_name} turn was aborted"
    return TurnResult(
        status=TurnStatus.INTERRUPTED,
        messages=messages,
        final_output=final_output,
        termination=TurnTermination(kind=kind, message=message),
        usage=usage,
        started_at=timing.started_at,
        completed_at=timing.completed_at(),
        duration_ms=timing.duration_ms(),
    )


__all__ = [
    "PendingTurn",
    "ProviderStartupError",
    "SerializedTurnHarness",
    "TurnTiming",
    "build_queued_stop_result",
    "interrupted_result",
]

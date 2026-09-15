# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Behavior tests for the shared serialized-turn harness skeleton."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from openjiuwen.harness_protocol import (
    PROTOCOL_VERSION,
    AbortMode,
    CheckpointReason,
    CheckpointSaveReceipt,
    DeliveryMode,
    HarnessCapability,
    HarnessCard,
    HarnessCheckpoint,
    HarnessContext,
    HarnessEvent,
    HarnessInput,
    HarnessProtocol,
    HarnessState,
    HarnessStateError,
    HostCapability,
    InteractionCancelReason,
    InteractionResponseStatus,
    OutputEvent,
    OutputKind,
    OutputOperation,
    StateChangedEvent,
    TurnEventKind,
    TurnLifecycleEvent,
    TurnResult,
    TurnStatus,
    UnsupportedHarnessCapabilityError,
    UserInputRequest,
    UserInputResponse,
)
from openjiuwen.harness_providers.base import PendingTurn, SerializedTurnHarness, TurnTiming
from tests.test_logger import logger


class _ScriptedHarness(SerializedTurnHarness):
    """Provider whose turns are scripted from the outside."""

    card = HarnessCard(
        name="scripted",
        implementation_version="1.0",
        protocol_version=PROTOCOL_VERSION,
        capabilities=frozenset({HarnessCapability.STEER, HarnessCapability.GRACEFUL_ABORT}),
        optional_host_capabilities=frozenset({HostCapability.USER_INPUT, HostCapability.CHECKPOINT_SINK}),
    )

    def __init__(self) -> None:
        super().__init__(event_buffer_capacity=64)
        self.opened = 0
        self.closed = 0
        self.steered: list[str] = []
        self.interrupted: list[AbortMode] = []
        self.release = asyncio.Event()
        self.release.set()
        self.crash_next = False
        self.ask_user_next = False
        self.responses: list[Any] = []

    async def _open_session(self, context: HarnessContext) -> str | None:
        _ = context
        self.opened += 1
        await self._publish_checkpoint({"cursor": 1}, reason=CheckpointReason.SESSION_ACTIVATED)
        return "scripted-session"

    async def _close_session(self) -> None:
        self.closed += 1
        self.release.set()

    async def _execute_turn(self, turn: PendingTurn) -> tuple[TurnEventKind, TurnResult]:
        timing = TurnTiming()
        if self.crash_next:
            self.crash_next = False
            raise RuntimeError("boom")
        if self.ask_user_next:
            self.ask_user_next = False
            response = await self._request_interaction(
                UserInputRequest(request_id="ask-1", prompt="color?", turn_id=turn.turn_id)
            )
            self.responses.append(response)
        await self._emit(
            OutputEvent(output_id=f"{turn.turn_id}:answer", kind=OutputKind.TEXT, content="hi", operation=OutputOperation.DELTA),
            turn=turn,
        )
        await self.release.wait()
        if turn.abort_requested:
            from openjiuwen.harness_providers.base import interrupted_result

            return TurnEventKind.ABORTED, interrupted_result(turn, provider_name="scripted", timing=timing)
        return TurnEventKind.FINISHED, TurnResult(
            status=TurnStatus.COMPLETED,
            final_output=f"done:{turn.content.content}",
            started_at=timing.started_at,
            completed_at=timing.completed_at(),
            duration_ms=timing.duration_ms(),
        )

    async def _steer(self, turn: PendingTurn, content: HarnessInput) -> None:
        self.steered.append(str(content.content))

    async def _interrupt_turn(self, turn: PendingTurn, mode: AbortMode) -> None:
        self.interrupted.append(mode)
        self.release.set()


class _RecordingSink:
    def __init__(self) -> None:
        self.saved: list[tuple[HarnessCheckpoint, CheckpointReason]] = []

    async def save(
        self,
        checkpoint: HarnessCheckpoint,
        *,
        reason: CheckpointReason,
        expected_storage_revision: str | None = None,
    ) -> CheckpointSaveReceipt:
        _ = expected_storage_revision
        self.saved.append((checkpoint, reason))
        return CheckpointSaveReceipt(
            checkpoint_id=checkpoint.checkpoint_id,
            sequence=checkpoint.sequence,
            storage_revision=f"rev-{len(self.saved)}",
        )


class _AnsweringHandler:
    def __init__(self) -> None:
        self.cancelled: list[tuple[str, InteractionCancelReason]] = []
        self.block = asyncio.Event()
        self.block.set()

    async def handle(self, request: Any) -> Any:
        await self.block.wait()
        return UserInputResponse(request_id=request.request_id, status=InteractionResponseStatus.COMPLETED, content="teal")

    async def cancel(self, request_id: str, *, reason: InteractionCancelReason = InteractionCancelReason.PROVIDER_WITHDREW) -> None:
        self.cancelled.append((request_id, reason))
        self.block.set()


def _context(**overrides: Any) -> HarnessContext:
    values: dict[str, Any] = {
        "agent_name": "worker",
        "agent_id": "agent-1",
        "host_session_id": "host-1",
        "system_prompt": "",
    }
    values.update(overrides)
    return HarnessContext(**values)


async def _collect_turn(harness: HarnessProtocol, turn_id: str) -> list[HarnessEvent]:
    events: list[HarnessEvent] = []
    async for event in harness.turn_events(turn_id):
        events.append(event)
    return events


def _terminal(events: list[HarnessEvent]) -> TurnLifecycleEvent:
    payload = events[-1].event
    assert isinstance(payload, TurnLifecycleEvent)
    return payload


@pytest.mark.asyncio
async def test_start_settles_idle_and_publishes_checkpoint() -> None:
    harness = _ScriptedHarness()
    sink = _RecordingSink()
    assert isinstance(harness, HarnessProtocol)
    await harness.start(_context(checkpoint_sink=sink, host_capabilities=frozenset({HostCapability.CHECKPOINT_SINK})))

    assert harness.state is HarnessState.IDLE
    assert harness.provider_session_id == "scripted-session"
    checkpoint = await harness.export_checkpoint()
    assert checkpoint is not None and checkpoint.sequence == 1
    assert [reason for _, reason in sink.saved] == [CheckpointReason.SESSION_ACTIVATED]
    await harness.stop()
    assert harness.state is HarnessState.TERMINATED
    await harness.stop()
    assert harness.closed == 1


@pytest.mark.asyncio
async def test_turns_are_serialized_with_follow_up_receipts() -> None:
    harness = _ScriptedHarness()
    await harness.start(_context())
    first = await harness.send(HarnessInput(content="one"))
    second = await harness.send(HarnessInput(content="two"))
    assert first.accepted_mode is DeliveryMode.AUTO
    assert second.accepted_mode is DeliveryMode.FOLLOW_UP

    first_events = await _collect_turn(harness, first.turn_id)
    second_events = await _collect_turn(harness, second.turn_id)
    assert _terminal(first_events).result.final_output == "done:one"
    assert _terminal(second_events).result.final_output == "done:two"
    assert [event.sequence for event in first_events] == sorted(event.sequence for event in first_events)
    logger.info("serialized turn sequences: %s", [event.sequence for event in first_events + second_events])
    await harness.stop()


@pytest.mark.asyncio
async def test_steer_targets_the_active_turn_and_fails_when_idle() -> None:
    harness = _ScriptedHarness()
    await harness.start(_context())
    with pytest.raises(HarnessStateError):
        await harness.send(HarnessInput(content="early"), mode=DeliveryMode.STEER)
    harness.release.clear()
    receipt = await harness.send(HarnessInput(content="task"))
    await asyncio.sleep(0)
    steer = await harness.send(HarnessInput(content="also"), mode=DeliveryMode.STEER)
    assert steer.turn_id == receipt.turn_id
    assert steer.accepted_mode is DeliveryMode.STEER
    assert harness.steered == ["also"]
    harness.release.set()
    events = await _collect_turn(harness, receipt.turn_id)
    assert _terminal(events).kind is TurnEventKind.FINISHED
    await harness.stop()


@pytest.mark.asyncio
async def test_abort_interrupts_and_cancels_pending_interactions() -> None:
    harness = _ScriptedHarness()
    handler = _AnsweringHandler()
    handler.block.clear()
    harness.ask_user_next = True
    await harness.start(_context(interactions=handler, host_capabilities=frozenset({HostCapability.USER_INPUT})))
    harness.release.clear()
    receipt = await harness.send(HarnessInput(content="task"))
    await asyncio.sleep(0.01)
    await harness.abort(mode=AbortMode.GRACEFUL)
    assert harness.interrupted == [AbortMode.GRACEFUL]
    assert handler.cancelled == [("ask-1", InteractionCancelReason.TURN_ABORTED)]
    events = await _collect_turn(harness, receipt.turn_id)
    assert _terminal(events).kind is TurnEventKind.ABORTED
    assert harness.state is HarnessState.IDLE
    with pytest.raises(UnsupportedHarnessCapabilityError):
        await harness.abort(mode=AbortMode.FORCE)
    await harness.stop()


@pytest.mark.asyncio
async def test_interaction_response_is_validated_and_delivered() -> None:
    harness = _ScriptedHarness()
    handler = _AnsweringHandler()
    harness.ask_user_next = True
    await harness.start(_context(interactions=handler, host_capabilities=frozenset({HostCapability.USER_INPUT})))
    receipt = await harness.send(HarnessInput(content="task"))
    events = await _collect_turn(harness, receipt.turn_id)
    assert _terminal(events).kind is TurnEventKind.FINISHED
    assert harness.responses[0].content == "teal"
    await harness.stop()


@pytest.mark.asyncio
async def test_stop_aborts_queued_turns_and_closes_the_stream() -> None:
    harness = _ScriptedHarness()
    await harness.start(_context())
    harness.release.clear()
    active = await harness.send(HarnessInput(content="active"))
    queued = await harness.send(HarnessInput(content="queued"))
    await asyncio.sleep(0.01)
    cursor = harness.events()
    await harness.stop()
    events = [event async for event in cursor]
    terminals = {
        event.turn_id: event.event.kind
        for event in events
        if isinstance(event.event, TurnLifecycleEvent) and event.event.kind is not TurnEventKind.STARTED
    }
    assert terminals == {active.turn_id: TurnEventKind.ABORTED, queued.turn_id: TurnEventKind.ABORTED}
    assert isinstance(events[-1].event, StateChangedEvent) and events[-1].event.new is HarnessState.TERMINATED
    with pytest.raises(HarnessStateError):
        await harness.send(HarnessInput(content="late"))


@pytest.mark.asyncio
async def test_provider_crash_becomes_a_failed_turn() -> None:
    harness = _ScriptedHarness()
    await harness.start(_context())
    harness.crash_next = True
    receipt = await harness.send(HarnessInput(content="task"))
    events = await _collect_turn(harness, receipt.turn_id)
    terminal = _terminal(events)
    assert terminal.kind is TurnEventKind.FAILED
    assert terminal.result.error is not None and terminal.result.error.code == "RuntimeError"
    assert harness.state is HarnessState.IDLE
    await harness.stop()


@pytest.mark.asyncio
async def test_single_consumer_lease_is_enforced() -> None:
    harness = _ScriptedHarness()
    await harness.start(_context())
    cursor = harness.events()
    with pytest.raises(HarnessStateError):
        harness.events()
    await cursor.aclose()
    harness.events()
    await harness.stop()

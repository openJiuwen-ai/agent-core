# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Contract-model tests for the third-party agent harness protocol."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from typing import AsyncIterator

import pytest

from openjiuwen.agent_teams.external.protocol import (
    MAX_CHECKPOINT_BYTES,
    PROTOCOL_VERSION,
    AbortMode,
    BeforeToolContext,
    CheckpointConflictError,
    CheckpointReason,
    CheckpointSaveReceipt,
    ContentBlock,
    DeliveryMode,
    DiagnosticEvent,
    DiagnosticLevel,
    EventBufferConfig,
    EventOverflowPolicy,
    EventRetention,
    ExternalHarnessCard,
    ExternalHarnessContext,
    ExternalHarnessInput,
    ExternalHarnessProtocol,
    ExternalHarnessProtocolError,
    ExternalHarnessProvider,
    HarnessCapability,
    HarnessCheckpoint,
    HarnessCheckpointSink,
    HarnessEvent,
    HarnessEventCursor,
    HarnessInteractionHandler,
    HarnessInteractionRequest,
    HarnessInteractionResponse,
    HostCapability,
    InteractionCancelReason,
    InteractionResponseStatus,
    McpServerConfig,
    McpTransport,
    MessageRole,
    MonetaryAmount,
    OutputChannel,
    OutputEvent,
    OutputKind,
    OutputOperation,
    ProviderEvent,
    ResumePolicy,
    SendReceipt,
    StateChangedEvent,
    ToolApprovalDecision,
    ToolApprovalRequest,
    ToolApprovalResponse,
    ToolDecision,
    ToolDecisionKind,
    TurnError,
    TurnEventKind,
    TurnLifecycleEvent,
    TurnMessage,
    TurnResult,
    TurnStatus,
    TurnTermination,
    TurnTerminationKind,
    TurnUsage,
    UnknownEvent,
    UsageUpdatedEvent,
    UsageUpdateMode,
    UserInputRequest,
    UserInputResponse,
    event_retention,
    harness_event_from_dict,
    harness_event_to_dict,
    validate_interaction_response,
)
from openjiuwen.agent_teams.harness import HarnessState


class _Harness:
    event_buffer_config = EventBufferConfig(capacity=16)
    card = ExternalHarnessCard(
        name="test-harness",
        implementation_version="1.0.0",
        capabilities=frozenset(
            {
                HarnessCapability.STEER,
                HarnessCapability.CHECKPOINT,
            }
        ),
        required_host_capabilities=frozenset({HostCapability.TOOL_APPROVAL}),
    )

    def __init__(self) -> None:
        self._state = HarnessState.IDLE
        turn_id = "turn-1"
        self._stream = (
            HarnessEvent(
                sequence=1,
                timestamp=0.0,
                team_session_id="team-session-1",
                member_agent_id="team-a_member-a",
                event=StateChangedEvent(old=HarnessState.IDLE, new=HarnessState.RUNNING),
            ),
            HarnessEvent(
                sequence=2,
                timestamp=0.1,
                team_session_id="team-session-1",
                member_agent_id="team-a_member-a",
                event=TurnLifecycleEvent(kind=TurnEventKind.STARTED),
                turn_id=turn_id,
            ),
            HarnessEvent(
                sequence=3,
                timestamp=0.2,
                team_session_id="team-session-1",
                member_agent_id="team-a_member-a",
                event=TurnLifecycleEvent(kind=TurnEventKind.PAUSED),
                turn_id=turn_id,
            ),
            HarnessEvent(
                sequence=4,
                timestamp=0.3,
                team_session_id="team-session-1",
                member_agent_id="team-a_member-a",
                event=TurnLifecycleEvent(kind=TurnEventKind.RESUMED),
                turn_id=turn_id,
            ),
            HarnessEvent(
                sequence=5,
                timestamp=0.4,
                team_session_id="team-session-1",
                member_agent_id="team-a_member-a",
                event=OutputEvent(
                    output_id="answer-1",
                    kind=OutputKind.TEXT,
                    content="done",
                    operation=OutputOperation.FINAL,
                ),
                turn_id=turn_id,
            ),
            HarnessEvent(
                sequence=6,
                timestamp=0.5,
                team_session_id="team-session-1",
                member_agent_id="team-a_member-a",
                event=TurnLifecycleEvent(
                    kind=TurnEventKind.FINISHED,
                    result=TurnResult(status=TurnStatus.COMPLETED, final_output="done"),
                ),
                turn_id=turn_id,
            ),
        )
        self._stream_cursor = 0

    @property
    def state(self) -> HarnessState:
        return self._state

    @property
    def session_id(self) -> str | None:
        return "provider-session"

    async def start(self, context: ExternalHarnessContext) -> None:
        _ = context
        self._state = HarnessState.IDLE

    async def stop(self) -> None:
        self._state = HarnessState.TERMINATED

    async def events(self) -> AsyncIterator[HarnessEvent]:
        while self._stream_cursor < len(self._stream):
            event = self._stream[self._stream_cursor]
            self._stream_cursor += 1
            yield event

    async def turn_events(self, turn_id: str | None = None) -> AsyncIterator[HarnessEvent]:
        selected_turn_id = None
        async for event in self.events():
            payload = event.event
            if selected_turn_id is None:
                if not (
                    isinstance(payload, TurnLifecycleEvent)
                    and payload.kind is TurnEventKind.STARTED
                    and (turn_id is None or event.turn_id == turn_id)
                ):
                    continue
                selected_turn_id = event.turn_id

            yield event
            if (
                event.turn_id == selected_turn_id
                and isinstance(payload, TurnLifecycleEvent)
                and payload.kind in {TurnEventKind.FINISHED, TurnEventKind.ABORTED, TurnEventKind.FAILED}
            ):
                return

    async def send(
        self,
        content: ExternalHarnessInput,
        *,
        mode: DeliveryMode = DeliveryMode.AUTO,
    ) -> SendReceipt:
        _ = content
        return SendReceipt(message_id="message-1", turn_id="turn-1", accepted_mode=mode)

    async def abort(self, *, mode: AbortMode = AbortMode.GRACEFUL) -> None:
        _ = mode

    async def pause(self) -> None:
        pass

    async def resume(self, *, query: ExternalHarnessInput | None = None) -> None:
        _ = query

    async def export_checkpoint(self) -> HarnessCheckpoint:
        return HarnessCheckpoint(
            provider="test",
            schema_version="1",
            member_agent_id="team-a_member-a",
            team_session_id="team-session-1",
            checkpoint_id="checkpoint-1",
            sequence=1,
            session_id="provider-session",
        )


class _Provider:
    card = _Harness.card

    def create(self, config):
        _ = config
        return _Harness()


class _CheckpointSink:
    def __init__(self) -> None:
        self.saved: tuple[HarnessCheckpoint, CheckpointReason] | None = None
        self._receipts: dict[str, CheckpointSaveReceipt] = {}
        self._latest_sequence = -1
        self._storage_revision: str | None = None

    async def save(
        self,
        checkpoint: HarnessCheckpoint,
        *,
        reason: CheckpointReason,
        expected_storage_revision: str | None = None,
    ) -> CheckpointSaveReceipt:
        if checkpoint.checkpoint_id in self._receipts:
            return self._receipts[checkpoint.checkpoint_id]
        if checkpoint.sequence <= self._latest_sequence:
            raise CheckpointConflictError("stale checkpoint sequence")
        if expected_storage_revision is not None and expected_storage_revision != self._storage_revision:
            raise CheckpointConflictError("checkpoint compare-and-set failed")
        self.saved = (checkpoint, reason)
        self._latest_sequence = checkpoint.sequence
        self._storage_revision = f"storage-{checkpoint.sequence}"
        receipt = CheckpointSaveReceipt(
            checkpoint_id=checkpoint.checkpoint_id,
            sequence=checkpoint.sequence,
            storage_revision=self._storage_revision,
        )
        self._receipts[checkpoint.checkpoint_id] = receipt
        return receipt


class _InteractionHandler:
    def __init__(self) -> None:
        self.cancelled: list[tuple[str, InteractionCancelReason]] = []

    async def handle(self, request: HarnessInteractionRequest) -> HarnessInteractionResponse:
        if isinstance(request, ToolApprovalRequest):
            return ToolApprovalResponse(request_id=request.request_id, decision=ToolApprovalDecision.ALLOW)
        if isinstance(request, UserInputRequest):
            return UserInputResponse(
                request_id=request.request_id,
                status=InteractionResponseStatus.COMPLETED,
                content="host answer",
            )
        raise AssertionError(f"unexpected interaction: {type(request).__name__}")

    async def cancel(
        self,
        request_id: str,
        *,
        reason: InteractionCancelReason = InteractionCancelReason.PROVIDER_WITHDREW,
    ) -> None:
        self.cancelled.append((request_id, reason))


def test_structural_protocols_accept_complete_implementations() -> None:
    assert isinstance(_Harness(), ExternalHarnessProtocol)
    assert isinstance(_Provider(), ExternalHarnessProvider)
    assert isinstance(_CheckpointSink(), HarnessCheckpointSink)
    assert isinstance(_InteractionHandler(), HarnessInteractionHandler)


@pytest.mark.asyncio
async def test_event_cursor_supports_explicit_close() -> None:
    harness = _Harness()
    cursor = harness.events()

    assert isinstance(cursor, HarnessEventCursor)
    assert (await anext(cursor)).sequence == 1
    await cursor.aclose()
    await cursor.aclose()

    remaining = [event async for event in harness.events()]
    assert [event.sequence for event in remaining] == [2, 3, 4, 5, 6]


def test_card_is_immutable_and_reports_capabilities() -> None:
    card = _Harness.card

    assert card.protocol_version == PROTOCOL_VERSION == "4.0"
    assert card.supports(HarnessCapability.STEER)
    assert not card.supports(HarnessCapability.PAUSE_RESUME)
    card.validate_host(protocol_version=PROTOCOL_VERSION, capabilities=frozenset({HostCapability.TOOL_APPROVAL}))

    with pytest.raises(ExternalHarnessProtocolError, match="missing required capabilities"):
        card.validate_host(protocol_version=PROTOCOL_VERSION, capabilities=frozenset())
    with pytest.raises(ExternalHarnessProtocolError, match="is not supported"):
        card.validate_host(protocol_version="99.0", capabilities=frozenset({HostCapability.TOOL_APPROVAL}))

    with pytest.raises(FrozenInstanceError):
        card.name = "changed"  # type: ignore[misc]


def test_context_keeps_checkpoint_and_host_services() -> None:
    mcp = McpServerConfig(
        name="team",
        transport=McpTransport.STDIO,
        command=("openjiuwen-team-mcp",),
        env={"MEMBER_SCOPE": "member-a"},
    )
    checkpoint = HarnessCheckpoint(
        provider="claude-code",
        schema_version="1",
        member_agent_id="team-a_member-a",
        team_session_id="session-a",
        checkpoint_id="checkpoint-1",
        sequence=1,
        data={"conversation_id": "conversation-a"},
    )
    checkpoint_sink = _CheckpointSink()
    interactions = _InteractionHandler()
    context = ExternalHarnessContext(
        team_name="team-a",
        member_name="member-a",
        member_agent_id="team-a_member-a",
        team_session_id="session-a",
        system_prompt="You are a teammate.",
        host_capabilities=frozenset({HostCapability.TOOL_APPROVAL}),
        resume_policy=ResumePolicy.REQUIRE_RESUME,
        checkpoint=checkpoint,
        checkpoint_sink=checkpoint_sink,
        mcp_servers=(mcp,),
        interactions=interactions,
    )

    assert context.resume_policy is ResumePolicy.REQUIRE_RESUME
    assert context.checkpoint is checkpoint
    assert context.checkpoint_sink is checkpoint_sink
    assert context.interactions is interactions
    assert context.host_capabilities == frozenset({HostCapability.TOOL_APPROVAL})
    assert context.mcp_servers == (mcp,)


@pytest.mark.asyncio
async def test_checkpoint_sink_accepts_proactive_updates() -> None:
    sink = _CheckpointSink()
    checkpoint = await _Harness().export_checkpoint()

    receipt = await sink.save(checkpoint, reason=CheckpointReason.TURN_COMPLETED)
    retried = await sink.save(checkpoint, reason=CheckpointReason.TURN_COMPLETED)

    assert sink.saved == (checkpoint, CheckpointReason.TURN_COMPLETED)
    assert receipt == retried == CheckpointSaveReceipt("checkpoint-1", 1, "storage-1")

    stale = HarnessCheckpoint(
        provider="test",
        schema_version="1",
        member_agent_id="team-a_member-a",
        team_session_id="team-session-1",
        checkpoint_id="checkpoint-stale",
        sequence=0,
    )
    with pytest.raises(CheckpointConflictError, match="stale"):
        await sink.save(stale, reason=CheckpointReason.PERIODIC)


def test_checkpoint_requires_provider_version_and_member_scope() -> None:
    with pytest.raises(ValueError, match="provider must not be empty"):
        HarnessCheckpoint(
            provider="",
            schema_version="1",
            member_agent_id="member-a",
            team_session_id="session-a",
            checkpoint_id="checkpoint-1",
            sequence=1,
        )
    with pytest.raises(ValueError, match="schema_version must not be empty"):
        HarnessCheckpoint(
            provider="acme",
            schema_version="",
            member_agent_id="member-a",
            team_session_id="session-a",
            checkpoint_id="checkpoint-1",
            sequence=1,
        )
    with pytest.raises(ValueError, match="member_agent_id must not be empty"):
        HarnessCheckpoint(
            provider="acme",
            schema_version="1",
            member_agent_id="",
            team_session_id="session-a",
            checkpoint_id="checkpoint-1",
            sequence=1,
        )
    with pytest.raises(ValueError, match="team_session_id must not be empty"):
        HarnessCheckpoint(
            provider="acme",
            schema_version="1",
            member_agent_id="member-a",
            team_session_id="",
            checkpoint_id="checkpoint-1",
            sequence=1,
        )
    with pytest.raises(ValueError, match="checkpoint_id must not be empty"):
        HarnessCheckpoint(
            provider="acme",
            schema_version="1",
            member_agent_id="member-a",
            team_session_id="session-a",
            checkpoint_id="",
            sequence=1,
        )
    with pytest.raises(ValueError, match="sequence must be non-negative"):
        HarnessCheckpoint(
            provider="acme",
            schema_version="1",
            member_agent_id="member-a",
            team_session_id="session-a",
            checkpoint_id="checkpoint-1",
            sequence=-1,
        )


def test_checkpoint_rejects_oversized_provider_data() -> None:
    with pytest.raises(ValueError, match="exceeds"):
        HarnessCheckpoint(
            provider="acme",
            schema_version="1",
            member_agent_id="member-a",
            team_session_id="session-a",
            checkpoint_id="checkpoint-1",
            sequence=1,
            data={"payload": "x" * MAX_CHECKPOINT_BYTES},
        )


@pytest.mark.asyncio
async def test_interaction_handler_returns_correlated_response_and_cancels() -> None:
    handler = _InteractionHandler()
    request = ToolApprovalRequest(
        request_id="approval-1",
        call_id="call-1",
        tool_name="shell",
        arguments={"command": "pwd"},
        turn_id="turn-1",
        deadline_at=2_000_000_000.0,
    )

    response = validate_interaction_response(request, await handler.handle(request))
    await handler.cancel(request.request_id, reason=InteractionCancelReason.TURN_ABORTED)

    assert response == ToolApprovalResponse(request_id="approval-1", decision=ToolApprovalDecision.ALLOW)
    assert handler.cancelled == [("approval-1", InteractionCancelReason.TURN_ABORTED)]
    assert request.turn_id == "turn-1"
    assert request.deadline_at == 2_000_000_000.0

    with pytest.raises(ExternalHarnessProtocolError, match="requires ToolApprovalResponse"):
        validate_interaction_response(
            request,
            UserInputResponse(request_id=request.request_id, status=InteractionResponseStatus.COMPLETED),
        )
    with pytest.raises(ExternalHarnessProtocolError, match="does not match"):
        validate_interaction_response(
            request,
            ToolApprovalResponse(request_id="wrong-id", decision=ToolApprovalDecision.DENY),
        )


def test_hook_context_uses_turn_identity() -> None:
    context = BeforeToolContext(
        member_name="member-a",
        session_id="session-1",
        turn_id="turn-1",
        call_id="call-1",
        tool_name="shell",
        arguments={"command": "pwd"},
    )

    assert context.turn_id == "turn-1"


@pytest.mark.asyncio
async def test_turn_events_is_finite_and_includes_terminal_event() -> None:
    harness = _Harness()
    receipt = await harness.send(ExternalHarnessInput(content="hello"))
    response = [event async for event in harness.turn_events(receipt.turn_id)]
    remaining = [event async for event in harness.events()]

    assert receipt == SendReceipt(message_id="message-1", turn_id="turn-1", accepted_mode=DeliveryMode.AUTO)
    assert len(response) == 5
    assert [event.sequence for event in response] == [2, 3, 4, 5, 6]
    assert isinstance(response[0].event, TurnLifecycleEvent)
    assert response[0].event.kind is TurnEventKind.STARTED
    assert isinstance(response[1].event, TurnLifecycleEvent)
    assert response[1].event.kind is TurnEventKind.PAUSED
    assert isinstance(response[2].event, TurnLifecycleEvent)
    assert response[2].event.kind is TurnEventKind.RESUMED
    assert isinstance(response[-1].event, TurnLifecycleEvent)
    assert response[-1].event.kind is TurnEventKind.FINISHED
    assert response[-1].event.result == TurnResult(status=TurnStatus.COMPLETED, final_output="done")
    assert remaining == []


@pytest.mark.parametrize(
    ("transport", "kwargs", "message"),
    [
        (McpTransport.STDIO, {}, "non-empty command"),
        (McpTransport.HTTP, {}, "requires url"),
        (McpTransport.IN_PROCESS, {}, "requires instance"),
    ],
)
def test_mcp_config_rejects_missing_transport_target(transport, kwargs, message) -> None:
    with pytest.raises(ValueError, match=message):
        McpServerConfig(name="team", transport=transport, **kwargs)


def test_mcp_config_rejects_multiple_transport_targets() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        McpServerConfig(
            name="team",
            transport=McpTransport.STDIO,
            command=("team-mcp",),
            url="https://example.invalid/mcp",
        )

    with pytest.raises(ValueError, match="headers are only valid"):
        McpServerConfig(
            name="team",
            transport=McpTransport.STDIO,
            command=("team-mcp",),
            headers={"Authorization": "redacted"},
        )


def test_event_envelope_carries_neutral_output_and_correlation() -> None:
    payload = OutputEvent(
        output_id="answer-1",
        kind=OutputKind.TEXT,
        content="done",
        operation=OutputOperation.DELTA,
        channel=OutputChannel.ANSWER,
        content_index=1,
    )

    event = HarnessEvent(
        sequence=1,
        timestamp=1.5,
        event=payload,
        team_session_id="team-session-1",
        member_agent_id="team-a_member-a",
        session_id="session-1",
        turn_id="turn-1",
        correlation_id="message-1",
        causation_ids=("message-1", "steer-1"),
    )

    assert event.event is payload
    assert event.turn_id == "turn-1"
    assert event.correlation_id == "message-1"
    assert event.causation_ids == ("message-1", "steer-1")
    assert payload.operation is OutputOperation.DELTA
    assert payload.content_index == 1

    usage = UsageUpdatedEvent(usage=TurnUsage(input_tokens=4), mode=UsageUpdateMode.DELTA)
    assert usage.mode is UsageUpdateMode.DELTA


def test_turn_result_preserves_messages_termination_and_exact_cost() -> None:
    block = ContentBlock(block_id="block-1", kind="text", content="partial")
    message = TurnMessage(message_id="message-1", role=MessageRole.ASSISTANT, content=(block,))
    completed = TurnResult(
        status=TurnStatus.COMPLETED,
        messages=(message,),
        final_output="partial",
        cost=MonetaryAmount(micros=125_000),
    )
    interrupted = TurnResult(
        status=TurnStatus.INTERRUPTED,
        termination=TurnTermination(kind=TurnTerminationKind.USER_ABORT, message="cancelled by user"),
    )

    assert completed.messages == (message,)
    assert completed.cost == MonetaryAmount(micros=125_000, currency="USD")
    assert interrupted.termination is not None

    with pytest.raises(ValueError, match="requires termination"):
        TurnResult(status=TurnStatus.INTERRUPTED)


def test_tool_decision_distinguishes_rewrite_ask_and_provider_policy() -> None:
    rewrite = ToolDecision(
        decision=ToolDecisionKind.REWRITE,
        updated_arguments={"command": "pwd"},
    )

    assert rewrite.updated_arguments == {"command": "pwd"}
    assert ToolDecision(decision=ToolDecisionKind.ASK).decision is ToolDecisionKind.ASK
    assert ToolDecision(decision=ToolDecisionKind.PROVIDER_POLICY).decision is ToolDecisionKind.PROVIDER_POLICY

    with pytest.raises(ValueError, match="requires updated_arguments"):
        ToolDecision(decision=ToolDecisionKind.REWRITE)


def test_turn_terminal_event_requires_matching_structured_result() -> None:
    result = TurnResult(status=TurnStatus.COMPLETED, final_output="done")
    payload = TurnLifecycleEvent(kind=TurnEventKind.FINISHED, result=result)

    event = HarnessEvent(
        sequence=2,
        timestamp=2.0,
        event=payload,
        team_session_id="team-session-1",
        member_agent_id="team-a_member-a",
        turn_id="turn-1",
    )

    assert payload.result is result
    assert event.event is payload

    with pytest.raises(ValueError, match="requires interrupted result"):
        TurnLifecycleEvent(kind=TurnEventKind.ABORTED, result=result)
    with pytest.raises(ValueError, match="non-terminal paused"):
        TurnLifecycleEvent(kind=TurnEventKind.PAUSED, result=result)
    with pytest.raises(ValueError, match="requires a result"):
        TurnLifecycleEvent(kind=TurnEventKind.FAILED)
    with pytest.raises(ValueError, match="requires turn_id"):
        HarnessEvent(
            sequence=3,
            timestamp=3.0,
            event=payload,
            team_session_id="team-session-1",
            member_agent_id="team-a_member-a",
        )


def test_failed_result_requires_structured_error() -> None:
    with pytest.raises(ValueError, match="requires error"):
        TurnResult(status=TurnStatus.FAILED)

    error = TurnError(message="provider failed", code="provider_error", retryable=True)
    result = TurnResult(status=TurnStatus.FAILED, error=error)

    assert result.error is error


def test_provider_event_preserves_namespaced_extension_payload() -> None:
    payload = ProviderEvent(
        provider="codex",
        event_type="thread.compacted",
        schema_version="1",
        payload={"thread_id": "thread-1"},
    )

    event = HarnessEvent(
        sequence=4,
        timestamp=4.0,
        event=payload,
        team_session_id="team-session-1",
        member_agent_id="team-a_member-a",
        session_id="thread-1",
    )

    assert event.event is payload


def test_protocol_json_values_are_validated_and_frozen() -> None:
    content = {"parts": ["one", {"value": 2}]}
    harness_input = ExternalHarnessInput(content=content, metadata={"source": "test"})
    content["parts"].append("mutated")

    assert harness_input.content == {"parts": ("one", {"value": 2})}
    with pytest.raises(TypeError):
        harness_input.metadata["source"] = "changed"  # type: ignore[index]
    with pytest.raises(ValueError, match="finite"):
        ExternalHarnessInput(content=float("nan"))
    with pytest.raises(TypeError, match="not JSON-compatible"):
        ExternalHarnessInput(content=object())  # type: ignore[arg-type]


def test_event_backpressure_retention_is_not_provider_selected() -> None:
    snapshot = OutputEvent(
        output_id="answer-1",
        kind=OutputKind.TEXT,
        content="current",
        operation=OutputOperation.SNAPSHOT,
    )
    delta = OutputEvent(
        output_id="answer-1",
        kind=OutputKind.TEXT,
        content="next",
        operation=OutputOperation.DELTA,
    )

    assert event_retention(snapshot) is EventRetention.COALESCIBLE
    assert event_retention(delta) is EventRetention.REQUIRED
    assert event_retention(DiagnosticEvent(level=DiagnosticLevel.DEBUG, message="trace")) is EventRetention.BEST_EFFORT
    assert EventBufferConfig(capacity=4).overflow is EventOverflowPolicy.COALESCE_OR_BLOCK
    with pytest.raises(ValueError, match="positive"):
        EventBufferConfig(capacity=0)


def test_event_json_codec_round_trips_and_preserves_unknown_events() -> None:
    event = HarnessEvent(
        sequence=7,
        timestamp=7.5,
        event=OutputEvent(
            output_id="answer-1",
            kind=OutputKind.STRUCTURED,
            content={"answer": [1, 2]},
            operation=OutputOperation.FINAL,
        ),
        team_session_id="team-session-1",
        member_agent_id="team-a_member-a",
        session_id="provider-session-1",
        turn_id="turn-1",
        correlation_id="trace-1",
        causation_ids=("message-1",),
    )

    wire = harness_event_to_dict(event)
    json.dumps(wire)
    assert harness_event_from_dict(wire) == event

    wire["event_type"] = "future_event"
    wire["payload"] = {"new_field": [1, 2, 3]}
    decoded = harness_event_from_dict(wire)

    assert isinstance(decoded.event, UnknownEvent)
    assert decoded.event.payload == {"new_field": (1, 2, 3)}
    assert harness_event_to_dict(decoded)["event_type"] == "future_event"


def test_event_scope_time_and_causation_are_validated() -> None:
    payload = DiagnosticEvent(level=DiagnosticLevel.INFO, message="ready")

    with pytest.raises(ValueError, match="finite Unix"):
        HarnessEvent(
            sequence=1,
            timestamp=float("inf"),
            event=payload,
            team_session_id="team-session-1",
            member_agent_id="member-a",
        )
    with pytest.raises(ValueError, match="duplicates"):
        HarnessEvent(
            sequence=1,
            timestamp=1.0,
            event=payload,
            team_session_id="team-session-1",
            member_agent_id="member-a",
            causation_ids=("message-1", "message-1"),
        )

# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Behavior tests for the protocol-to-DeepAgent input/output adapter."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from openjiuwen.core.common.constants.constant import INTERACTION
from openjiuwen.core.session.interaction.interactive_input import InteractiveInput
from openjiuwen.harness_protocol import (
    AbortMode,
    DeliveryMode,
    EventBufferConfig,
    HarnessCapability,
    HarnessCard,
    HarnessContext,
    HarnessEvent,
    HarnessInput,
    HarnessState,
    HostCapability,
    InteractionResponseStatus,
    ItemEventKind,
    ItemLifecycleEvent,
    OutputChannel,
    OutputEvent,
    OutputKind,
    OutputOperation,
    ProviderInteractionRequest,
    ProviderInteractionResponse,
    SendReceipt,
    ToolApprovalDecision,
    ToolApprovalRequest,
    UnsupportedHarnessCapabilityError,
    UserInputRequest,
)
from openjiuwen.harness_providers.io_adapter import INTERACTIVE_INPUT_KIND, HarnessIOAdapter
from tests.test_logger import logger


class _Cursor:
    _END = object()

    def __init__(self) -> None:
        self._queue: asyncio.Queue[Any] = asyncio.Queue()
        self.closed = 0

    def __aiter__(self) -> "_Cursor":
        return self

    async def __anext__(self) -> HarnessEvent:
        item = await self._queue.get()
        if item is self._END:
            raise StopAsyncIteration
        return item

    async def put(self, event: HarnessEvent) -> None:
        await self._queue.put(event)

    async def finish(self) -> None:
        await self._queue.put(self._END)

    async def aclose(self) -> None:
        self.closed += 1


class _FakeHarness:
    def __init__(self, *, capabilities: frozenset[HarnessCapability] = frozenset(), fail_start: bool = False) -> None:
        self._card = HarnessCard(name="fake", implementation_version="1", capabilities=capabilities)
        self.state = HarnessState.TERMINATED
        self.cursor = _Cursor()
        self.contexts: list[HarnessContext] = []
        self.sends: list[tuple[HarnessInput, DeliveryMode]] = []
        self.aborts: list[AbortMode] = []
        self.stops = 0
        self.sequence = 0
        self.fail_start = fail_start

    @property
    def card(self) -> HarnessCard:
        return self._card

    @property
    def provider_session_id(self) -> str:
        return "fake-session"

    @property
    def event_buffer_config(self) -> EventBufferConfig:
        return EventBufferConfig(capacity=8)

    async def start(self, context: HarnessContext) -> None:
        if self.fail_start:
            raise RuntimeError("no sdk")
        self.contexts.append(context)
        self.state = HarnessState.IDLE

    async def stop(self) -> None:
        self.stops += 1
        self.state = HarnessState.TERMINATED
        await self.cursor.finish()

    def events(self) -> _Cursor:
        return self.cursor

    def turn_events(self, turn_id: str | None = None) -> _Cursor:
        _ = turn_id
        return self.cursor

    async def send(self, content: HarnessInput, *, mode: DeliveryMode = DeliveryMode.AUTO) -> SendReceipt:
        self.sends.append((content, mode))
        return SendReceipt(message_id=f"m{len(self.sends)}", turn_id="turn-1", accepted_mode=mode)

    async def abort(self, *, mode: AbortMode = AbortMode.GRACEFUL) -> None:
        self.aborts.append(mode)

    async def pause(self) -> None:
        raise UnsupportedHarnessCapabilityError("no pause")

    async def resume(self, *, query: HarnessInput | None = None) -> None:
        _ = query

    async def export_checkpoint(self) -> None:
        return None

    async def emit(self, payload: Any, *, item_id: str | None = None) -> None:
        self.sequence += 1
        await self.cursor.put(
            HarnessEvent(
                sequence=self.sequence,
                timestamp=float(self.sequence),
                event=payload,
                host_session_id="host",
                agent_id="agent",
                turn_id="turn-1",
                item_id=item_id,
            )
        )


def _context() -> HarnessContext:
    return HarnessContext(agent_name="worker", agent_id="agent", host_session_id="host", system_prompt="")


async def _drain(adapter: HarnessIOAdapter) -> list[Any]:
    return [chunk async for chunk in adapter.outputs()]


@pytest.mark.asyncio
async def test_outputs_and_tool_items_project_to_deepagent_chunks() -> None:
    harness = _FakeHarness()
    adapter = HarnessIOAdapter(harness)
    await adapter.start(_context())
    await harness.emit(OutputEvent(output_id="a", kind=OutputKind.TEXT, content="Hel", operation=OutputOperation.DELTA))
    await harness.emit(OutputEvent(output_id="a", kind=OutputKind.TEXT, content="Hello", operation=OutputOperation.FINAL))
    await harness.emit(
        OutputEvent(
            output_id="r", kind=OutputKind.TEXT, content="think", operation=OutputOperation.FINAL, channel=OutputChannel.REASONING
        )
    )
    await harness.emit(
        ItemLifecycleEvent(kind=ItemEventKind.STARTED, item_type="tool", data={"name": "shell", "arguments": {"cmd": "ls"}}),
        item_id="call-1",
    )
    await harness.emit(
        ItemLifecycleEvent(kind=ItemEventKind.COMPLETED, item_type="tool", data={"tool_name": "shell", "result": "ok"}),
        item_id="call-1",
    )
    await adapter.stop()
    chunks = await _drain(adapter)
    assert [(chunk.type, chunk.index) for chunk in chunks] == [
        ("llm_output", 0),
        ("llm_output", 1),
        ("llm_reasoning", 2),
        ("tool_call", 3),
        ("tool_result", 4),
    ]
    assert [chunk.payload["content"] for chunk in chunks[:2]] == ["Hel", "lo"]
    assert chunks[3].payload == {"name": "shell", "arguments": '{"cmd":"ls"}', "tool_call_id": "call-1"}
    assert chunks[4].payload["result"] == "ok"
    assert harness.cursor.closed == 1
    started = harness.contexts[0]
    assert HostCapability.USER_INPUT in started.host_capabilities
    assert started.interactions is adapter


@pytest.mark.asyncio
async def test_user_input_request_round_trips_through_interactive_input() -> None:
    harness = _FakeHarness()
    adapter = HarnessIOAdapter(harness)
    await adapter.start(_context())
    request = UserInputRequest(request_id="ask-1", prompt="favorite color?", choices=("red", "teal"))
    pending = asyncio.create_task(adapter.handle(request))
    await asyncio.sleep(0.01)
    assert adapter.has_pending_interrupt()
    assert adapter.pending_interrupt_ids == ("ask-1",)
    interactive = InteractiveInput()
    interactive.update("ask-1", {"answers": {"favorite color?": "teal"}})
    assert adapter.is_pending_interrupt_resume_valid(interactive)
    assert await adapter.send(interactive) is None
    response = await pending
    assert response.status is InteractionResponseStatus.COMPLETED
    assert response.content["answers"]["favorite color?"] == "teal"
    assert harness.sends == []
    await adapter.stop()
    chunks = await _drain(adapter)
    assert chunks[0].type == INTERACTION
    assert chunks[0].payload.id == "ask-1"
    assert chunks[0].payload.value["choices"] == ["red", "teal"]
    logger.info("interaction chunk: %s", chunks[0].payload)


@pytest.mark.asyncio
async def test_unmatched_interactive_input_is_forwarded_to_the_provider() -> None:
    harness = _FakeHarness()
    adapter = HarnessIOAdapter(harness)
    await adapter.start(_context())
    interactive = InteractiveInput()
    interactive.update("unknown", "value")
    receipt = await adapter.send(interactive)
    assert receipt is not None
    sent, mode = harness.sends[0]
    assert mode is DeliveryMode.AUTO
    assert sent.metadata["kind"] == INTERACTIVE_INPUT_KIND
    assert sent.content["user_inputs"]["unknown"] == "value"
    await adapter.stop()


@pytest.mark.asyncio
async def test_tool_approval_is_auto_allowed_by_default_and_asked_when_disabled() -> None:
    harness = _FakeHarness()
    adapter = HarnessIOAdapter(harness)
    await adapter.start(_context())
    request = ToolApprovalRequest(request_id="approve-1", call_id="c1", tool_name="shell")
    response = await adapter.handle(request)
    assert response.decision is ToolApprovalDecision.ALLOW
    await adapter.stop()

    strict = HarnessIOAdapter(_FakeHarness(), auto_approve_tools=False)
    await strict.start(_context())
    pending = asyncio.create_task(strict.handle(request))
    await asyncio.sleep(0.01)
    interactive = InteractiveInput()
    interactive.update("approve-1", {"approved": False, "feedback": "not now"})
    await strict.send(interactive)
    denied = await pending
    assert denied.decision is ToolApprovalDecision.DENY
    assert denied.reason == "not now"
    await strict.stop()


@pytest.mark.asyncio
async def test_delivery_mode_follows_state_and_capabilities() -> None:
    steerable = _FakeHarness(capabilities=frozenset({HarnessCapability.STEER}))
    adapter = HarnessIOAdapter(steerable)
    await adapter.start(_context())
    assert adapter.delivery_mode(immediate=True) is DeliveryMode.AUTO
    steerable.state = HarnessState.RUNNING
    assert adapter.delivery_mode(immediate=True) is DeliveryMode.STEER
    assert adapter.delivery_mode(immediate=False) is DeliveryMode.FOLLOW_UP
    await adapter.send("text", immediate=True)
    assert steerable.sends[-1][1] is DeliveryMode.STEER
    await adapter.stop()

    plain = _FakeHarness()
    adapter = HarnessIOAdapter(plain)
    await adapter.start(_context())
    plain.state = HarnessState.RUNNING
    assert adapter.delivery_mode(immediate=True) is DeliveryMode.FOLLOW_UP
    await adapter.stop()


@pytest.mark.asyncio
async def test_abort_uses_the_declared_capability_or_stops() -> None:
    graceful_only = _FakeHarness(capabilities=frozenset({HarnessCapability.GRACEFUL_ABORT}))
    adapter = HarnessIOAdapter(graceful_only)
    await adapter.start(_context())
    await adapter.abort(immediate=True)
    assert graceful_only.aborts == [AbortMode.GRACEFUL]
    await adapter.stop()

    none = _FakeHarness()
    adapter = HarnessIOAdapter(none, stop_on_unsupported_force_abort=True)
    await adapter.start(_context())
    none.state = HarnessState.RUNNING
    await adapter.abort(immediate=True)
    assert none.stops == 1

    running = _FakeHarness()
    adapter = HarnessIOAdapter(running)
    await adapter.start(_context())
    running.state = HarnessState.RUNNING
    with pytest.raises(UnsupportedHarnessCapabilityError):
        await adapter.abort(immediate=False)
    with pytest.raises(UnsupportedHarnessCapabilityError):
        await adapter.pause()
    await adapter.stop()


@pytest.mark.asyncio
async def test_failed_start_stops_the_harness_and_propagates() -> None:
    harness = _FakeHarness(fail_start=True)
    adapter = HarnessIOAdapter(harness)
    with pytest.raises(RuntimeError, match="no sdk"):
        await adapter.start(_context())
    assert harness.stops == 1
    await adapter.stop()


@pytest.mark.asyncio
async def test_provider_interaction_requests_are_declined_without_a_handler() -> None:
    harness = _FakeHarness()
    adapter = HarnessIOAdapter(harness)
    await adapter.start(_context())
    assert HostCapability.PROVIDER_INTERACTION not in harness.contexts[0].host_capabilities
    request = ProviderInteractionRequest(
        request_id="ext-1", provider="fake", request_type="auth_fallback", schema_version="1", payload={}
    )
    response = await adapter.handle(request)
    assert isinstance(response, ProviderInteractionResponse)
    assert response.status is InteractionResponseStatus.DECLINED
    await adapter.stop()


@pytest.mark.asyncio
async def test_provider_interaction_requests_route_to_the_bound_handler() -> None:
    harness = _FakeHarness()
    seen: list[ProviderInteractionRequest] = []

    async def _handler(request: ProviderInteractionRequest) -> ProviderInteractionResponse:
        seen.append(request)
        return ProviderInteractionResponse(request_id=request.request_id, status=InteractionResponseStatus.COMPLETED)

    adapter = HarnessIOAdapter(harness, provider_interaction_handler=_handler)
    await adapter.start(_context())
    assert HostCapability.PROVIDER_INTERACTION in harness.contexts[0].host_capabilities
    request = ProviderInteractionRequest(
        request_id="ext-2", provider="fake", request_type="auth_fallback", schema_version="1", payload={"model": "m"}
    )
    response = await adapter.handle(request)
    logger.info("provider interaction response: %s", response)
    assert response.status is InteractionResponseStatus.COMPLETED
    assert seen[0].payload == {"model": "m"}
    await adapter.stop()

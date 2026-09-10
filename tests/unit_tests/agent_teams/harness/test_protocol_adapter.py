# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Protocol controls against NativeHarness's real supervisor/task-loop kernel."""

import asyncio
import json

import pytest
import pytest_asyncio

from openjiuwen.agent_teams.harness import NativeHarness, create_native_harness_protocol
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.schema.deep_agent_spec import DeepAgentSpec
from openjiuwen.harness.schema.extension_spec import AgentTemplateSpec
from openjiuwen.harness_protocol import (
    AbortMode, HarnessContext, HarnessInput, HarnessProtocol, HarnessState,
    HarnessStateError, TurnEventKind, TurnLifecycleEvent, ResumePolicy,
    UnsupportedHarnessCapabilityError,
)
from tests.unit_tests.agent_teams.harness.fixtures import (
    make_spec, start_harness, wait_invoke_running, wait_tool_running,
)


@pytest_asyncio.fixture
async def adapter(monkeypatch):
    monkeypatch.setattr(DeepAgentSpec, "resolve_parts", lambda self, context=None: make_spec(self.card).resolve_parts(context))
    await Runner.start()
    template = AgentTemplateSpec(agent_card=AgentCard(id="protocol-native", name="protocol-native"))
    result = create_native_harness_protocol(template)
    await result.start(HarnessContext(system_prompt="", agent_name="native", agent_id="native", host_session_id="protocol-test"))
    try:
        yield result
    finally:
        await result.stop()
        await Runner.stop()


async def collect(adapter, turn_id):
    cursor = adapter.turn_events(turn_id)
    try:
        return [event async for event in cursor]
    finally:
        await cursor.aclose()


def kinds(events):
    return [e.event.kind for e in events if isinstance(e.event, TurnLifecycleEvent)]


@pytest.mark.asyncio
async def test_followups_have_distinct_turns_and_terminal_output(adapter):
    assert isinstance(adapter, HarnessProtocol)
    assert isinstance(adapter.native_harness, NativeHarness)
    fake = await start_harness(adapter.native_harness, answer_output="final-answer")
    first = await adapter.send(HarnessInput(content="first"))
    second = await adapter.send(HarnessInput(content="second"))
    one = await asyncio.wait_for(collect(adapter, first.turn_id), 4)
    two = await asyncio.wait_for(collect(adapter, second.turn_id), 4)
    assert first.turn_id != second.turn_id
    assert kinds(one) == kinds(two) == [TurnEventKind.STARTED, TurnEventKind.FINISHED]
    assert one[-1].event.result.final_output == two[-1].event.result.final_output == "final-answer"
    assert len(fake.invocations) == 2


@pytest.mark.asyncio
async def test_pause_resume_keeps_one_turn_and_queued_input(adapter):
    fake = await start_harness(adapter.native_harness, sleep_seconds=5, answer_output="resumed")
    receipt = await adapter.send(HarnessInput(content="first"))
    consumer = asyncio.create_task(collect(adapter, receipt.turn_id))
    await wait_invoke_running(fake)
    await asyncio.wait_for(adapter.pause(), 3)
    assert adapter.state is HarnessState.PAUSED
    assert not consumer.done()
    queued = await adapter.send(HarnessInput(content="second"))
    assert not consumer.done()
    fake.sleep_seconds = 0
    await adapter.resume()
    events = await asyncio.wait_for(consumer, 4)
    assert kinds(events) == [TurnEventKind.STARTED, TurnEventKind.PAUSED, TurnEventKind.RESUMED, TurnEventKind.FINISHED]
    assert all(e.turn_id == receipt.turn_id for e in events if isinstance(e.event, TurnLifecycleEvent))
    assert (await asyncio.wait_for(collect(adapter, queued.turn_id), 4))[-1].event.kind is TurnEventKind.FINISHED


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [AbortMode.GRACEFUL, AbortMode.FORCE])
async def test_abort_maps_to_native_control(adapter, mode):
    fake = await start_harness(adapter.native_harness, iterations=2, emit_tools=True, tool_sleep_seconds=0.15)
    receipt = await adapter.send(HarnessInput(content="work"))
    consumer = asyncio.create_task(collect(adapter, receipt.turn_id))
    await wait_tool_running(fake)
    await adapter.abort(mode=mode)
    events = await asyncio.wait_for(consumer, 4)
    assert kinds(events) == [TurnEventKind.STARTED, TurnEventKind.ABORTED]
    if mode is AbortMode.GRACEFUL:
        assert fake.completed_tools == 1
        assert fake.cancelled_count == 0


@pytest.mark.asyncio
async def test_stop_while_paused_terminates_turn(adapter):
    fake = await start_harness(adapter.native_harness, sleep_seconds=5)
    receipt = await adapter.send(HarnessInput(content="work"))
    consumer = asyncio.create_task(collect(adapter, receipt.turn_id))
    await wait_invoke_running(fake)
    await adapter.pause()
    await asyncio.wait_for(adapter.stop(), 4)
    assert kinds(await consumer)[-1] is TurnEventKind.ABORTED
    assert adapter.state is HarnessState.TERMINATED


def test_manifest_path_reuses_template_snapshot(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"package_type": "agent_template", "name": "expert", "description": "Expert"}))
    adapter = create_native_harness_protocol(path, agent_spec=DeepAgentSpec(max_iterations=7))
    assert adapter._spec.card.name == "expert"
    assert adapter._spec.agent_template_spec["agent_card"]["name"] == "expert"
    assert adapter._spec.max_iterations == 7
    assert adapter.native_harness is None


@pytest.mark.asyncio
async def test_cold_protocol_resume_is_explicitly_unsupported():
    adapter = create_native_harness_protocol(AgentTemplateSpec(agent_card=AgentCard(id="a", name="a")))
    with pytest.raises(UnsupportedHarnessCapabilityError):
        await adapter.start(HarnessContext(system_prompt="", agent_name="a", agent_id="a", host_session_id="s", resume_policy=ResumePolicy.REQUIRE_RESUME))
    with pytest.raises(HarnessStateError):
        await adapter.resume(query=HarnessInput(content="restore"))


@pytest.mark.asyncio
async def test_abort_before_native_dispatch_does_not_hang(adapter):
    receipt = await adapter.send(HarnessInput(content="work"))
    consumer = asyncio.create_task(collect(adapter, receipt.turn_id))
    # Let the public supervisor accept ownership, without waiting for a model.
    while adapter.active_turn is None:
        await asyncio.sleep(0)
    await asyncio.wait_for(adapter.abort(mode=AbortMode.FORCE), 3)
    assert kinds(await asyncio.wait_for(consumer, 3))[-1] is TurnEventKind.ABORTED


@pytest.mark.asyncio
async def test_outputs_and_interaction_share_native_session(adapter):
    from openjiuwen.core.common.constants.constant import INTERACTION
    from openjiuwen.core.session.stream import OutputSchema
    from openjiuwen.core.session.interaction.interaction import InteractionOutput
    from openjiuwen.harness_protocol import HostCapability, UserInputResponse, InteractionResponseStatus, OutputEvent, ItemLifecycleEvent
    from dataclasses import replace

    class Handler:
        def __init__(self):
            self.requests = []

        async def handle(self, request):
            self.requests.append(request)
            return UserInputResponse(request_id=request.request_id, status=InteractionResponseStatus.COMPLETED, content="teal")

        async def cancel(self, request_id, *, reason):
            pass

    handler = Handler()
    adapter._context = replace(adapter.context, interactions=handler, host_capabilities=frozenset({HostCapability.USER_INPUT}))
    fake = await start_harness(adapter.native_harness, answer_output="teal")
    original = fake.write_invoke_result_to_stream
    calls = 0

    async def output(result, session):
        nonlocal calls
        calls += 1
        if calls == 1:
            for kind, payload in [
                ("llm_reasoning", {"content": "consider"}),
                ("llm_output", {"content": "question"}),
                ("tool_call", {"tool_call_id": "t", "tool_name": "read", "tool_args": {}}),
                ("tool_result", {"tool_call_id": "t", "tool_name": "read", "tool_result": "ok"}),
            ]:
                await session.write_stream(OutputSchema(type=kind, index=0, payload=payload))
            await session.write_stream(OutputSchema(type=INTERACTION, index=0, payload=InteractionOutput(id="ask", value={"questions": [{"question": "Color?"}]})))
        else:
            await original(result, session)

    fake.write_invoke_result_to_stream = output
    receipt = await adapter.send(HarnessInput(content="ask"))
    events = await asyncio.wait_for(collect(adapter, receipt.turn_id), 4)
    assert kinds(events) == [TurnEventKind.STARTED, TurnEventKind.FINISHED]
    assert len(handler.requests) == 1
    assert handler.requests[0].turn_id == receipt.turn_id
    assert events[-1].event.result.final_output == "teal"
    assert len([e for e in events if isinstance(e.event, ItemLifecycleEvent)]) == 2
    assert {e.event.channel.value for e in events if isinstance(e.event, OutputEvent)} == {"answer", "reasoning"}


@pytest.mark.asyncio
async def test_native_prepare_loaded_manifest_and_rebuilds_each_cycle(adapter):
    old = adapter.native_harness
    assert old._active_agent_template[1] == "protocol-native"
    context = adapter.context
    await adapter.stop()
    await adapter.start(context)
    assert adapter.native_harness is not old
    assert adapter.native_harness._active_agent_template[1] == "protocol-native"


@pytest.mark.asyncio
async def test_single_consumer_lease_is_enforced(adapter):
    cursor = adapter.events()
    with pytest.raises(HarnessStateError):
        adapter.turn_events()
    await cursor.aclose()
    await adapter.events().aclose()


@pytest.mark.asyncio
async def test_pause_during_tool_execution_preserves_tool_completion(adapter):
    fake = await start_harness(adapter.native_harness, iterations=2, emit_tools=True, tool_sleep_seconds=0.15)
    receipt = await adapter.send(HarnessInput(content="work"))
    consumer = asyncio.create_task(collect(adapter, receipt.turn_id))
    await wait_tool_running(fake)
    await asyncio.wait_for(adapter.pause(), 3)
    assert fake.completed_tools == 1
    assert fake.cancelled_count == 0
    assert adapter.state is HarnessState.PAUSED
    await adapter.abort()
    assert kinds(await asyncio.wait_for(consumer, 3))[-1] is TurnEventKind.ABORTED

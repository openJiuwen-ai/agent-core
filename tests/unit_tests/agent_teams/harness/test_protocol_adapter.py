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
    UnsupportedHarnessCapabilityError, HarnessProtocolError, HarnessCheckpoint,
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
async def test_cold_protocol_resume_requires_checkpoint():
    adapter = create_native_harness_protocol(AgentTemplateSpec(agent_card=AgentCard(id="a", name="a")))
    with pytest.raises(HarnessProtocolError):
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


@pytest.mark.asyncio
async def test_checkpoint_roundtrip_restores_context_and_paused_turn(adapter, monkeypatch):
    from dataclasses import replace
    from openjiuwen.core.foundation.llm import AssistantMessage, UserMessage
    from openjiuwen.harness_providers.jsonsafe import to_json_safe
    from tests.unit_tests.agent_teams.harness.fixtures import MockContextEngine

    async def save_contexts(engine, session):
        context = engine.get_context(session.get_session_id())
        return {"default_context_id": {"messages": context.get_messages(), "offload_messages": {}}}

    monkeypatch.setattr(MockContextEngine, "save_contexts", save_contexts, raising=False)
    fake = await start_harness(adapter.native_harness, sleep_seconds=5)
    context = adapter.context
    receipt = await adapter.send(HarnessInput(content="original task"))
    consumer = asyncio.create_task(collect(adapter, receipt.turn_id))
    await wait_invoke_running(fake)
    await adapter.pause()
    # Actual context codec must retain concrete roles and assistant metadata.
    saved_context = fake.context_engine.get_context(adapter.provider_session_id)
    saved_context.set_messages([UserMessage(content="original task"), AssistantMessage(content="retained boundary")])
    queued = await adapter.send(HarnessInput(content="later"))
    checkpoint = await adapter.export_checkpoint()
    wire = json.loads(json.dumps(to_json_safe(checkpoint)))
    checkpoint = HarnessCheckpoint(**wire)
    await adapter.stop()
    await consumer
    # Construct a distinct NativeHarness/context engine from the JSON envelope.
    rebuilt = create_native_harness_protocol(AgentTemplateSpec(agent_card=AgentCard(id="protocol-native", name="protocol-native")))
    await rebuilt.start(replace(context, resume_policy=ResumePolicy.REQUIRE_RESUME, checkpoint=checkpoint))
    try:
        restored = rebuilt._agent_session.get_state("context")["default_context_id"]["messages"]
        assert isinstance(restored[1], AssistantMessage)
        assert [m.content for m in restored] == ["original task", "retained boundary"]
        real_context = await rebuilt.native_harness.react_agent.context_engine.create_context(session=rebuilt._agent_session)
        assert [m.content for m in real_context.get_messages(with_history=True)] == ["original task", "retained boundary"]
        resumed_fake = await start_harness(rebuilt.native_harness, answer_output="done")
        # FakeReact does not implement ReActAgent._init_context; emulate that
        # normal lazy context read from the reconstructed session.
        resumed_fake.context_engine.get_context(rebuilt.provider_session_id).set_messages(restored)
        with pytest.raises(HarnessStateError):
            await rebuilt.send(HarnessInput(content="must resume first"))
        await rebuilt.resume()
        events = await asyncio.wait_for(collect(rebuilt, receipt.turn_id), 4)
        assert kinds(events) == [TurnEventKind.STARTED, TurnEventKind.PAUSED, TurnEventKind.RESUMED, TurnEventKind.FINISHED]
        assert resumed_fake.invocations[0]["_resume_continuation"] is True
        assert resumed_fake.invocations[0]["query"] == "original task"
        messages = resumed_fake.context_engine.get_context(rebuilt.provider_session_id).get_messages()
        assert [m.content for m in messages].count("original task") == 1
        assert (await asyncio.wait_for(collect(rebuilt, queued.turn_id), 4))[-1].event.kind is TurnEventKind.FINISHED
        assert (await rebuilt.export_checkpoint()).sequence > checkpoint.sequence
    finally:
        await rebuilt.stop()


@pytest.mark.asyncio
async def test_checkpoint_rejects_running_state_and_foreign_scope(adapter, monkeypatch):
    from dataclasses import replace
    from tests.unit_tests.agent_teams.harness.fixtures import MockContextEngine

    async def save_contexts(engine, session):
        return {}
    monkeypatch.setattr(MockContextEngine, "save_contexts", save_contexts, raising=False)
    fake = await start_harness(adapter.native_harness, sleep_seconds=5)
    receipt = await adapter.send(HarnessInput(content="task"))
    consumer = asyncio.create_task(collect(adapter, receipt.turn_id))
    await wait_invoke_running(fake)
    with pytest.raises(HarnessStateError):
        await adapter.export_checkpoint()
    await adapter.pause()
    checkpoint = await adapter.export_checkpoint()
    restored = create_native_harness_protocol(AgentTemplateSpec(agent_card=AgentCard(id="protocol-native", name="protocol-native")))
    for invalid in [replace(checkpoint, provider="other"), replace(checkpoint, schema_version="unknown"), replace(checkpoint, agent_id="other")]:
        with pytest.raises(HarnessProtocolError):
            await restored.start(replace(adapter.context, checkpoint=invalid, resume_policy=ResumePolicy.REQUIRE_RESUME))
    await adapter.stop()
    await consumer


@pytest.mark.asyncio
async def test_idle_checkpoint_uses_real_context_codec_and_sink(adapter):
    from dataclasses import replace
    from openjiuwen.core.foundation.llm import UserMessage, AssistantMessage, ToolMessage, ToolCall
    from openjiuwen.harness_protocol import CheckpointSaveReceipt, CheckpointReason

    saved = []
    class Sink:
        async def save(self, checkpoint, *, reason, expected_storage_revision=None):
            saved.append((checkpoint, reason, expected_storage_revision))
            return CheckpointSaveReceipt(checkpoint_id=checkpoint.checkpoint_id, sequence=checkpoint.sequence, storage_revision=str(checkpoint.sequence))

    adapter._context = replace(adapter.context, checkpoint_sink=Sink())
    native = adapter.native_harness
    context = await native.react_agent.context_engine.create_context(session=adapter._agent_session)
    context.set_messages([
        UserMessage(content="keep this"),
        AssistantMessage(content="", tool_calls=[ToolCall(id="call-1", name="read", arguments='{"path":"x"}', type="function")]),
        ToolMessage(content="result", tool_call_id="call-1"),
    ])
    native.loop_coordinator.increment_iteration()
    checkpoint = await adapter.export_checkpoint()
    assert saved[0][1] is CheckpointReason.TURN_COMPLETED
    assert checkpoint.data["deepagent"]["stop_condition_state"]["iteration"] == 1
    again = await adapter.export_checkpoint()
    assert saved[-1][2] == str(checkpoint.sequence)
    assert again.sequence > checkpoint.sequence
    original_context = adapter.context
    await adapter.stop()
    rebuilt = create_native_harness_protocol(AgentTemplateSpec(agent_card=AgentCard(id="protocol-native", name="protocol-native")))
    await rebuilt.start(replace(original_context, checkpoint=again, resume_policy=ResumePolicy.REQUIRE_RESUME))
    try:
        restored = await rebuilt.native_harness.react_agent.context_engine.create_context(session=rebuilt._agent_session)
        messages = restored.get_messages(with_history=True)
        assert isinstance(messages[1], AssistantMessage)
        assert messages[1].tool_calls[0].id == "call-1"
        assert isinstance(messages[2], ToolMessage)
        assert messages[2].tool_call_id == "call-1"
        assert rebuilt.native_harness.loop_coordinator.get_state()["iteration"] == 1
        assert rebuilt.state is HarnessState.IDLE
    finally:
        await rebuilt.stop()


@pytest.mark.asyncio
async def test_new_policy_ignores_checkpoint_and_query_mismatch_is_rejected(adapter, monkeypatch):
    from dataclasses import replace
    from tests.unit_tests.agent_teams.harness.fixtures import MockContextEngine

    async def save_contexts(engine, session):
        return {"default_context_id": {"messages": engine.get_context(session.get_session_id()).get_messages()}}
    monkeypatch.setattr(MockContextEngine, "save_contexts", save_contexts, raising=False)
    fake = await start_harness(adapter.native_harness, sleep_seconds=5)
    context = adapter.context
    receipt = await adapter.send(HarnessInput(content="saved query"))
    consumer = asyncio.create_task(collect(adapter, receipt.turn_id))
    await wait_invoke_running(fake)
    await adapter.pause()
    checkpoint = await adapter.export_checkpoint()
    await adapter.stop()
    await consumer
    for policy in [ResumePolicy.REQUIRE_RESUME, ResumePolicy.NEW]:
        rebuilt = create_native_harness_protocol(AgentTemplateSpec(agent_card=AgentCard(id="protocol-native", name="protocol-native")))
        await rebuilt.start(replace(context, checkpoint=checkpoint, resume_policy=policy))
        try:
            if policy is ResumePolicy.REQUIRE_RESUME:
                with pytest.raises(HarnessStateError, match="does not match"):
                    await rebuilt.resume(query=HarnessInput(content="another query"))
                assert rebuilt._cold_restore is not None
            else:
                assert rebuilt._cold_restore is None
                assert not rebuilt._agent_session.get_state("context")
        finally:
            await rebuilt.stop()

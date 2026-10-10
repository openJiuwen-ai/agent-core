# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Persistent native-call isolation, with real DeepAgent/Session and local models."""

import asyncio
from dataclasses import replace
from importlib import import_module

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.foundation.llm.schema.message import AssistantMessage
from openjiuwen.core.foundation.llm.schema.message_chunk import AssistantMessageChunk
from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall
from openjiuwen.core.foundation.store import InMemoryKVStore
from openjiuwen.core.foundation.tool import Tool, ToolCard
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent import AgentCard, create_agent_session
from openjiuwen.harness.schema.deep_agent_spec import _TOOL_PROVIDER_REGISTRY, BuiltinToolSpec, ModelSpec

pytestmark = pytest.mark.level0


class LocalModel:
    model_client_config = None
    model_config = None

    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()
        self.queries = []
        self.failure = False
        self.tool_requested = False
        self.stream_tasks = []
        self.stream_waiting = asyncio.Event()
        self.cancel_seen = asyncio.Event()
        self.cleanup_release = asyncio.Event()
        self.cleanup_release.set()

    async def invoke(self, messages, **kwargs):
        self.queries.append([message.content for message in messages])
        self.started.set()
        await self.release.wait()
        if self.failure:
            raise RuntimeError("local model failure")
        if any(message.content == "run-tool" for message in messages) and not self.tool_requested:
            self.tool_requested = True
            return AssistantMessage(
                tool_calls=[ToolCall(id="call-1", type="function", name="remember", arguments='{"value":"private"}')]
            )
        return AssistantMessage(content="local answer", finish_reason="stop")

    async def stream(self, messages, **kwargs):
        self.queries.append([message.content for message in messages])
        self.stream_tasks.append(asyncio.current_task())
        self.started.set()
        try:
            yield AssistantMessageChunk(content="local ")
            self.stream_waiting.set()
            await self.release.wait()
            if self.failure:
                raise RuntimeError("local stream failure")
            yield AssistantMessageChunk(content="answer", finish_reason="stop")
        except asyncio.CancelledError:
            self.cancel_seen.set()
            await self.cleanup_release.wait()
            raise


class NoHostCalls:
    async def record(self, *args):
        raise AssertionError("binding does not record events or call the budget")

    read_execution_log = admit = report = record


@pytest.fixture
def built(monkeypatch):
    api = import_module("openjiuwen.harness.persistent_agent")
    pairs = []

    def build(session_id="session-a", tools=None):
        model = LocalModel()
        monkeypatch.setattr(ModelSpec, "build", lambda self: model)
        spec = api.PersistentAgentSpec(
            card=AgentCard(id="same-business-agent", name="test"),
            model=ModelSpec(
                model_client_config={
                    "client_provider": "OpenAI",
                    "api_key": "unused",
                    "api_base": "https://example.invalid/v1",
                }
            ),
            enable_sys_operation=False,
            enable_task_loop=False,
            enable_security_rail=False,
            enable_tool_resilience_rail=False,
            auto_create_workspace=False,
            tools=tools,
        )
        execution = api.ExecutionContext(
            namespace_id="tenant",
            agent_id=spec.card.id,
            session_id=session_id,
            request_id="request",
            execution_id="execution-" + session_id,
            config_version="v1",
            config_fingerprint=spec.fingerprint(),
            source_ref="source:message-1",
            principal_ref="user",
            startup_scope=frozenset({"read"}),
            source_scope=frozenset({"read"}),
            policy_revision="p1",
            capability_snapshot=spec.enabled_features,
        )
        host = NoHostCalls()
        ctx = api.PersistentBuildContext(
            execution=execution,
            session=create_agent_session(session_id=session_id, card=spec.card),
            ports=api.PersistentPorts(commit=host, budget=host, window_backend=InMemoryKVStore()),
        )
        agent = api.create_persistent_agent(spec, context=ctx)
        pairs.append(agent)
        return agent, ctx, model

    yield build
    for agent in pairs:
        agent.ability_manager.teardown_tools()


def runtime():
    return import_module("openjiuwen.harness.persistent_agent.persistent_agent.execution.binding")


@pytest.mark.asyncio
async def test_bound_invoke_runs_real_native_agent(built):
    agent, ctx, model = built()
    result = await runtime().invoke_bound(agent, {"query": "only-a"}, context=ctx)
    assert result["output"] == "local answer"
    assert any("only-a" in query for query in model.queries)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mismatch", ["session", "same-id-session", "execution", "request", "config-version", "ports", "owner"]
)
async def test_mismatch_is_rejected_before_model_or_state_change(built, mismatch):
    agent, ctx, model = built()
    original = ctx.session
    if mismatch in ("session", "same-id-session"):
        ctx.session = create_agent_session(session_id="other" if mismatch == "session" else "session-a")
    elif mismatch == "execution":
        ctx.execution = replace(ctx.execution, execution_id="other")
    elif mismatch == "request":
        ctx.execution = replace(ctx.execution, request_id="other-request")
    elif mismatch == "config-version":
        ctx.execution = replace(ctx.execution, config_version="v2")
    elif mismatch == "ports":
        ctx.ports = None
    else:
        ctx.tool_owner_id = "other"
    with pytest.raises(BaseError):
        await runtime().invoke_bound(agent, {"query": "must not run"}, context=ctx)
    assert model.queries == []
    assert original.get_state("deepagent") is None


@pytest.mark.asyncio
async def test_same_instance_rejects_overlap_without_interrupting_first(built):
    agent, ctx, model = built()
    model.release.clear()
    running = asyncio.create_task(runtime().invoke_bound(agent, {"query": "first"}, context=ctx))
    try:
        await asyncio.wait_for(model.started.wait(), 5)
        with pytest.raises(BaseError):
            await runtime().invoke_bound(agent, {"query": "second"}, context=ctx)
        model.release.set()
        assert (await running)["output"] == "local answer"
        assert not any("second" in query for query in model.queries)
    finally:
        model.release.set()
        await asyncio.gather(running, return_exceptions=True)


@pytest.mark.asyncio
async def test_parallel_sessions_keep_messages_and_state_separate(built):
    first, one, model_a = built("session-a")
    second, two, model_b = built("session-b")
    await asyncio.gather(
        runtime().invoke_bound(first, {"query": "secret-a"}, context=one),
        runtime().invoke_bound(second, {"query": "secret-b"}, context=two),
    )
    assert not any("secret-b" in query for query in model_a.queries)
    assert not any("secret-a" in query for query in model_b.queries)
    first.load_state(one.session).pending_follow_ups.append("private-a")
    assert second.load_state(two.session).pending_follow_ups == []
    assert one.tool_owner_id != two.tool_owner_id


@pytest.mark.asyncio
async def test_abort_is_scoped_and_stopped_binding_cannot_restart(built):
    first, one, model_a = built("session-a")
    second, two, model_b = built("session-b")
    model_a.release.clear()
    model_b.release.clear()
    tasks = [
        asyncio.create_task(runtime().invoke_bound(first, {"query": "a"}, context=one)),
        asyncio.create_task(runtime().invoke_bound(second, {"query": "b"}, context=two)),
    ]
    try:
        await asyncio.wait_for(asyncio.gather(model_a.started.wait(), model_b.started.wait()), 5)
        await runtime().abort_bound(first, context=one)
        with pytest.raises(asyncio.CancelledError):
            await tasks[0]
        assert not tasks[1].done()
        model_b.release.set()
        assert (await tasks[1])["output"] == "local answer"
        with pytest.raises(BaseError):
            await runtime().invoke_bound(first, {"query": "restart"}, context=one)
    finally:
        model_a.release.set()
        model_b.release.set()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["config", "model", "prompt", "card", "owner", "stream-lifecycle"])
async def test_runtime_config_drift_rejected_before_native_call(built, change):
    agent, ctx, model = built()
    if change == "config":
        agent.configure(replace(agent.deep_config))
    elif change == "model":
        agent.deep_config.model = LocalModel()
    elif change == "prompt":
        agent.deep_config.system_prompt = "changed"
    elif change == "card":
        agent.card.id = "other"
    elif change == "stream-lifecycle":
        agent.deep_config.stream_task_lifecycle = None
    else:
        agent.deep_config.tool_owner_id = "other"
    with pytest.raises(BaseError):
        await runtime().invoke_bound(agent, {"query": "must not run"}, context=ctx)
    assert model.queries == []


@pytest.mark.asyncio
async def test_close_does_not_delete_session_state_or_allow_reuse(built):
    agent, ctx, _ = built()
    ctx.session.update_state({"durable-test": {"keep": True}})
    await runtime().close_bound(agent, context=ctx)
    await runtime().close_bound(agent, context=ctx)
    assert ctx.session.get_state("durable-test") == {"keep": True}
    with pytest.raises(BaseError):
        await runtime().invoke_bound(agent, {"query": "closed"}, context=ctx)


@pytest.mark.asyncio
async def test_close_rejects_active_call(built):
    agent, ctx, model = built()
    model.release.clear()
    task = asyncio.create_task(runtime().invoke_bound(agent, {"query": "active"}, context=ctx))
    try:
        await asyncio.wait_for(model.started.wait(), 5)
        with pytest.raises(BaseError):
            await runtime().close_bound(agent, context=ctx)
        model.release.set()
        await task
        await runtime().close_bound(agent, context=ctx)
    finally:
        model.release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_native_failure_releases_occupancy_for_next_call(built):
    agent, ctx, model = built()
    model.failure = True
    with pytest.raises(Exception):
        await runtime().invoke_bound(agent, {"query": "fails"}, context=ctx)
    model.failure = False
    result = await runtime().invoke_bound(agent, {"query": "next"}, context=ctx)
    assert result["output"] == "local answer"


@pytest.mark.asyncio
async def test_real_native_stream_completes_with_final_answer(built):
    agent, ctx, model = built()
    chunks = [chunk async for chunk in runtime().stream_bound(agent, {"query": "stream"}, context=ctx)]
    answers = [chunk.payload for chunk in chunks if getattr(chunk, "type", None) == "answer"]
    assert len(answers) == 1
    assert answers[0]["output"] == "local answer"
    assert model.stream_tasks and all(task.done() for task in model.stream_tasks)
    assert ctx.binding.runtime.active_task is None


@pytest.mark.asyncio
@pytest.mark.parametrize("callback_mode", ["default", "once", "generator"])
async def test_early_stream_close_waits_for_native_producer_exit(built, callback_mode):
    agent, ctx, model = built()
    if callback_mode == "once":
        agent.stream = Runner.callback_framework.emit_after("binding.stream.test", stream_mode="once")(agent.stream)
    elif callback_mode == "generator":
        agent.stream = Runner.callback_framework.transform_io(output_mode="generator")(agent.stream)
    model.release.clear()
    stream = runtime().stream_bound(agent, {"query": "early-close"}, context=ctx)
    try:
        while True:
            chunk = await anext(stream)
            if getattr(chunk, "type", None) == "llm_output":
                break
        await asyncio.wait_for(model.stream_waiting.wait(), 5)
        assert not model.stream_tasks[0].done()
        await stream.aclose()
        assert all(task.done() for task in model.stream_tasks), "native producer still alive after close"
        assert model.cancel_seen.is_set()
        assert ctx.binding.runtime.active_task is None
        await runtime().close_bound(agent, context=ctx)
    finally:
        model.release.set()
        await stream.aclose()


@pytest.mark.asyncio
async def test_stream_abort_keeps_occupancy_until_cleanup_despite_repeated_cancel(built, monkeypatch):
    agent, ctx, model = built()
    model.release.clear()
    model.cleanup_release.clear()
    commits = []
    native_commit = ctx.session.commit

    async def record_commit(*args, **kwargs):
        commits.append(ctx.binding.runtime.closed)
        return await native_commit(*args, **kwargs)

    monkeypatch.setattr(ctx.session, "commit", record_commit)

    async def consume():
        async for _ in runtime().stream_bound(agent, {"query": "cancel-me"}, context=ctx):
            pass

    consumer = asyncio.create_task(consume())
    stopper = None
    try:
        await asyncio.wait_for(model.started.wait(), 5)
        stopper = asyncio.create_task(runtime().abort_bound(agent, context=ctx))
        await asyncio.wait_for(model.cancel_seen.wait(), 5)
        consumer.cancel()
        await asyncio.sleep(0)
        assert not consumer.done()
        assert ctx.binding.runtime.active_task is consumer
        with pytest.raises(BaseError):
            await runtime().close_bound(agent, context=ctx)
        model.cleanup_release.set()
        await asyncio.wait_for(stopper, 5)
        with pytest.raises(asyncio.CancelledError):
            await consumer
        assert all(task.done() for task in model.stream_tasks)
        assert commits and not any(commits)
        await runtime().close_bound(agent, context=ctx)
        commit_count = len(commits)
        model.release.set()
        await asyncio.sleep(0)
        assert len(commits) == commit_count
    finally:
        model.release.set()
        model.cleanup_release.set()
        if not consumer.done():
            consumer.cancel()
        await asyncio.gather(consumer, *([stopper] if stopper else []), return_exceptions=True)


@pytest.mark.asyncio
async def test_stream_abort_does_not_stop_other_instance(built):
    first, one, model_a = built("stream-a")
    second, two, model_b = built("stream-b")
    model_a.release.clear()
    model_b.release.clear()

    async def consume(agent, ctx):
        return [chunk async for chunk in runtime().stream_bound(agent, {"query": "stream"}, context=ctx)]

    tasks = [asyncio.create_task(consume(first, one)), asyncio.create_task(consume(second, two))]
    try:
        await asyncio.wait_for(asyncio.gather(model_a.started.wait(), model_b.started.wait()), 5)
        await runtime().abort_bound(first, context=one)
        with pytest.raises(asyncio.CancelledError):
            await tasks[0]
        assert model_a.cancel_seen.is_set()
        assert not model_b.cancel_seen.is_set()
        assert not tasks[1].done()
        model_b.release.set()
        chunks = await tasks[1]
        assert any(getattr(chunk, "type", None) == "answer" for chunk in chunks)
    finally:
        model_a.release.set()
        model_b.release.set()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_default_deep_agent_does_not_enable_persistent_branch(built):
    from openjiuwen.harness import create_deep_agent

    _, ctx, persistent_model = built()
    personal_model = LocalModel()
    personal = create_deep_agent(
        personal_model,
        card=AgentCard(id="personal-default", name="personal"),
        enable_sys_operation=False,
        enable_security_rail=False,
        enable_tool_resilience_rail=False,
        enable_task_loop=False,
        auto_create_workspace=False,
    )
    session = create_agent_session(session_id="personal-session", card=personal.card)
    try:
        assert personal.deep_config.stream_task_lifecycle is None
        chunks = [chunk async for chunk in personal.stream({"query": "personal"}, session=session)]
        answers = [chunk.payload for chunk in chunks if getattr(chunk, "type", None) == "answer"]
        assert len(answers) == 1 and answers[0]["output"] == "local answer"
        assert not persistent_model.queries
        assert ctx.binding.runtime.active_task is None
    finally:
        personal.ability_manager.teardown_tools()


@pytest.mark.asyncio
@pytest.mark.parametrize("model_failure", [False, True])
async def test_cancel_during_native_commit_waits_without_interrupting_commit(built, monkeypatch, model_failure):
    agent, ctx, model = built()
    model.failure = model_failure
    entered, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
    interrupted = []
    native_commit = ctx.session.commit

    async def delayed_commit(*args, **kwargs):
        entered.set()
        try:
            await release.wait()
            result = await native_commit(*args, **kwargs)
            finished.set()
            return result
        except asyncio.CancelledError:
            interrupted.append(True)
            raise

    monkeypatch.setattr(ctx.session, "commit", delayed_commit)

    async def consume():
        return [chunk async for chunk in runtime().stream_bound(agent, {"query": "commit-window"}, context=ctx)]

    consumer = asyncio.create_task(consume())
    try:
        await asyncio.wait_for(entered.wait(), 5)
        consumer.cancel()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not interrupted, "consumer cancellation interrupted native Session.commit"
        assert not consumer.done()
        with pytest.raises(BaseError):
            await runtime().close_bound(agent, context=ctx)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await consumer
        assert finished.is_set()
        assert all(task.done() for task in model.stream_tasks)
        await runtime().close_bound(agent, context=ctx)
    finally:
        release.set()
        await asyncio.gather(consumer, return_exceptions=True)


@pytest.mark.asyncio
async def test_tools_and_cleanup_are_owner_scoped(built, monkeypatch):
    class Remember(Tool):
        def __init__(self):
            super().__init__(
                card=ToolCard(
                    id="remember",
                    name="remember",
                    input_params={"type": "object", "properties": {"value": {"type": "string"}}},
                )
            )
            self.received = []

        async def invoke(self, inputs, **kwargs):
            self.received.append(inputs)
            return {"remembered": inputs}

        async def stream(self, inputs, **kwargs):
            yield await self.invoke(inputs, **kwargs)

    monkeypatch.setitem(_TOOL_PROVIDER_REGISTRY, "test.binding.remember", lambda params, context: Remember())
    tools = [BuiltinToolSpec(type="test.binding.remember")]
    first, one, _ = built("session-a", tools=tools)
    second, two, _ = built("session-b", tools=tools)

    class TestEmitter:
        async def emit(self, *_args, **_kwargs):
            return None

    api = import_module("openjiuwen.harness.persistent_agent")

    def attach_test_capabilities(context):
        execution = context.execution
        scope = api.InvocationRef.from_execution(execution).model_dump()

        async def admit(_execution, model_call_id):
            return api.BudgetAdmission(
                **scope,
                model_call_id=model_call_id,
                status="admitted",
                reservation_ref="test-reservation",
            )

        async def report(_execution, model_call_id, _usage):
            return api.BudgetReport(
                **scope,
                model_call_id=model_call_id,
                status="confirmed",
                receipt_ref="test-receipt",
            )

        context.binding.control.emitter = TestEmitter()
        context.binding.ports.budget.admit = admit
        context.binding.ports.budget.report = report

    # This test exercises direct native binding and owner-scoped cleanup, not
    # the PA-04 event path; provide the event capability required by PA-15.
    attach_test_capabilities(one)
    attach_test_capabilities(two)
    id_a, id_b = "remember_" + one.tool_owner_id, "remember_" + two.tool_owner_id
    tool_a, tool_b = Runner.resource_mgr.get_tool(id_a), Runner.resource_mgr.get_tool(id_b)
    await runtime().invoke_bound(first, {"query": "run-tool"}, context=one)
    assert tool_a.received == [{"value": "private"}]
    assert tool_b.received == []
    await runtime().close_bound(first, context=one)
    assert Runner.resource_mgr.get_tool(id_a) is None
    assert Runner.resource_mgr.get_tool(id_b) is tool_b
    assert (await runtime().invoke_bound(second, {"query": "run-tool"}, context=two))["output"] == "local answer"
    assert tool_b.received == [{"value": "private"}]


def test_binding_refuses_event_loop_reuse(built):
    agent, ctx, _ = built()

    async def once():
        return await runtime().invoke_bound(agent, {"query": "hello"}, context=ctx)

    assert asyncio.run(once())["output"] == "local answer"
    with pytest.raises(BaseError):
        asyncio.run(once())


@pytest.mark.asyncio
async def test_close_cannot_race_inflight_abort(built, monkeypatch):
    agent, ctx, _ = built()
    entered, release = asyncio.Event(), asyncio.Event()
    native_abort = agent.abort

    async def delayed_abort(*, session):
        await native_abort(session=session)
        entered.set()
        await release.wait()

    monkeypatch.setattr(agent, "abort", delayed_abort)
    task = asyncio.create_task(runtime().abort_bound(agent, context=ctx))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        with pytest.raises(BaseError):
            await runtime().close_bound(agent, context=ctx)
    finally:
        release.set()
        await task
    await runtime().close_bound(agent, context=ctx)


@pytest.mark.asyncio
async def test_stopped_native_call_cannot_return_a_normal_success(built, monkeypatch):
    agent, ctx, model = built()
    entered = asyncio.Event()
    native_invoke = model.invoke

    async def suppress_cancellation(messages, **kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            return await native_invoke(messages, **kwargs)

    monkeypatch.setattr(model, "invoke", suppress_cancellation)
    task = asyncio.create_task(runtime().invoke_bound(agent, {"query": "late"}, context=ctx))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        await runtime().abort_bound(agent, context=ctx)
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_context_changed_during_native_call_is_rejected_on_return(built):
    agent, ctx, model = built()
    model.release.clear()
    task = asyncio.create_task(runtime().invoke_bound(agent, {"query": "waiting"}, context=ctx))
    try:
        await asyncio.wait_for(model.started.wait(), 5)
        ctx.execution = replace(ctx.execution, request_id="other-request")
        model.release.set()
        with pytest.raises(BaseError):
            await task
    finally:
        model.release.set()
        await asyncio.gather(task, return_exceptions=True)

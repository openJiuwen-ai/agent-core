# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openjiuwen.agent_teams.harness import TeamHarness, NativeHarness, HarnessState, bind_leader_preparation
from openjiuwen.agent_teams.schema.team import TeamRole
from tests.unit_tests.agent_teams.harness.fixtures import make_spec


@pytest.mark.asyncio
async def test_leader_prepare_observes_restored_child_before_supervisor(monkeypatch):
    spec = make_spec()
    spec.agent_template_spec = {
        "agent_card": {"id": "leader-template", "name": "leader-template", "description": "test"},
    }
    native = NativeHarness(spec)
    native.load_agent_template_spec = AsyncMock()
    harness = TeamHarness(spec, None, native, role=TeamRole.LEADER, member_name="custom-chief")
    native.start = AsyncMock()
    seen = []

    async def prepare(actual, child, _team_session):
        assert actual is native
        assert child.get_agent_id() == native.card.id
        assert child._pre_run_done
        assert actual.is_initialized
        native.load_agent_template_spec.assert_awaited_once()
        native.start.assert_not_awaited()
        seen.append(child)

    with bind_leader_preparation(prepare):
        await harness.start()
    native.start.assert_awaited_once_with(session=seen[0])


@pytest.mark.asyncio
async def test_failed_preparation_never_starts_native():
    spec = make_spec()
    native = NativeHarness(spec)
    harness = TeamHarness(spec, None, native, role=TeamRole.LEADER, member_name="chief")
    native.start = AsyncMock()

    async def failed(_actual, _child, _team_session):
        raise OSError("commit failed")

    with bind_leader_preparation(failed), pytest.raises(OSError):
        await harness.start()
    native.start.assert_not_awaited()


@pytest.mark.asyncio
async def test_teammate_never_invokes_leader_preparation():
    spec = make_spec()
    native = NativeHarness(spec)
    harness = TeamHarness(spec, None, native, role=TeamRole.TEAMMATE, member_name="worker")
    native.start = AsyncMock()
    callback = AsyncMock()
    with bind_leader_preparation(callback):
        await harness.start()
    callback.assert_not_awaited()


@pytest.mark.asyncio
async def test_idle_check_waits_until_async_tool_completion_is_injected():
    native = NativeHarness(make_spec())
    runtime = native.async_tool_runtime
    injection_started = asyncio.Event()
    release_injection = asyncio.Event()
    injected = asyncio.Event()

    async def work():
        return "result"

    async def inject(_text):
        injection_started.set()
        await release_injection.wait()
        injected.set()

    runtime.inject = inject
    assert native.conversation_is_idle()
    try:
        runtime.launch("idle-check", work, tool_name="demo", description="idle boundary")
        assert runtime.has_pending_tasks()
        assert not native.conversation_is_idle()
        await asyncio.wait_for(injection_started.wait(), timeout=1)
        assert runtime.get("idle-check").status == "completed"
        assert not runtime.has_running("demo")
        assert runtime.has_pending_tasks()
        assert not native.conversation_is_idle()

        release_injection.set()
        await asyncio.wait_for(injected.wait(), timeout=1)
        await asyncio.sleep(0)
        assert not runtime.has_pending_tasks()
        assert native.conversation_is_idle()
    finally:
        release_injection.set()
        runtime.cancel_all()


@pytest.mark.asyncio
async def test_queued_input_is_rejected_without_crashing_native_supervisor():
    from openjiuwen.agent_teams.harness.control import _CmdSend
    from openjiuwen.agent_teams.harness.state import InboxMessage

    native = NativeHarness(make_spec())
    native._on_send = AsyncMock()

    def blocked(_request_id=None):
        raise RuntimeError("inactive_owner")

    native.set_conversation_guard(blocked)
    ack = asyncio.get_running_loop().create_future()
    await native._dispatch(_CmdSend(msg=InboxMessage(seq=0, content="late member message", immediate=False), ack=ack))
    with pytest.raises(RuntimeError, match="inactive_owner"):
        await ack
    native._on_send.assert_not_awaited()
    assert native.state is HarnessState.IDLE


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_stage", ["save_contexts", "commit"])
async def test_conversation_commit_failure_preserves_supervisor_and_queued_input(monkeypatch, caplog, failure_stage):
    from openjiuwen.core.runner import Runner
    from tests.unit_tests.agent_teams.harness.fixtures import (
        answer_outputs, drain_outputs, start_harness, wait_for_state,
    )

    await Runner.start()
    native = NativeHarness(make_spec())
    consumer = None
    release = asyncio.Event()
    try:
        caplog.set_level("ERROR")
        fake = await start_harness(native)
        native.set_conversation_guard(lambda _request_id: None)
        save_contexts = AsyncMock(side_effect=[OSError("checkpoint failed"), None, None]
                                  if failure_stage == "save_contexts" else None)
        monkeypatch.setattr(fake.context_engine, "save_contexts", save_contexts, raising=False)
        original_commit = native._session.commit
        commit_failed = False

        async def commit():
            nonlocal commit_failed
            # Opening a turn also commits; fail only the conversation commit.
            if failure_stage == "commit" and save_contexts.await_count and not commit_failed:
                commit_failed = True
                raise OSError("checkpoint failed")
            await original_commit()

        monkeypatch.setattr(native._session, "commit", commit)
        entered = asyncio.Event()
        original_invoke = fake.invoke

        async def invoke(*args, **kwargs):
            entered.set()
            await release.wait()
            return await original_invoke(*args, **kwargs)

        monkeypatch.setattr(fake, "invoke", invoke)
        collected = []
        consumer = asyncio.create_task(drain_outputs(native, collected))
        await native.send("first")
        await asyncio.wait_for(entered.wait(), timeout=2)
        await native.send("queued", immediate=False)
        release.set()
        assert await wait_for_state(native, HarnessState.IDLE)
        assert [item["query"] for item in fake.invocations] == ["first", "queued"]
        assert not consumer.done()

        await native.send("next")
        assert await wait_for_state(native, HarnessState.IDLE)
        assert save_contexts.await_count == 3
        assert "round_id=1 conversation commit failed" in caplog.text
        assert "checkpoint failed" in caplog.text
    finally:
        release.set()
        await native.stop()
        if consumer is not None:
            await asyncio.wait_for(consumer, timeout=2)
        await Runner.stop()

    assert answer_outputs(collected) == ["echo:first", "echo:queued", "echo:next"]


@pytest.mark.asyncio
@pytest.mark.parametrize("pending", [None, "member", "paused", "task", "mailbox", "resume", "identity"])
async def test_cold_team_checks_all_pending_work_and_restores_session_binding(monkeypatch, pending):
    from openjiuwen.agent_teams.context import get_session_id, reset_session_id, set_session_id
    from openjiuwen.agent_teams.harness import persisted_team_conversation_is_idle
    from openjiuwen.agent_teams.spawn import shared_resources

    async def members(_team):
        assert get_session_id() == "cold-session"
        status = {"member": "busy", "paused": "paused"}.get(pending, "ready")
        return [SimpleNamespace(status=status)]

    db = SimpleNamespace(
        initialize=AsyncMock(),
        member=SimpleNamespace(get_team_members=members),
        task=SimpleNamespace(get_team_tasks=AsyncMock(return_value=[
            SimpleNamespace(status="in_progress" if pending == "task" else "completed"),
        ])),
        message=SimpleNamespace(has_unread_messages=AsyncMock(return_value=pending == "mailbox")),
    )
    monkeypatch.setattr(shared_resources, "get_shared_db", lambda _: db)
    bucket = {"context": {"db_config": {"db_type": "memory"}}}
    if pending == "resume":
        bucket["pending_resume"] = {"query": "old"}
    if pending == "identity":
        bucket.pop("context")
    session = SimpleNamespace(get_session_id=lambda: "cold-session", get_state=lambda _: {"custom-team": bucket})
    token = set_session_id("other-session")
    try:
        assert await persisted_team_conversation_is_idle(session) is (pending is None)
        assert get_session_id() == "other-session"
    finally:
        reset_session_id(token)


@pytest.mark.asyncio
async def test_imported_messages_reach_native_executor_and_survive_next_checkpoint(monkeypatch):
    from openjiuwen.core.common.run_context import RunContext
    from openjiuwen.core.context_engine import ContextEngine, ContextEngineConfig, export_conversation, replace_conversation
    from openjiuwen.core.foundation.llm import AssistantMessage, UserMessage
    from openjiuwen.core.runner import Runner
    from openjiuwen.core.session.agent import create_agent_session
    from openjiuwen.core.session.checkpointer import CheckpointerFactory
    from openjiuwen.core.session.checkpointer.persistence import PersistenceCheckpointer
    from tests.unit_tests.core.session.checkpointer.test_persistence_storage import MockKVStore
    from tests.unit_tests.agent_teams.harness.fixtures import FakeReactAgent, start_harness, wait_for_state

    checkpointer = PersistenceCheckpointer(MockKVStore())
    monkeypatch.setattr(CheckpointerFactory, "get_checkpointer", lambda: checkpointer)

    class ModelProbe(FakeReactAgent):
        async def invoke(self, inputs, session, **kwargs):
            context = self.context_engine.get_context(session_id=session.get_session_id())
            assert [message.content for message in context.get_messages()] == ["A", "D-leader-secret"]
            self.invocations.append(inputs)
            await context.add_messages([UserMessage(content=inputs["query"]), AssistantMessage(content="used-D")])
            return {"output": "used-D", "result_type": "answer"}

    await Runner.start()
    native = NativeHarness(make_spec())
    try:
        await start_harness(native)
        probe = ModelProbe(native.card)
        probe.context_engine = ContextEngine(ContextEngineConfig(enable_openrouter_model_context_window_tokens=False))
        native.set_react_agent(probe, initialized=True)
        child = native._session
        admitted = []
        native.set_conversation_guard(admitted.append)
        await replace_conversation(child, probe.context_engine, {
            "messages": [UserMessage(content="A"), AssistantMessage(content="D-leader-secret")],
        }, {"id": "b"})
        await native.send("B", run_context=RunContext(extra={"conversation_request_id": "b"}))
        assert await wait_for_state(native, HarnessState.IDLE)
        assert [item["query"] for item in probe.invocations] == ["B"]
        assert admitted.count("b") == 1
        restored = create_agent_session(session_id=child.get_session_id(), card=native.card)
        await restored.pre_run()
        assert [message.content for message in export_conversation(restored)["messages"]] == [
            "A", "D-leader-secret", "B", "used-D",
        ]
    finally:
        await native.stop()
        await Runner.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("pending", [None, "task", "paused", "unread"])
async def test_cached_leader_peer_check_uses_explicit_session(monkeypatch, pending):
    from openjiuwen.agent_teams.context import get_session_id, reset_session_id, set_session_id
    from openjiuwen.agent_teams.rails import team_context

    async def members(_name):
        assert get_session_id() == "correct-session"
        return [SimpleNamespace(member_name="chief", status="starting"),
                SimpleNamespace(member_name="worker", status="paused" if pending == "paused" else "ready")]

    backend = SimpleNamespace(
        team_name="custom-team", member_name="chief",
        db=SimpleNamespace(member=SimpleNamespace(get_team_members=members)),
        task_manager=SimpleNamespace(list_tasks=AsyncMock(return_value=[
            SimpleNamespace(status="in_progress" if pending == "task" else "completed"),
        ])),
        message_manager=SimpleNamespace(has_unread_messages=AsyncMock(return_value=pending == "unread")),
    )
    monkeypatch.setattr(team_context, "get_team_backend", lambda _: backend)
    native = NativeHarness(make_spec())
    token = set_session_id("unrelated-session")
    try:
        assert await native.conversation_peers_are_idle(session_id="correct-session") is (pending is None)
        assert get_session_id() == "unrelated-session"
    finally:
        reset_session_id(token)

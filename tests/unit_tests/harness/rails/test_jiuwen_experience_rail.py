# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Optional experience synchronization on the existing JiuwenMemoryRail."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext, AgentCallbackEvent, InvokeInputs, ToolCallInputs,
)
from openjiuwen.harness.rails import JiuwenMemoryCodingRail, JiuwenMemoryRail
from openjiuwen.harness.rails.memory import experience_capture
from tests.unit_tests.harness.rails.test_jiuwen_memory_rail import MockAgent, MockMemoryProvider


def _invoke(agent, query, result=None, session="session", event=AgentCallbackEvent.BEFORE_INVOKE):
    return AgentCallbackContext(
        agent=agent, event=event,
        inputs=InvokeInputs(query=query, conversation_id=session, result=result),
    )


async def _tool(rail, agent, name="shell", args=None, result="ok", call_id="call", session="session"):
    await rail.after_tool_call(AgentCallbackContext(
        agent=agent, event=AgentCallbackEvent.AFTER_TOOL_CALL,
        session=SimpleNamespace(get_session_id=lambda: session),
        inputs=ToolCallInputs(
            tool_call=SimpleNamespace(id=call_id), tool_name=name,
            tool_args=args or {}, tool_result=result,
        ),
    ))


async def _finish(rail, agent, query, result):
    await rail.after_invoke(_invoke(agent, query, result, event=AgentCallbackEvent.AFTER_INVOKE))
    await rail.drain_experience()
    if rail._sync_task is not None:
        await rail._sync_task


def test_disabled_experience_keeps_ordinary_memory_and_tools():
    agent, provider = MockAgent(), MockMemoryProvider()
    provider.sync_experience = AsyncMock()
    rail = JiuwenMemoryRail(provider)
    rail.init(agent)
    assert agent.ability_manager.names() == {"memory_search", "memory_add"}

    async def run():
        await rail.before_invoke(_invoke(agent, "hello"))
        await _tool(rail, agent)
        await _finish(rail, agent, "hello", {"output": "answer"})

    asyncio.run(run())
    assert provider.prefetch_queries == ["hello"]
    assert provider.sync_calls == [("hello", "answer")]
    provider.sync_experience.assert_not_called()
    assert rail._experience is None
    rail.uninit(agent)
    assert agent.ability_manager.names() == set()
    assert provider.shutdown_called


def test_enabled_experience_and_ordinary_memory_both_submit():
    agent, provider = MockAgent(), MockMemoryProvider()
    sync = AsyncMock()
    rail = JiuwenMemoryRail(provider, sync_experience=sync)
    rail.init(agent)
    assert agent.ability_manager.names() == {"memory_search", "memory_add", "set_task_status"}

    async def run():
        status = await agent.ability_manager.func_of("set_task_status").invoke({"status": "completed"})
        assert status == {"status": "completed"}
        await rail.before_invoke(_invoke(agent, "repair CI"))
        await _tool(rail, agent, args={"command": "run tests"}, result="tests passed")
        await _finish(rail, agent, "repair CI", {"result_type": "answer", "output": "fixed"})

    asyncio.run(run())
    assert provider.prefetch_queries == ["repair CI"]
    assert provider.sync_calls == [("repair CI", "fixed")]
    sync.assert_awaited_once()
    trajectory_id, query, output, activities, session = sync.call_args.args
    assert trajectory_id.startswith("agent_") and session == "session"
    assert (query, output) == ("repair CI", "fixed")
    assert [(a["tool_name"], a["tool_result_preview"]) for a in activities] == [("shell", "tests passed")]
    rail.uninit(agent)
    assert agent.ability_manager.names() == set()


@pytest.mark.asyncio
async def test_coding_experience_keeps_evidence_separate_for_owners_in_one_session():
    provider = MockMemoryProvider()
    sync = AsyncMock()
    rail = JiuwenMemoryCodingRail(provider, sync_experience=sync)
    main, child = MockAgent(), MockAgent()
    main._react_agent = SimpleNamespace()
    rail.init(main)
    rail.init(child)
    try:
        assert "set_task_status" in main.ability_manager.names()
        assert "set_task_status" in child.ability_manager.names()
        await rail.before_invoke(_invoke(main, "edit config"))
        await rail.before_invoke(_invoke(child, "inspect fixture"))
        await _tool(rail, main._react_agent, "edit_file", result="updated", call_id="edit")
        await _tool(rail, child, "read_file", result="valid", call_id="read")
        await rail.after_invoke(_invoke(main, "edit config", {"output": "done"}))
        await rail.after_invoke(_invoke(child, "inspect fixture", {"output": "checked"}))
        await rail.drain()
        assert provider.sync_calls == [("edit config", "done"), ("inspect fixture", "checked")]
        evidence = {call.args[1]: [record["tool_name"] for record in call.args[3]]
                    for call in sync.await_args_list}
        assert sync.await_count == 2
        assert evidence == {"edit config": ["edit_file"], "inspect fixture": ["read_file"]}
    finally:
        await rail.aclose()
    assert "set_task_status" not in main.ability_manager.names()
    assert "set_task_status" not in child.ability_manager.names()


def test_waiting_execution_resumes_and_submits_once():
    agent, provider = MockAgent(), MockMemoryProvider()
    sync = AsyncMock()
    rail = JiuwenMemoryRail(provider, sync_experience=sync)
    rail.init(agent)

    async def run():
        await rail.before_invoke(_invoke(agent, "repair CI"))
        await _tool(rail, agent, "read_logs", result="failed check", call_id="logs")
        await _tool(rail, agent, "set_task_status", {"status": "waiting_for_user"}, call_id="waiting")
        await rail.after_invoke(_invoke(agent, "repair CI", {"result_type": "interrupt"}))
        sync.assert_not_called()
        assert provider.sync_calls == []
        # A different ordinary message in the same session must not become evidence.
        await rail.before_invoke(_invoke(agent, "unrelated question"))
        await _finish(rail, agent, "unrelated question", {"output": "unrelated answer"})
        sync.assert_not_called()
        resume = InteractiveInput()
        await rail.before_invoke(_invoke(agent, resume))
        await _tool(rail, agent, "rerun_build", result="passed", call_id="build")
        await _tool(rail, agent, "set_task_status", {"status": "completed"}, call_id="completed")
        await _finish(rail, agent, resume, {"result_type": "answer", "output": "fixed"})

    asyncio.run(run())
    sync.assert_awaited_once()
    assert sync.call_args.args[1] == "repair CI"
    assert [(a["tool_name"], a["tool_result_preview"]) for a in sync.call_args.args[3]] == [
        ("read_logs", "failed check"), ("rerun_build", "passed"),
    ]
    rail.uninit(agent)


@pytest.mark.parametrize("failure", ["capture", "submission"])
def test_experience_failure_does_not_block_ordinary_sync(monkeypatch, failure):
    agent, provider = MockAgent(), MockMemoryProvider()
    sync = AsyncMock(side_effect=ValueError("experience unavailable"))
    rail = JiuwenMemoryRail(provider, sync_experience=sync)
    rail.init(agent)
    if failure == "capture":
        def fail(_ctx):
            raise ValueError("capture failed")
        monkeypatch.setattr(rail._experience, "finish", fail)

    async def run():
        await rail.before_invoke(_invoke(agent, "task"))
        await _tool(rail, agent)
        await _finish(rail, agent, "task", {"output": "done"})

    asyncio.run(run())
    assert provider.sync_calls == [("task", "done")]
    rail.uninit(agent)


def test_plain_chat_with_experience_enabled_only_syncs_dialogue():
    agent, provider = MockAgent(), MockMemoryProvider()
    sync = AsyncMock()
    rail = JiuwenMemoryRail(provider, sync_experience=sync)
    rail.init(agent)

    async def run():
        await rail.before_invoke(_invoke(agent, "hello"))
        await _finish(rail, agent, "hello", {"output": "hi"})

    asyncio.run(run())
    assert provider.sync_calls == [("hello", "hi")]
    sync.assert_not_called()
    rail.uninit(agent)


def test_unavailable_provider_does_not_register_experience_tool():
    agent, provider = MockAgent(), MockMemoryProvider(available=False)
    sync = AsyncMock()
    rail = JiuwenMemoryRail(provider, sync_experience=sync)
    rail.init(agent)
    assert agent.ability_manager.names() == set()
    asyncio.run(rail.before_invoke(_invoke(agent, "hello")))
    assert rail._experience._experience_active == {}
    sync.assert_not_called()
    rail.uninit(agent)


def test_invalid_experience_callback_is_rejected():
    with pytest.raises(TypeError, match="sync_experience"):
        JiuwenMemoryRail(MockMemoryProvider(), sync_experience="enabled")


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_public_agent_dispatches_both_memory_paths(tmp_path, enabled):
    from openjiuwen.core.runner import Runner
    from tests.unit_tests.fixtures.deep_agent_interrupts import CountingTool, make_deep_agent, run, scripted
    from tests.unit_tests.fixtures.mock_llm import MockLLMModel, create_text_response, create_tool_call_response

    provider = MockMemoryProvider(prefetch_result="ORDINARY_MEMORY_SENTINEL")
    sync = AsyncMock()
    rail = JiuwenMemoryRail(provider, sync_experience=sync if enabled else None)
    tool = CountingTool("verify_build")
    agent = make_deep_agent(tmp_path, tools=[tool], rails=[rail])
    model = MockLLMModel()
    model.set_responses([
        create_tool_call_response("verify_build", "{}", tool_call_id="verify"),
        create_text_response("verified"),
    ])
    await Runner.start()
    try:
        with scripted(model):
            result = await run(agent, "verify CI", f"experience-{enabled}")
        await rail.drain_experience()
        if rail._sync_task:
            await rail._sync_task
        assert result["output"] == "verified" and tool.invoke_count == 1
        assert "ORDINARY_MEMORY_SENTINEL" in str(model.call_history)
        assert provider.sync_calls == [("verify CI", "verified")]
        if enabled:
            sync.assert_awaited_once()
            activities = sync.call_args.args[3]
            assert [a["tool_name"] for a in activities] == ["verify_build"]
            assert "verify_build" in activities[0]["tool_result_preview"]
        else:
            sync.assert_not_called()
    finally:
        await Runner.stop()


@pytest.mark.parametrize("rail_type", [JiuwenMemoryRail, JiuwenMemoryCodingRail])
@pytest.mark.asyncio
async def test_malformed_tool_evidence_does_not_break_memory(rail_type):
    agent, provider = MockAgent(), MockMemoryProvider()
    sync = AsyncMock()
    rail = rail_type(provider, sync_experience=sync)
    rail.init(agent)
    malformed = {}
    malformed["self"] = malformed
    try:
        await rail.before_invoke(_invoke(agent, "task"))
        await _tool(rail, agent, "malformed", args=malformed)
        await _tool(rail, agent, "valid", call_id="valid")
        if isinstance(rail, JiuwenMemoryCodingRail):
            await rail.after_invoke(_invoke(agent, "task", {"output": "done"}))
            await rail.drain()
        else:
            await _finish(rail, agent, "task", {"output": "done"})
        assert provider.sync_calls == [("task", "done")]
        sync.assert_awaited_once()
        assert [activity["tool_name"] for activity in sync.call_args.args[3]] == ["valid"]
    finally:
        if isinstance(rail, JiuwenMemoryCodingRail):
            await rail.aclose()
        else:
            rail.uninit(agent)
            await rail._shutdown_task


@pytest.mark.asyncio
async def test_work_uninit_drains_experience_before_provider_shutdown():
    agent, provider = MockAgent(), MockMemoryProvider()
    started, release = asyncio.Event(), asyncio.Event()
    order = []

    async def sync(*_args):
        started.set()
        await release.wait()
        order.append("experience")

    async def shutdown():
        order.append("shutdown")

    provider.shutdown = shutdown
    rail = JiuwenMemoryRail(provider, sync_experience=sync)
    rail.init(agent)
    await rail.before_invoke(_invoke(agent, "task"))
    await _tool(rail, agent)
    await rail.after_invoke(_invoke(agent, "task", {"output": "done"}))
    await asyncio.wait_for(started.wait(), timeout=2)
    rail.uninit(agent)
    assert "set_task_status" not in agent.ability_manager.names()
    assert not order
    release.set()
    await asyncio.wait_for(rail._shutdown_task, timeout=2)
    assert order == ["experience", "shutdown"]


@pytest.mark.asyncio
async def test_experience_submission_retries_the_same_trajectory(monkeypatch):
    delay = AsyncMock()
    monkeypatch.setattr(experience_capture.asyncio, "sleep", delay)
    agent, provider = MockAgent(), MockMemoryProvider()
    sync = AsyncMock(side_effect=[OSError(), TimeoutError(), None])
    rail = JiuwenMemoryRail(provider, sync_experience=sync)
    rail.init(agent)
    await rail.before_invoke(_invoke(agent, "task"))
    await _tool(rail, agent)
    await _finish(rail, agent, "task", {"output": "done"})
    assert [call.args for call in sync.await_args_list] == [sync.call_args.args] * 3
    assert [call.args for call in delay.await_args_list] == [(1,), (3,)]
    assert provider.sync_calls == [("task", "done")]
    rail.uninit(agent)
    await rail._shutdown_task


@pytest.mark.parametrize("removal", ["expired", "evicted"])
@pytest.mark.asyncio
async def test_old_suspended_experience_cannot_resume(monkeypatch, removal):
    clock = Mock(return_value=100)
    monkeypatch.setattr(experience_capture, "time", SimpleNamespace(monotonic=clock))
    monkeypatch.setattr(experience_capture, "_MAX_SUSPENDED", 2)
    monkeypatch.setattr(experience_capture, "_CAPTURE_TTL", 20)
    agent, provider = MockAgent(), MockMemoryProvider()
    sync = AsyncMock()
    rail = JiuwenMemoryRail(provider, sync_experience=sync)
    rail.init(agent)

    async def suspend(session):
        await rail.before_invoke(_invoke(agent, session, session=session))
        await _tool(rail, agent, "before_pause", session=session)
        await rail.after_invoke(_invoke(agent, session, {"result_type": "interrupt"}, session=session))

    async def resume(session, call_id):
        query = InteractiveInput()
        await rail.before_invoke(_invoke(agent, query, session=session))
        await _tool(rail, agent, "after_pause", call_id=call_id, session=session)
        await rail.after_invoke(_invoke(agent, query, {"output": "done"}, session=session))

    await suspend("old")
    if removal == "expired":
        clock.return_value = 120
    else:
        await suspend("middle")
    await suspend("recent")
    await resume("old", "old_resume")
    await resume("recent", "recent_resume")
    await rail.drain_experience()
    sync.assert_awaited_once()
    assert sync.call_args.args[1] == "recent"
    assert [activity["tool_name"] for activity in sync.call_args.args[3]] == ["before_pause", "after_pause"]
    rail.uninit(agent)
    await rail._shutdown_task

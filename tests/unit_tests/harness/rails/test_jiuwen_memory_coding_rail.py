# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Coding document-memory ownership, real callback dispatch and write lifecycle."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from openjiuwen.harness.rails import JiuwenMemoryCodingRail
from tests.unit_tests.harness.rails.test_jiuwen_memory_rail import MockAgent, MockMemoryProvider, MockSession, make_ctx


def context(agent, query="query", result="answer", run_kind=None):
    ctx = make_ctx(query=query, result={"output": result}, run_kind=run_kind)
    ctx.agent, ctx.session = agent, MockSession()
    return ctx


@pytest.mark.asyncio
async def test_shared_provider_keeps_owner_cache_and_tool_cleanup_separate():
    provider = MockMemoryProvider()
    provider.initialize = AsyncMock()
    provider.prefetch = AsyncMock(side_effect=lambda query, **kw: "memory:" + query)
    provider.shutdown = AsyncMock()
    rail = JiuwenMemoryCodingRail(provider)
    main, child = MockAgent(), MockAgent()
    main._react_agent = SimpleNamespace()
    rail.init(main)
    rail.init(child)
    await rail.before_invoke(context(main, "main"))
    await rail.before_invoke(context(child, "child"))
    await rail.before_model_call(context(main._react_agent, "model envelope"))
    assert provider.initialize.await_count == 1
    assert provider.prefetch.await_count == 2
    main_items = await main.prompt_attachment_manager.list_by_filter(session_id="sess1")
    child_items = await child.prompt_attachment_manager.list_by_filter(session_id="sess1")
    assert "memory:main" in main_items[0].content
    assert "memory:child" in child_items[0].content
    rail.uninit(child)
    await rail.wait_closed()
    assert main.ability_manager.names() and not child.ability_manager.names()
    provider.shutdown.assert_not_awaited()
    await rail.aclose()
    provider.shutdown.assert_awaited_once()
    assert not await main.prompt_attachment_manager.list_by_filter(session_id="sess1")


@pytest.mark.asyncio
async def test_writes_are_serial_and_last_owner_shutdown_drains():
    provider = MockMemoryProvider()
    provider.shutdown = AsyncMock()
    started, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def write(user, assistant, **kw):
        calls.append(user)
        if user == "first":
            started.set()
            await release.wait()

    provider.sync_turn = write
    rail = JiuwenMemoryCodingRail(provider)
    main, child = MockAgent(), MockAgent()
    rail.init(main)
    rail.init(child)
    await rail.before_invoke(context(main))
    await rail.after_invoke(context(main, "first"))
    await started.wait()
    await rail.after_invoke(context(child, "second"))
    await asyncio.sleep(0)
    assert calls == ["first"]
    closing = asyncio.create_task(rail.aclose())
    await asyncio.sleep(0)
    provider.shutdown.assert_not_awaited()
    release.set()
    await closing
    assert calls == ["first", "second"]
    provider.shutdown.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["heartbeat", "cron"])
async def test_background_reads_existing_documents_but_never_syncs(kind):
    provider = MockMemoryProvider()
    provider.prefetch = AsyncMock(return_value="existing document")
    rail = JiuwenMemoryCodingRail(provider)
    agent = MockAgent()
    rail.init(agent)
    ctx = context(agent, run_kind=kind)
    await rail.before_invoke(ctx)
    await rail.after_invoke(ctx)
    assert provider.prefetch.call_args.kwargs["background"] is True
    assert provider.sync_calls == []
    await rail.aclose()


@pytest.mark.asyncio
async def test_failed_initialize_does_not_break_turn_or_claim_write():
    provider = MockMemoryProvider()
    provider.initialize = AsyncMock(side_effect=RuntimeError("offline"))
    rail = JiuwenMemoryCodingRail(provider)
    agent = MockAgent()
    rail.init(agent)
    await rail.before_invoke(context(agent))
    await rail.after_invoke(context(agent))
    assert not provider.prefetch_queries and not provider.sync_calls
    await rail.aclose()


@pytest.mark.asyncio
async def test_public_code_agent_dispatches_memory_callbacks(tmp_path):
    from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig
    from openjiuwen.core.runner import Runner
    from openjiuwen.core.single_agent import AgentCard, create_agent_session
    from openjiuwen.harness.subagents.code_agent import create_code_agent
    from tests.unit_tests.fixtures.mock_llm import create_text_response, mock_llm_context

    provider = MockMemoryProvider(prefetch_result="PROJECT_MEMORY_SENTINEL")
    rail = JiuwenMemoryCodingRail(provider)
    model = Model(model_client_config=ModelClientConfig(
        client_provider="OpenAI", api_key="mock", api_base="http://mock-base"),
        model_config=ModelRequestConfig(model="mock-model"))
    agent = create_code_agent(model=model, rails=[rail], workspace=str(tmp_path))
    session = create_agent_session(session_id="coding-memory", card=AgentCard(id="coding-memory"))
    await Runner.start()
    try:
        with mock_llm_context() as llm:
            llm.set_responses([create_text_response("done")])
            result = await Runner.run_agent(agent, {"query": "project question"}, session=session)
        await rail.drain()
        assert "PROJECT_MEMORY_SENTINEL" in str(llm.call_history)
        assert result["output"] == "done"
        assert provider.prefetch_queries == ["project question"]
        assert provider.sync_calls == [("project question", "done")]
        items = await agent.prompt_attachment_manager.list_by_filter(session_id="coding-memory")
        assert any("PROJECT_MEMORY_SENTINEL" in (item.content or "") for item in items)
    finally:
        await rail.aclose()
        await Runner.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["schemas", "ability", "prompt"])
async def test_registration_failure_rolls_back_without_breaking_agent(monkeypatch, failure):
    provider = MockMemoryProvider()
    provider.shutdown = AsyncMock()
    rail = JiuwenMemoryCodingRail(provider)
    healthy, agent = MockAgent(), MockAgent()
    rail.init(healthy)
    await rail.before_invoke(context(healthy))
    add = agent.ability_manager.add_ability

    def add_tool(card, tool):
        if card.name == "memory_add":
            raise RuntimeError("registration failed")
        return add(card, tool)

    if failure == "schemas":
        monkeypatch.setattr(provider, "get_tool_schemas", Mock(side_effect=RuntimeError("schemas")))
    elif failure == "ability":
        monkeypatch.setattr(agent.ability_manager, "add_ability", add_tool)
    else:
        monkeypatch.setattr(agent.system_prompt_builder, "add_section", Mock(side_effect=ValueError()))
    rail.init(agent)
    await rail.before_invoke(context(agent))
    await rail.after_invoke(context(agent))
    await rail.wait_closed()
    assert rail.attached_agents == (healthy,) and not agent.ability_manager.names()
    assert healthy.ability_manager.names() and not provider.sync_calls
    provider.shutdown.assert_not_awaited()
    await rail.aclose()
    provider.shutdown.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["tool", "prompt", "attachment"])
async def test_cleanup_failure_still_closes_provider(monkeypatch, failure):
    provider = MockMemoryProvider()
    provider.shutdown = AsyncMock()
    agent = MockAgent()
    rail = JiuwenMemoryCodingRail(provider)
    rail.init(agent)
    await rail.before_invoke(context(agent))
    remove = agent.ability_manager.remove_ability

    def remove_tool(name):
        if name == "memory_search":
            raise RuntimeError("remove failed")
        remove(name)

    if failure == "tool":
        monkeypatch.setattr(agent.ability_manager, "remove_ability", remove_tool)
    elif failure == "prompt":
        monkeypatch.setattr(agent.system_prompt_builder, "remove_section", Mock(side_effect=ValueError()))
    else:
        monkeypatch.setattr(agent.prompt_attachment_manager, "clear_source", AsyncMock(side_effect=ValueError()))
    await rail.aclose()
    assert not rail.attached_agents
    assert "memory_add" not in agent.ability_manager.names()
    provider.shutdown.assert_awaited_once()


@pytest.mark.asyncio
async def test_registered_tool_handles_invalid_schema_initialization_and_unmount(monkeypatch):
    provider = MockMemoryProvider()
    schemas = provider.get_tool_schemas()
    monkeypatch.setattr(provider, "get_tool_schemas", lambda: [{}, {"name": " "}, None, *schemas])
    provider.initialize = AsyncMock(side_effect=RuntimeError("offline"))
    provider.handle_tool_call = AsyncMock(return_value='{"result": "stored"}')
    agent = MockAgent()
    rail = JiuwenMemoryCodingRail(provider)
    rail.init(agent)
    tool = agent.ability_manager.func_of("memory_add")
    assert "error" in json.loads(await tool.invoke({}))
    provider.handle_tool_call.assert_not_awaited()
    provider.initialize.side_effect = None
    assert json.loads(await tool.invoke({})) == {"result": "stored"}
    await rail.aclose()
    assert "error" in json.loads(await tool.invoke({}))
    provider.handle_tool_call.assert_awaited_once_with("memory_add", {})

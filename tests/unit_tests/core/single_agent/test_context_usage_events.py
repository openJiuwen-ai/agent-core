# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.context_engine import CacheAggregationKey, ContextEngineConfig, ContextWindow, RequestKVCacheUsage
from openjiuwen.core.foundation.llm import AssistantMessage, SystemMessage, UsageMetadata, UserMessage
from openjiuwen.core.session.agent import Session
from openjiuwen.core.session.agent_team import create_agent_team_session
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent, ReActAgentConfig
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, InvokeInputs, ModelCallInputs
from openjiuwen.core.single_agent.schema.agent_card import AgentCard


@pytest.mark.asyncio
async def test_react_agent_emits_one_post_context_usage_event() -> None:
    agent = ReActAgent(AgentCard(name="usage-agent")).configure(
        ReActAgentConfig(
            model_name="deepseek-chat",
            model_provider="deepseek",
            context_engine_config=ContextEngineConfig(context_window_tokens=1_000),
        )
    )
    session = Session(session_id="usage-session", card=agent.card)
    session.write_stream = AsyncMock()
    context = await agent.context_engine.create_context(session=session)
    ctx = AgentCallbackContext(
        agent=agent,
        inputs=InvokeInputs(query="hello"),
        session=session,
        context=context,
    )
    agent.add_prompt_builder_section("memory", "remember the environment", priority=20)
    agent.add_prompt_builder_section(
        "skills",
        "use the selected skill",
        priority=40,
        category="skills",
    )
    window = ContextWindow(
        system_messages=[SystemMessage(content="remember the environment")],
        context_messages=[UserMessage(content="hello")],
    )

    agent._begin_context_usage_request(ctx)
    await agent._emit_context_usage(
        ctx,
        window,
        phase="post_call",
        usage_metadata=UsageMetadata(
            input_tokens=100,
            cache_read_tokens=40,
            cache_miss_tokens=60,
            cache_status="partial_hit",
            cache_source="provider_usage",
            cache_authoritative=True,
        ),
    )

    events = [call.args[0] for call in session.write_stream.await_args_list]
    assert [event.type for event in events] == ["context.usage"]
    assert events[0].payload["phase"] == "post_call"
    assert events[0].payload["sequence"] == 0
    assert events[0].payload["parts"]["skills"]["category"] == "skills"
    assert events[0].payload["context_window"]["input_tokens"] == 100
    assert events[0].payload["kv_cache"]["session"]["weighted_hit_rate"] == 0.4
    assert events[0].payload["session_kv_cache_hit_rate"] == 0.4
    assert sum(part["tokens"] for part in events[0].payload["parts"].values()) == 100
    assert events[0].payload["parts"]["messages"]["source"] == "provider_usage_residual"


@pytest.mark.asyncio
async def test_railed_model_call_does_not_emit_pre_call_usage_event() -> None:
    agent = ReActAgent(AgentCard(name="usage-call-agent")).configure(
        ReActAgentConfig(
            model_name="deepseek-chat",
            model_provider="deepseek",
            context_engine_config=ContextEngineConfig(context_window_tokens=1_000),
        )
    )
    session = Session(session_id="usage-call-session", card=agent.card)
    session.write_stream = AsyncMock()
    context = await agent.context_engine.create_context(session=session)
    response = AssistantMessage(
        content="done",
        usage_metadata=UsageMetadata(
            input_tokens=20,
            cache_read_tokens=10,
            cache_miss_tokens=10,
        ),
    )
    fake_llm = MagicMock()
    fake_llm.invoke = AsyncMock(return_value=response)
    agent._llm = fake_llm
    agent._sync_prompt_attachments = AsyncMock()
    agent._kv_cache_model_call_hook.resolve_runtime = MagicMock(return_value=object())
    agent._kv_cache_model_call_hook.resolve_lineage = MagicMock(
        return_value=(session.get_session_id(), None)
    )
    agent._kv_cache_model_call_hook.handle_context_window_change = AsyncMock()
    agent._kv_cache_model_call_hook.build_invoke_kwargs = MagicMock(return_value={})

    ctx = AgentCallbackContext(
        agent=agent,
        inputs=ModelCallInputs(model_context=context),
        session=session,
        context=context,
    )

    result = await agent._railed_model_call(ctx)

    assert result is response
    usage_events = [
        call.args[0]
        for call in session.write_stream.await_args_list
        if call.args[0].type == "context.usage"
    ]
    assert len(usage_events) == 1
    assert usage_events[0].payload["phase"] == "post_call"
    assert usage_events[0].payload["sequence"] == 0


@pytest.mark.asyncio
async def test_clear_session_keeps_usage_scope_for_reused_session_id() -> None:
    agent = ReActAgent(AgentCard(name="usage-lifecycle-agent"))
    session_id = "reused-session"
    key = CacheAggregationKey(session_id, "deepseek", "deepseek-chat")
    agent._context_usage_sequences[session_id] = 2
    agent._context_usage_aggregator.record(
        request_id="old-request",
        scope_key=key,
        usage=RequestKVCacheUsage(input_tokens=100, cache_read_tokens=50, cache_miss_tokens=50),
    )
    agent.context_engine.clear_context = AsyncMock()

    with patch("openjiuwen.core.runner.Runner.release", new=AsyncMock()):
        await agent.clear_session(session_id)

    assert agent._context_usage_sequences[session_id] == 2
    assert agent._context_usage_aggregator.snapshot(key).calls_total == 1


@pytest.mark.asyncio
async def test_team_member_usage_has_independent_owner_and_cache_identity() -> None:
    agent = ReActAgent(AgentCard(id="reviewer-card", name="reviewer")).configure(
        ReActAgentConfig(
            model_name="deepseek-chat",
            model_provider="deepseek",
            context_engine_config=ContextEngineConfig(context_window_tokens=1_000),
        )
    )
    team_session = create_agent_team_session(session_id="product-session", team_id="team-alpha")
    member_session = team_session.create_agent_session(
        card=agent.card,
        share_stream_writer=False,
        member_name="reviewer",
    )
    member_session.write_stream = AsyncMock()
    context = await agent.context_engine.create_context(session=member_session)
    ctx = AgentCallbackContext(
        agent=agent,
        inputs=InvokeInputs(query="hello"),
        session=member_session,
        context=context,
    )

    await agent._emit_context_usage(
        ctx,
        ContextWindow(system_messages=[SystemMessage(content="team rules")]),
        phase="pre_call",
    )

    payload = member_session.write_stream.await_args.args[0].payload
    assert payload["product_session_id"] == "product-session"
    assert payload["execution_session_id"] == "product-session"
    assert payload["team_id"] == "team-alpha"
    assert payload["member_name"] == "reviewer"
    assert payload["context_owner_id"].startswith("team-alpha|reviewer|")
    assert payload["cache_identity"].startswith("team:product-session:team:team-alpha:member:")


@pytest.mark.asyncio
async def test_subagent_usage_uses_parent_product_session_but_child_execution_scope() -> None:
    agent = ReActAgent(AgentCard(id="subagent-card", name="subagent")).configure(
        ReActAgentConfig(
            model_name="deepseek-chat",
            model_provider="deepseek",
            context_engine_config=ContextEngineConfig(context_window_tokens=1_000),
        )
    )
    child_session = Session(
        session_id="product-session_sub_task",
        parent_session_id="product-session",
        card=agent.card,
    )
    child_session.write_stream = AsyncMock()
    context = await agent.context_engine.create_context(session=child_session)
    ctx = AgentCallbackContext(
        agent=agent,
        inputs=InvokeInputs(query="hello"),
        session=child_session,
        context=context,
    )

    await agent._emit_context_usage(
        ctx,
        ContextWindow(system_messages=[SystemMessage(content="subagent rules")]),
        phase="pre_call",
    )

    payload = child_session.write_stream.await_args.args[0].payload
    assert payload["session_id"] == "product-session"
    assert payload["product_session_id"] == "product-session"
    assert payload["execution_session_id"] == "product-session_sub_task"
    assert payload["cache_identity"] == "product-session_sub_task"

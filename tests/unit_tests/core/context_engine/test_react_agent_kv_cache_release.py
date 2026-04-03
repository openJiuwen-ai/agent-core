# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Unit tests for ReActAgent KV cache release behavior at the agent entry point.

**Scope**: ReActAgent invoke-stage wiring (context window + invoke kwargs).

Verifies:
- When `enable_kv_cache_release=True` and llm is `InferenceAffinity`:
  `get_context_window(model=llm)` is called, and `llm.invoke/stream` receives
  `session_id` and `enable_cache_sharing=True`.
- When `enable_kv_cache_release=True` but llm is NOT `InferenceAffinity`:
  `get_context_window()` must NOT receive `model=llm`.
- When `enable_kv_cache_release=False`:
  `model` must NOT be passed to `get_context_window()`.

Does NOT cover:
full invoke flow, actual KVCacheManager.release() behavior, or processor logic
(those are in test_kv_cache_manager.py and test_inference_affinity_kv_cache_release_with_processors.py).
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.core.context_engine.base import ContextWindow
from openjiuwen.core.foundation.llm import AssistantMessage
from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig, ModelRequestConfig
from openjiuwen.core.session.agent import Session
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent, ReActAgentConfig
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.core.single_agent.schema.agent_card import AgentCard


pytestmark = pytest.mark.asyncio

_WARNING_SUBSTR = "ContextEngineConfig.enable_kv_cache_release is True"
_KV_NOT_TAKE_EFFECT_SUBSTR = "KV cache release will not take effect."


class _TestableReActAgent(ReActAgent):
    async def call_model_for_test(self, *, ctx: AgentCallbackContext, context, tools=None):
        return await self._call_model(ctx=ctx, context=context, tools=tools)


def _is_kv_cache_release_warning(msg: str) -> bool:
    return _WARNING_SUBSTR in msg and _KV_NOT_TAKE_EFFECT_SUBSTR in msg


@pytest.mark.parametrize("provider", ["OpenAI", "SiliconFlow"])
@pytest.mark.asyncio
async def test_kv_release_warning_once_when_not_supported_and_enabled(caplog, provider):
    """
    When KV cache release is enabled but llm does NOT support KV cache release,
    KV-cache-release warning should be logged once per agent configuration.
    """
    api_bases = {"OpenAI": "https://api.openai.com/v1", "SiliconFlow": "https://api.siliconflow.cn/v1"}
    api_base = api_bases.get(provider)
    assert api_base is not None, f"Unsupported provider: {provider}"
    config = ReActAgentConfig()
    config.configure_model_client(
        provider=provider,
        api_key="test-key",
        api_base=api_base,
        model_name="test-model",
        verify_ssl=False,
    )
    config.configure_context_engine(max_context_message_num=100)
    config.context_engine_config.enable_kv_cache_release = True
    card = AgentCard(name="test", id="test")
    agent = _TestableReActAgent(card=card)
    agent.configure(config)

    session = MagicMock(spec=Session)
    session.get_session_id = MagicMock(return_value="sess-1")
    ctx = AgentCallbackContext(agent=agent, session=session, inputs={}, extra={})

    model_context = MagicMock()
    model_context.get_messages = MagicMock(return_value=[])
    model_context.get_context_window = AsyncMock(
        return_value=ContextWindow(system_messages=[], context_messages=[], tools=[])
    )

    fake_llm = MagicMock()
    fake_llm.supports_kv_cache_release = MagicMock(return_value=False)
    fake_llm.invoke = AsyncMock(return_value=AssistantMessage(content="ok", tool_calls=[]))
    agent.set_llm(fake_llm)
    ctx.context = model_context

    caplog.set_level(logging.WARNING)
    caplog.clear()
    await agent.call_model_for_test(ctx=ctx, context=model_context)
    warnings_1 = [r for r in caplog.records if _is_kv_cache_release_warning(r.message)]
    assert len(warnings_1) == 1, (
        f"Expected exactly one warning for provider={provider}, got: {[r.message for r in caplog.records]}"
    )

    caplog.clear()
    await agent.call_model_for_test(ctx=ctx, context=model_context)
    warnings_2 = [r for r in caplog.records if _is_kv_cache_release_warning(r.message)]
    assert len(warnings_2) == 0, (
        f"Expected warning only once per configuration, got: {[r.message for r in caplog.records]}"
    )


@pytest.mark.asyncio
async def test_kv_release_warning_again_after_switch_provider(caplog):
    """
    After switching top-level provider, if llm does NOT support KV cache release and release enabled,
    KV-cache-release warning should be logged again once for the new provider.
    """
    # Shared ctx/session/context
    session = MagicMock(spec=Session)
    session.get_session_id = MagicMock(return_value="sess-1")
    agent = _TestableReActAgent(card=AgentCard(name="test", id="test"))
    ctx = AgentCallbackContext(agent=agent, session=session, inputs={}, extra={})
    model_context = MagicMock()
    model_context.get_messages = MagicMock(return_value=[])
    model_context.get_context_window = AsyncMock(
        return_value=ContextWindow(system_messages=[], context_messages=[], tools=[])
    )
    ctx.context = model_context

    fake_llm = MagicMock()
    fake_llm.supports_kv_cache_release = MagicMock(return_value=False)
    fake_llm.invoke = AsyncMock(return_value=AssistantMessage(content="ok", tool_calls=[]))

    caplog.set_level(logging.WARNING)
    caplog.clear()

    # OpenAI
    config_openai = ReActAgentConfig()
    config_openai.configure_model_client(
        provider="OpenAI",
        api_key="key1",
        api_base="https://api.openai.com/v1",
        model_name="test-model",
        verify_ssl=False,
    )
    config_openai.configure_context_engine(max_context_message_num=100)
    config_openai.context_engine_config.enable_kv_cache_release = True
    agent.configure(config_openai)
    agent.set_llm(fake_llm)
    await agent.call_model_for_test(ctx=ctx, context=model_context)
    warnings_1 = [r for r in caplog.records if _is_kv_cache_release_warning(r.message)]
    assert len(warnings_1) == 1, (
        f"Expected one warning for OpenAI, got: {[r.message for r in caplog.records]}"
    )

    # Switch to SiliconFlow
    config_sf = ReActAgentConfig()
    config_sf.configure_model_client(
        provider="SiliconFlow",
        api_key="key2",
        api_base="https://api.siliconflow.cn/v1",
        model_name="test-model",
        verify_ssl=False,
    )
    config_sf.configure_context_engine(max_context_message_num=100)
    config_sf.context_engine_config.enable_kv_cache_release = True
    agent.configure(config_sf)
    agent.set_llm(fake_llm)
    caplog.clear()
    await agent.call_model_for_test(ctx=ctx, context=model_context)
    warnings_2 = [r for r in caplog.records if _is_kv_cache_release_warning(r.message)]
    assert len(warnings_2) == 1, (
        f"Expected one warning again after switch, got: {[r.message for r in caplog.records]}"
    )


@pytest.mark.asyncio
async def test_kv_release_no_warning_when_release_disabled(caplog):
    """
    When enable_kv_cache_release=False, KV-cache-release warning must not be logged.
    """
    config = ReActAgentConfig()
    config.configure_model_client(
        provider="OpenAI",
        api_key="test-key",
        api_base="https://api.openai.com/v1",
        model_name="test-model",
        verify_ssl=False,
    )
    config.configure_context_engine(max_context_message_num=100)
    config.context_engine_config.enable_kv_cache_release = False
    card = AgentCard(name="test", id="test")
    agent = _TestableReActAgent(card=card)
    agent.configure(config)

    session = MagicMock(spec=Session)
    session.get_session_id = MagicMock(return_value="sess-1")
    ctx = AgentCallbackContext(agent=agent, session=session, inputs={}, extra={})

    model_context = MagicMock()
    model_context.get_messages = MagicMock(return_value=[])
    model_context.get_context_window = AsyncMock(
        return_value=ContextWindow(system_messages=[], context_messages=[], tools=[])
    )
    ctx.context = model_context
    fake_llm = MagicMock()
    fake_llm.supports_kv_cache_release = MagicMock(return_value=False)
    fake_llm.invoke = AsyncMock(return_value=AssistantMessage(content="ok", tool_calls=[]))
    agent.set_llm(fake_llm)

    caplog.set_level(logging.WARNING)
    caplog.clear()
    await agent.call_model_for_test(ctx=ctx, context=model_context)
    warnings = [r for r in caplog.records if _is_kv_cache_release_warning(r.message)]
    assert len(warnings) == 0, f"Expected no KV-cache-release warning, got: {[r.message for r in caplog.records]}"


@pytest.mark.asyncio
async def test_kv_release_model_passed_when_inference_affinity_and_enabled(caplog):
    """
    When enable_kv_cache_release=True and llm is InferenceAffinity,
    get_context_window receives model=llm and invoke receives session/cache flags.
    """
    from openjiuwen.core.foundation.llm.inference_affinity_model import InferenceAffinityModel

    config = ReActAgentConfig()
    config.configure_model_client(
        provider="InferenceAffinity",
        api_key="dummy-key",
        api_base="http://test:8111",
        model_name="test-model",
        verify_ssl=False,
    )
    config.configure_context_engine(max_context_message_num=100)
    config.context_engine_config.enable_kv_cache_release = True

    agent = _TestableReActAgent(card=AgentCard(name="test", id="test"))
    agent.configure(config)

    affinity_llm = InferenceAffinityModel(
        model_client_config=ModelClientConfig(
            client_id="ut-affinity",
            client_provider="InferenceAffinity",
            api_key="dummy-key",
            api_base="http://test:8111",
            verify_ssl=False,
        ),
        model_config=ModelRequestConfig(model="test-model"),
    )
    affinity_llm.invoke = AsyncMock(return_value=AssistantMessage(content="ok"))
    agent.set_llm(affinity_llm)

    session = MagicMock(spec=Session)
    session.get_session_id = MagicMock(return_value="sess-1")
    ctx = AgentCallbackContext(agent=agent, session=session, inputs={}, extra={})

    mock_window = ContextWindow(system_messages=[], context_messages=[], tools=[])
    mock_model_context = MagicMock()
    mock_model_context.get_messages = MagicMock(return_value=[])
    mock_model_context.get_context_window = AsyncMock(return_value=mock_window)
    ctx.context = mock_model_context

    caplog.set_level(logging.WARNING)
    caplog.clear()
    await agent.call_model_for_test(ctx=ctx, context=mock_model_context)

    call_kwargs = mock_model_context.get_context_window.call_args.kwargs
    assert call_kwargs["model"] is affinity_llm, (
        "When enable_kv_cache_release=True and llm is InferenceAffinity, "
        "model must be passed to get_context_window"
    )

    llm_kwargs = affinity_llm.invoke.call_args.kwargs
    assert llm_kwargs.get("session_id") == "sess-1"
    assert llm_kwargs.get("enable_cache_sharing") is True

    # When using InferenceAffinity, warning must not be emitted.
    warnings = [r for r in caplog.records if _WARNING_SUBSTR in r.message]
    assert len(warnings) == 0, (
        f"Did not expect KV-cache-release warning for InferenceAffinity, got: {[r.message for r in caplog.records]}"
    )

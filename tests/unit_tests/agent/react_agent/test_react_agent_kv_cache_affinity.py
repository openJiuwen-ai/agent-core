# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for ReActAgent Ascend KV-cache affinity wiring."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.core.context_engine.base import ContextWindow, ContextWindowChange
from openjiuwen.core.foundation.kv_cache import KVCacheAffinityConfig
from openjiuwen.core.foundation.llm.schema.message import AssistantMessage, BaseMessage, SystemMessage, UserMessage
from openjiuwen.core.foundation.llm.schema.message_chunk import AssistantMessageChunk
from openjiuwen.core.foundation.tool import ToolInfo
from openjiuwen.core.session.agent import Session
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent, ReActAgentConfig
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ModelCallInputs
from openjiuwen.core.single_agent.schema.agent_card import AgentCard


class _FakeAffinityLLM:
    def __init__(self, *, evict_error: Exception | None = None) -> None:
        self.evict_error = evict_error
        self.evict_calls: list[dict[str, Any]] = []
        self.invoke_kwargs: dict[str, Any] | None = None
        self.stream_kwargs: dict[str, Any] | None = None

    def supports_kv_cache_release(self) -> bool:
        return False

    def supports_kv_cache_affinity(self) -> bool:
        return True

    def build_kv_cache_invoke_kwargs(self, **_: Any) -> dict[str, Any]:
        return {}

    def build_kv_cache_affinity_invoke_kwargs(
            self,
            *,
            session_id: str | None = None,
            parent_session_id: str | None = None,
            enable_kv_cache_affinity: bool = False,
            **_: Any,
    ) -> dict[str, Any]:
        if not enable_kv_cache_affinity:
            return {}
        return {
            "session_id": session_id,
            "parent_session_id": parent_session_id,
        }

    async def evict_kvc(self, **kwargs: Any) -> bool:
        self.evict_calls.append(dict(kwargs))
        if self.evict_error:
            raise self.evict_error
        return True

    async def invoke(self, *args: Any, **kwargs: Any) -> AssistantMessage:
        self.invoke_kwargs = dict(kwargs)
        return AssistantMessage(content="ok")

    async def stream(self, *args: Any, **kwargs: Any):
        self.stream_kwargs = dict(kwargs)
        yield AssistantMessageChunk(content="ok", finish_reason="stop")


class _FakeNonAffinityLLM(_FakeAffinityLLM):
    def supports_kv_cache_affinity(self) -> bool:
        return False

    def build_kv_cache_affinity_invoke_kwargs(self, **_: Any) -> dict[str, Any]:
        return {}


class _FakeContext:
    def __init__(
            self,
            *,
            window: ContextWindow,
            change: ContextWindowChange | None = None,
            session_id: str = "ctx_session",
    ) -> None:
        self.window = window
        self.change = change
        self.detected_windows: list[ContextWindow] = []
        self._session_id = session_id

    async def get_context_window(self, **_: Any) -> ContextWindow:
        return self.window

    def detect_context_window_change(self, window: ContextWindow) -> ContextWindowChange | None:
        self.detected_windows.append(window)
        return self.change

    def session_id(self) -> str:
        return self._session_id


def _msg(role: str, content: str) -> BaseMessage:
    if role == "system":
        return SystemMessage(content=content)
    if role == "assistant":
        return AssistantMessage(content=content)
    return UserMessage(content=content)


def _tool(name: str) -> ToolInfo:
    return ToolInfo(
        name=name,
        description=f"{name} description",
        parameters={"type": "object", "properties": {"value": {"type": "string"}}},
    )


def _window(messages: list[BaseMessage], tools: list[ToolInfo] | None = None) -> ContextWindow:
    return ContextWindow(
        system_messages=[SystemMessage(content="system")],
        context_messages=messages,
        tools=tools or [],
    )


def _agent(enable_affinity: bool = True) -> ReActAgent:
    agent = ReActAgent(card=AgentCard(id="kv_affinity_agent", name="kv_affinity_agent"))
    config = ReActAgentConfig()
    config.model_name = "test-model"
    config.kv_cache_affinity_config = KVCacheAffinityConfig(enable_kv_cache_affinity=enable_affinity)
    agent.configure(config)
    return agent


def _ctx(agent: ReActAgent, session: Session, context: _FakeContext) -> AgentCallbackContext:
    return AgentCallbackContext(
        agent=agent,
        session=session,
        context=context,
        inputs=ModelCallInputs(messages=[], tools=[]),
        extra={},
    )


@pytest.mark.asyncio
async def test_append_only_context_window_does_not_evict_kvc() -> None:
    session = Session(session_id="sess_main")
    context = _FakeContext(window=_window([_msg("user", "q1")]), change=None)
    llm = _FakeAffinityLLM()
    agent = _agent()
    agent.set_llm(llm)

    result = await agent._railed_model_call(_ctx(agent, session, context))

    assert result.content == "ok"
    assert llm.evict_calls == []
    assert llm.invoke_kwargs["session_id"] == "sess_main"
    assert llm.invoke_kwargs["parent_session_id"] == "sess_main"


@pytest.mark.asyncio
async def test_affinity_invoke_adds_session_agent_hint_kwargs() -> None:
    session = Session(session_id="sess_affinity")
    context = _FakeContext(window=_window([]), change=None)
    llm = _FakeAffinityLLM()
    agent = _agent()
    agent.set_llm(llm)

    result = await agent._railed_model_call(_ctx(agent, session, context))

    assert result.content == "ok"
    assert llm.invoke_kwargs["session_id"] == "sess_affinity"
    assert llm.invoke_kwargs["parent_session_id"] == "sess_affinity"


@pytest.mark.asyncio
async def test_affinity_stream_adds_session_agent_hint_kwargs() -> None:
    session = Session(session_id="sess_affinity")
    context = _FakeContext(window=_window([]), change=None)
    llm = _FakeAffinityLLM()
    agent = _agent()
    agent.set_llm(llm)
    ctx = _ctx(agent, session, context)
    ctx.extra["_streaming"] = True

    result = await agent._railed_model_call(ctx)

    assert result.content == "ok"
    assert llm.stream_kwargs["session_id"] == "sess_affinity"
    assert llm.stream_kwargs["parent_session_id"] == "sess_affinity"


@pytest.mark.asyncio
async def test_non_affinity_model_does_not_receive_session_hint_kwargs() -> None:
    session = Session(session_id="sess_affinity")
    context = _FakeContext(window=_window([]), change=None)
    llm = _FakeNonAffinityLLM()
    agent = _agent()
    agent.set_llm(llm)

    result = await agent._railed_model_call(_ctx(agent, session, context))

    assert result.content == "ok"
    assert "session_id" not in llm.invoke_kwargs
    assert "parent_session_id" not in llm.invoke_kwargs


@pytest.mark.asyncio
async def test_affinity_invoke_uses_parent_session_from_child_session_env() -> None:
    session = Session(session_id="child_session", envs={"kv_cache_affinity_parent_session_id": "parent_session"})
    context = _FakeContext(window=_window([]), change=None)
    llm = _FakeAffinityLLM()
    agent = _agent()
    agent.set_llm(llm)

    result = await agent._railed_model_call(_ctx(agent, session, context))

    assert result.content == "ok"
    assert llm.invoke_kwargs["session_id"] == "child_session"
    assert llm.invoke_kwargs["parent_session_id"] == "parent_session"


@pytest.mark.asyncio
async def test_messages_change_triggers_best_effort_messages_evict_before_invoke() -> None:
    session = Session(session_id="sess_sub", envs={"kv_cache_affinity_parent_session_id": "sess_main"})
    old_messages = [_msg("user", "old q"), _msg("assistant", "old a")]
    change = ContextWindowChange(old_messages=old_messages, old_tools=[], msg_start=1, msg_end=2)
    context = _FakeContext(window=_window([_msg("user", "old q"), _msg("assistant", "new a")]), change=change)
    llm = _FakeAffinityLLM()
    agent = _agent()
    agent.set_llm(llm)

    await agent._railed_model_call(_ctx(agent, session, context))

    assert len(llm.evict_calls) == 1
    assert llm.evict_calls[0] == {
        "session_id": "sess_sub",
        "parent_session_id": "sess_main",
        "target": "messages",
        "messages": old_messages,
        "tools": [],
        "model": "test-model",
        "msg_start": 1,
        "msg_end": 2,
    }
    assert llm.invoke_kwargs["session_id"] == "sess_sub"
    assert llm.invoke_kwargs["parent_session_id"] == "sess_main"


@pytest.mark.asyncio
async def test_tools_change_triggers_tools_evict_before_invoke() -> None:
    session = Session(session_id="sess_sub", envs={"kv_cache_affinity_parent_session_id": "sess_main"})
    old_tools = [_tool("old_tool")]
    change = ContextWindowChange(
        old_messages=[_msg("user", "q")],
        old_tools=old_tools,
        tools_start=0,
        tools_end=1,
    )
    context = _FakeContext(window=_window([_msg("user", "q")], tools=[_tool("new_tool")]), change=change)
    llm = _FakeAffinityLLM()
    agent = _agent()
    agent.set_llm(llm)

    await agent._railed_model_call(_ctx(agent, session, context))

    assert len(llm.evict_calls) == 1
    assert llm.evict_calls[0] == {
        "session_id": "sess_sub",
        "parent_session_id": "sess_main",
        "target": "tools",
        "messages": [_msg("user", "q")],
        "tools": old_tools,
        "model": "test-model",
        "tools_start": 0,
        "tools_end": 1,
    }


@pytest.mark.asyncio
async def test_messages_and_tools_change_include_tools_in_messages_evict() -> None:
    session = Session(session_id="sess_sub", envs={"kv_cache_affinity_parent_session_id": "sess_main"})
    old_messages = [_msg("user", "old q"), _msg("assistant", "old a")]
    old_tools = [_tool("old_tool")]
    change = ContextWindowChange(
        old_messages=old_messages,
        old_tools=old_tools,
        msg_start=1,
        msg_end=2,
        tools_start=0,
        tools_end=1,
    )
    context = _FakeContext(window=_window([_msg("user", "old q"), _msg("assistant", "new a")]), change=change)
    llm = _FakeAffinityLLM()
    agent = _agent()
    agent.set_llm(llm)

    await agent._railed_model_call(_ctx(agent, session, context))

    assert len(llm.evict_calls) == 1
    assert llm.evict_calls[0]["target"] == "messages"
    assert llm.evict_calls[0]["msg_start"] == 1
    assert llm.evict_calls[0]["msg_end"] == 2
    assert llm.evict_calls[0]["include_tools"] is True
    assert llm.evict_calls[0]["tools_start"] == 0
    assert llm.evict_calls[0]["tools_end"] == 1


@pytest.mark.asyncio
async def test_kvc_evict_failure_does_not_block_normal_invoke() -> None:
    session = Session(session_id="sess_sub", envs={"kv_cache_affinity_parent_session_id": "sess_main"})
    change = ContextWindowChange(old_messages=[_msg("user", "old")], old_tools=[], msg_start=0, msg_end=1)
    context = _FakeContext(window=_window([_msg("user", "new")]), change=change)
    llm = _FakeAffinityLLM(evict_error=RuntimeError("evict boom"))
    agent = _agent()
    agent.set_llm(llm)

    result = await agent._railed_model_call(_ctx(agent, session, context))

    assert result.content == "ok"
    assert len(llm.evict_calls) == 1
    assert llm.invoke_kwargs["session_id"] == "sess_sub"


@pytest.mark.asyncio
async def test_affinity_disabled_does_not_detect_or_evict_or_add_agent_hint_kwargs() -> None:
    session = Session(session_id="sess_sub", envs={"kv_cache_affinity_parent_session_id": "sess_main"})
    change = ContextWindowChange(old_messages=[_msg("user", "old")], old_tools=[], msg_start=0)
    context = _FakeContext(window=_window([_msg("user", "new")]), change=change)
    llm = _FakeAffinityLLM()
    agent = _agent(enable_affinity=False)
    agent.set_llm(llm)

    await agent._railed_model_call(_ctx(agent, session, context))

    assert context.detected_windows == []
    assert llm.evict_calls == []
    assert "session_id" not in llm.invoke_kwargs
    assert "parent_session_id" not in llm.invoke_kwargs


@pytest.mark.asyncio
async def test_affinity_disabled_does_not_touch_capability_or_lineage() -> None:
    session = MagicMock(spec=Session)
    session.get_session_id.return_value = "sess_disabled"
    session.get_env.side_effect = AssertionError("affinity lineage must remain untouched")
    context = _FakeContext(
        window=_window([_msg("user", "new")]),
        change=ContextWindowChange(
            old_messages=[_msg("user", "old")],
            old_tools=[],
            msg_start=0,
        ),
    )
    llm = MagicMock()
    llm.supports_kv_cache_release.side_effect = AssertionError(
        "release capability must remain untouched"
    )
    llm.supports_kv_cache_affinity.side_effect = AssertionError(
        "affinity capability must remain untouched"
    )
    llm.build_kv_cache_invoke_kwargs.side_effect = AssertionError(
        "release kwargs must remain untouched"
    )
    llm.build_kv_cache_affinity_invoke_kwargs.side_effect = AssertionError(
        "affinity kwargs must remain untouched"
    )
    llm.invoke = AsyncMock(return_value=AssistantMessage(content="ok"))
    agent = _agent(enable_affinity=False)
    agent.set_llm(llm)

    result = await agent._railed_model_call(_ctx(agent, session, context))

    assert result.content == "ok"
    assert context.detected_windows == []
    session.get_env.assert_not_called()
    llm.supports_kv_cache_release.assert_not_called()
    llm.supports_kv_cache_affinity.assert_not_called()
    llm.build_kv_cache_invoke_kwargs.assert_not_called()
    llm.build_kv_cache_affinity_invoke_kwargs.assert_not_called()
    invoke_kwargs = llm.invoke.await_args.kwargs
    assert "session_id" not in invoke_kwargs
    assert "parent_session_id" not in invoke_kwargs


@pytest.mark.asyncio
async def test_non_affinity_llm_does_not_detect_or_evict() -> None:
    session = Session(session_id="sess_sub", envs={"kv_cache_affinity_parent_session_id": "sess_main"})
    change = ContextWindowChange(old_messages=[_msg("user", "old")], old_tools=[], msg_start=0)
    context = _FakeContext(window=_window([_msg("user", "new")]), change=change)
    llm = _FakeNonAffinityLLM()
    agent = _agent()
    agent.set_llm(llm)

    await agent._railed_model_call(_ctx(agent, session, context))

    assert context.detected_windows == []
    assert llm.evict_calls == []
    assert "session_id" not in llm.invoke_kwargs
    assert "parent_session_id" not in llm.invoke_kwargs


def test_kv_cache_affinity_config_rejects_double_enable() -> None:
    with pytest.raises(ValueError, match="cannot both be True"):
        KVCacheAffinityConfig(
            enable_kv_cache_release=True,
            enable_kv_cache_affinity=True,
        )

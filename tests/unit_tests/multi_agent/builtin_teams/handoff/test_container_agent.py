# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Unit tests for ContainerAgent.

Coverage:
1. _build_agent_input  -- no history, dict+history, string+history
2. _strip_handoff_messages -- filtering logic
3. _get_target_agent   -- lazy init, caching
4. invoke()            -- non-HandoffRequest, no coordinator, completion, error path
5. stream()            -- delegates to invoke
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.multi_agent.teams.handoff.container_agent import ContainerAgent
from openjiuwen.core.multi_agent.teams.handoff.handoff_request import HandoffRequest
from openjiuwen.core.single_agent.schema.agent_card import AgentCard


def _card(aid: str) -> AgentCard:
    return AgentCard(id=aid, name=aid, description=f"agent {aid}")


def _make_coordinator():
    coord = MagicMock()
    coord.complete = AsyncMock()
    coord.error = AsyncMock()
    coord.request_handoff = AsyncMock(return_value=True)
    return coord


class TestBuildAgentInput:
    @staticmethod
    def test_no_history_returns_raw_message():
        req = HandoffRequest(input_message="hello", history=[])
        agent = ContainerAgent(_card("a"), lambda: MagicMock(card=_card("a")), [])
        assert getattr(agent, "_build_agent_input")(req) == "hello"

    @staticmethod
    def test_no_history_dict_returned_as_is():
        msg = {"query": "q"}
        req = HandoffRequest(input_message=msg, history=[])
        agent = ContainerAgent(_card("a"), lambda: MagicMock(card=_card("a")), [])
        assert getattr(agent, "_build_agent_input")(req) is msg

    @staticmethod
    def test_dict_message_with_history_merged():
        req = HandoffRequest(
            input_message={"query": "q"},
            history=[{"agent": "a", "output": {}}],
        )
        agent = ContainerAgent(_card("a"), lambda: MagicMock(card=_card("a")), [])
        result = getattr(agent, "_build_agent_input")(req)
        assert result["query"] == "q"
        assert "handoff_history" in result

    @staticmethod
    def test_string_message_with_history_wrapped():
        req = HandoffRequest(
            input_message="hello",
            history=[{"agent": "a", "output": {}}],
        )
        agent = ContainerAgent(_card("a"), lambda: MagicMock(card=_card("a")), [])
        result = getattr(agent, "_build_agent_input")(req)
        assert result["query"] == "hello"
        assert "handoff_history" in result

    @staticmethod
    def test_history_list_passed_through():
        hist = [{"agent": "a", "output": {}}, {"agent": "b", "output": {}}]
        req = HandoffRequest(input_message="x", history=hist)
        agent = ContainerAgent(_card("a"), lambda: MagicMock(card=_card("a")), [])
        result = getattr(agent, "_build_agent_input")(req)
        assert result["handoff_history"] == hist


class TestStripHandoffMessages:
    @staticmethod
    def _make_tool_msg():
        msg = MagicMock()
        msg.role = "tool"
        return msg

    @staticmethod
    def _make_user_msg():
        msg = MagicMock()
        msg.role = "user"
        return msg

    @staticmethod
    def _make_assistant_msg(tool_calls=None):
        from openjiuwen.core.foundation.llm import AssistantMessage
        msg = MagicMock(spec=AssistantMessage)
        msg.role = "assistant"
        msg.tool_calls = tool_calls if tool_calls is not None else []
        return msg

    @staticmethod
    def test_tool_messages_removed():
        msgs = [TestStripHandoffMessages._make_user_msg(), TestStripHandoffMessages._make_tool_msg()]
        strip_fn = getattr(ContainerAgent, "_strip_handoff_messages")
        cleaned = strip_fn(msgs)
        assert all(getattr(m, "role", "") != "tool" for m in cleaned)

    @staticmethod
    def test_assistant_with_tool_calls_removed():
        msgs = [TestStripHandoffMessages._make_assistant_msg(tool_calls=[MagicMock()])]
        strip_fn = getattr(ContainerAgent, "_strip_handoff_messages")
        assert strip_fn(msgs) == []

    @staticmethod
    def test_assistant_without_tool_calls_kept():
        msgs = [TestStripHandoffMessages._make_assistant_msg(tool_calls=[])]
        strip_fn = getattr(ContainerAgent, "_strip_handoff_messages")
        assert len(strip_fn(msgs)) == 1

    @staticmethod
    def test_user_messages_kept():
        msgs = [TestStripHandoffMessages._make_user_msg()]
        strip_fn = getattr(ContainerAgent, "_strip_handoff_messages")
        assert len(strip_fn(msgs)) == 1

    @staticmethod
    def test_empty_list_returns_empty():
        strip_fn = getattr(ContainerAgent, "_strip_handoff_messages")
        assert strip_fn([]) == []

    @staticmethod
    def test_is_static_method():
        assert isinstance(
            ContainerAgent.__dict__["_strip_handoff_messages"], staticmethod
        )


class TestGetTargetAgent:
    @staticmethod
    def test_provider_called_on_first_access():
        calls = []
        mock_agent = MagicMock(card=_card("a"))

        def provider():
            calls.append(1)
            return mock_agent

        agent = ContainerAgent(_card("a"), provider, [])
        result = getattr(agent, "_get_target_agent")()
        assert len(calls) == 1
        assert result is mock_agent

    @staticmethod
    def test_provider_called_only_once():
        calls = []

        def provider():
            calls.append(1)
            return MagicMock(card=_card("a"))

        agent = ContainerAgent(_card("a"), provider, [])
        get_target = getattr(agent, "_get_target_agent")
        get_target()
        get_target()
        assert len(calls) == 1

    @staticmethod
    def test_same_instance_returned():
        mock_agent = MagicMock(card=_card("a"))
        agent = ContainerAgent(_card("a"), lambda: mock_agent, [])
        get_target = getattr(agent, "_get_target_agent")
        assert get_target() is get_target()


class TestContainerAgentInvoke:
    @pytest.mark.asyncio
    async def test_returns_empty_for_non_handoff_request(self):
        agent = ContainerAgent(_card("a"), lambda: MagicMock(card=_card("a")), [])
        assert await agent.invoke(inputs="not a request") == {}

    @pytest.mark.asyncio
    async def test_raises_when_no_coordinator_empty_session(self):
        from openjiuwen.core.common.exception.errors import BaseError
        agent = ContainerAgent(
            _card("a"), lambda: MagicMock(card=_card("a")), [],
            coordinator_lookup=lambda sid: None,
        )
        with pytest.raises(BaseError):
            await agent.invoke(inputs=HandoffRequest(input_message="hi"))

    @pytest.mark.asyncio
    async def test_completes_with_agent_result(self):
        mock_target = MagicMock()
        mock_target.card = _card("a")
        mock_target.invoke = AsyncMock(return_value={"answer": "done"})
        mock_target.stream = AsyncMock(side_effect=NotImplementedError)
        coordinator = _make_coordinator()

        agent = ContainerAgent(
            _card("a"), lambda: mock_target, [],
            coordinator_lookup=lambda sid: coordinator,
        )
        with patch("openjiuwen.core.runner.Runner") as mock_runner:
            mock_runner.resource_mgr.add_tool = MagicMock()
            await agent.invoke(inputs=HandoffRequest(input_message="hi"))

        coordinator.complete.assert_awaited_once_with({"answer": "done"})

    @pytest.mark.asyncio
    async def test_invoke_returns_empty_dict(self):
        mock_target = MagicMock()
        mock_target.card = _card("a")
        mock_target.invoke = AsyncMock(return_value={"ok": True})
        mock_target.stream = AsyncMock(side_effect=NotImplementedError)
        coordinator = _make_coordinator()

        agent = ContainerAgent(
            _card("a"), lambda: mock_target, [],
            coordinator_lookup=lambda sid: coordinator,
        )
        with patch("openjiuwen.core.runner.Runner") as mock_runner:
            mock_runner.resource_mgr.add_tool = MagicMock()
            result = await agent.invoke(inputs=HandoffRequest(input_message="hi"))
        assert result == {}

    @pytest.mark.asyncio
    async def test_error_called_on_agent_exception(self):
        mock_target = MagicMock()
        mock_target.card = _card("a")
        mock_target.invoke = AsyncMock(side_effect=RuntimeError("crash"))
        mock_target.stream = AsyncMock(side_effect=NotImplementedError)
        coordinator = _make_coordinator()

        agent = ContainerAgent(
            _card("a"), lambda: mock_target, [],
            coordinator_lookup=lambda sid: coordinator,
        )
        with patch("openjiuwen.core.runner.Runner") as mock_runner:
            mock_runner.resource_mgr.add_tool = MagicMock()
            await agent.invoke(inputs=HandoffRequest(input_message="hi"))

        coordinator.error.assert_awaited_once()


class TestContainerAgentStream:
    @pytest.mark.asyncio
    async def test_stream_yields_one_chunk(self):
        mock_target = MagicMock()
        mock_target.card = _card("a")
        mock_target.invoke = AsyncMock(return_value={"ok": True})
        mock_target.stream = AsyncMock(side_effect=NotImplementedError)
        coordinator = _make_coordinator()

        agent = ContainerAgent(
            _card("a"), lambda: mock_target, [],
            coordinator_lookup=lambda sid: coordinator,
        )
        with patch("openjiuwen.core.runner.Runner") as mock_runner:
            mock_runner.resource_mgr.add_tool = MagicMock()
            chunks = []
            async for c in agent.stream(inputs=HandoffRequest(input_message="hi")):
                chunks.append(c)
        assert len(chunks) == 1

    @pytest.mark.asyncio
    async def test_stream_non_request_yields_empty(self):
        agent = ContainerAgent(_card("a"), lambda: MagicMock(card=_card("a")), [])
        chunks = [c async for c in agent.stream(inputs="not a request")]
        assert chunks == [{}]

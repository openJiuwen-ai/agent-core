# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Unit tests for BaseGroup - Part 2: Query, Send, Publish, Invoke/Stream."""
import sys
from types import ModuleType
from typing import Any, AsyncIterator, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.multi_agent.config import GroupConfig
from openjiuwen.core.multi_agent.group import BaseGroup
from openjiuwen.core.multi_agent.group_runtime.group_runtime import GroupRuntime
from openjiuwen.core.multi_agent.schema.group_card import GroupCard
from openjiuwen.core.single_agent.schema.agent_card import AgentCard


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class ConcreteGroup(BaseGroup):
    async def invoke(self, message, session=None) -> Any:
        return {"result": "ok", "message": message}

    async def stream(self, message, session=None) -> AsyncIterator[Any]:
        yield {"chunk": message}


def _make_group_card(group_id: str = "g") -> GroupCard:
    return GroupCard(id=group_id, name=group_id, description="")


def _make_agent_card(agent_id: str) -> AgentCard:
    return AgentCard(id=agent_id, name=agent_id, description="")


def _make_runner_module():
    mod = ModuleType("openjiuwen.core.runner")
    mock_rm = MagicMock()
    mock_rm.add_agent.return_value = MagicMock(is_err=lambda: False)
    mock_runner = MagicMock()
    mock_runner.resource_mgr = mock_rm
    mod.Runner = mock_runner
    return mod


def _build_group(config: Optional[GroupConfig] = None) -> ConcreteGroup:
    card = _make_group_card()
    return ConcreteGroup(card=card, config=config)


def _add_agent(group: BaseGroup, agent_id: str) -> AgentCard:
    card = _make_agent_card(agent_id)
    mod = _make_runner_module()
    with patch.dict(
        sys.modules, {"openjiuwen.core.runner": mod}
    ):
        group.add_agent(card, lambda: MagicMock(spec=[]))
    return card


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------

class TestBaseGroupQuery:
    @staticmethod
    def test_list_agents_empty():
        group = _build_group()
        assert group.list_agents() == []

    @staticmethod
    def test_list_agents_returns_all_ids():
        group = _build_group()
        _add_agent(group, "a1")
        _add_agent(group, "a2")
        assert set(group.list_agents()) == {"a1", "a2"}

    @staticmethod
    def test_get_agent_card_returns_card():
        group = _build_group()
        card = _add_agent(group, "a1")
        assert group.get_agent_card("a1") is card

    @staticmethod
    def test_get_agent_card_returns_none_for_unknown():
        group = _build_group()
        assert group.get_agent_card("ghost") is None

    @staticmethod
    def test_get_agent_count():
        group = _build_group()
        assert group.get_agent_count() == 0
        _add_agent(group, "a1")
        assert group.get_agent_count() == 1


# ---------------------------------------------------------------------------
# subscribe / unsubscribe
# ---------------------------------------------------------------------------

class TestBaseGroupSubscription:
    @staticmethod
    @pytest.mark.asyncio
    async def test_subscribe_delegates_to_runtime():
        group = _build_group()
        group.runtime.subscribe = AsyncMock()
        await group.subscribe("agent_a", "events")
        group.runtime.subscribe.assert_awaited_once_with("agent_a", "events")

    @staticmethod
    @pytest.mark.asyncio
    async def test_unsubscribe_delegates_to_runtime():
        group = _build_group()
        group.runtime.unsubscribe = AsyncMock()
        await group.unsubscribe("agent_a", "events")
        group.runtime.unsubscribe.assert_awaited_once_with("agent_a", "events")


# ---------------------------------------------------------------------------
# send
# ---------------------------------------------------------------------------

class TestBaseGroupSend:
    @staticmethod
    @pytest.mark.asyncio
    async def test_send_raises_when_sender_not_in_group():
        """Sender not registered -> AGENT_GROUP_AGENT_NOT_FOUND raised."""
        group = _build_group()
        _add_agent(group, "agent_b")
        with pytest.raises(Exception):
            await group.send(
                message="hello",
                recipient="agent_b",
                sender="unknown_sender",
            )

    @staticmethod
    @pytest.mark.asyncio
    async def test_send_raises_when_recipient_not_in_group():
        """Recipient not registered -> AGENT_GROUP_AGENT_NOT_FOUND raised."""
        group = _build_group()
        _add_agent(group, "agent_a")
        with pytest.raises(Exception):
            await group.send(
                message="hello",
                recipient="unknown_recipient",
                sender="agent_a",
            )

    @staticmethod
    @pytest.mark.asyncio
    async def test_send_delegates_to_runtime():
        group = _build_group()
        _add_agent(group, "agent_a")
        _add_agent(group, "agent_b")
        group.runtime.send = AsyncMock(return_value="pong")

        result = await group.send(
            message="ping",
            recipient="agent_b",
            sender="agent_a",
        )
        assert result == "pong"
        group.runtime.send.assert_awaited_once()

    @staticmethod
    @pytest.mark.asyncio
    async def test_send_passes_session_id_and_timeout():
        group = _build_group()
        _add_agent(group, "agent_a")
        _add_agent(group, "agent_b")
        group.runtime.send = AsyncMock(return_value="ok")

        await group.send(
            message="msg",
            recipient="agent_b",
            sender="agent_a",
            session_id="sess-1",
            timeout=10.0,
        )
        _, kwargs = group.runtime.send.call_args
        assert kwargs["session_id"] == "sess-1"
        assert kwargs["timeout"] == 10.0


# ---------------------------------------------------------------------------
# publish
# ---------------------------------------------------------------------------

class TestBaseGroupPublish:
    @staticmethod
    @pytest.mark.asyncio
    async def test_publish_raises_when_sender_not_in_group():
        group = _build_group()
        with pytest.raises(Exception):
            await group.publish(
                message="event",
                topic_id="events",
                sender="unknown_sender",
            )

    @staticmethod
    @pytest.mark.asyncio
    async def test_publish_delegates_to_runtime():
        group = _build_group()
        _add_agent(group, "agent_a")
        group.runtime.publish = AsyncMock(return_value=None)

        await group.publish(
            message="event",
            topic_id="code_events",
            sender="agent_a",
        )
        group.runtime.publish.assert_awaited_once()

    @staticmethod
    @pytest.mark.asyncio
    async def test_publish_passes_session_id():
        group = _build_group()
        _add_agent(group, "agent_a")
        group.runtime.publish = AsyncMock(return_value=None)

        await group.publish(
            message="evt",
            topic_id="t",
            sender="agent_a",
            session_id="sess-xyz",
        )
        _, kwargs = group.runtime.publish.call_args
        assert kwargs["session_id"] == "sess-xyz"


# ---------------------------------------------------------------------------
# invoke / stream
# ---------------------------------------------------------------------------

class TestBaseGroupInvokeStream:
    @staticmethod
    @pytest.mark.asyncio
    async def test_invoke_returns_result():
        group = _build_group()
        result = await group.invoke("test_message")
        assert result["result"] == "ok"
        assert result["message"] == "test_message"

    @staticmethod
    @pytest.mark.asyncio
    async def test_stream_yields_chunks():
        group = _build_group()
        chunks = []
        async for chunk in group.stream("test_message"):
            chunks.append(chunk)
        assert len(chunks) == 1
        assert chunks[0]["chunk"] == "test_message"

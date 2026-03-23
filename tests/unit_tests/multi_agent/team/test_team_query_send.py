# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Unit tests for BaseTeam - Part 2: Query, Send, Publish, Invoke/Stream."""
import sys
from types import ModuleType
from typing import Any, AsyncIterator, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.multi_agent.config import TeamConfig
from openjiuwen.core.multi_agent.team import BaseTeam
from openjiuwen.core.multi_agent.team_runtime.team_runtime import TeamRuntime
from openjiuwen.core.multi_agent.schema.team_card import TeamCard
from openjiuwen.core.single_agent.schema.agent_card import AgentCard


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class ConcreteTeam(BaseTeam):
    async def invoke(self, message, session=None) -> Any:
        return {"result": "ok", "message": message}

    async def stream(self, message, session=None) -> AsyncIterator[Any]:
        yield {"chunk": message}


def _make_team_card(team_id: str = "g") -> TeamCard:
    return TeamCard(id=team_id, name=team_id, description="")


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


def _build_team(config: Optional[TeamConfig] = None) -> ConcreteTeam:
    card = _make_team_card()
    return ConcreteTeam(card=card, config=config)


def _add_agent(team: BaseTeam, agent_id: str) -> AgentCard:
    card = _make_agent_card(agent_id)
    mod = _make_runner_module()
    with patch.dict(
        sys.modules, {"openjiuwen.core.runner": mod}
    ):
        team.add_agent(card, lambda: MagicMock(spec=[]))
    return card


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------

class TestBaseTeamQuery:
    @staticmethod
    def test_list_agents_empty():
        team = _build_team()
        assert team.list_agents() == []

    @staticmethod
    def test_list_agents_returns_all_ids():
        team = _build_team()
        _add_agent(team, "a1")
        _add_agent(team, "a2")
        assert set(team.list_agents()) == {"a1", "a2"}

    @staticmethod
    def test_get_agent_card_returns_card():
        team = _build_team()
        card = _add_agent(team, "a1")
        assert team.get_agent_card("a1") is card

    @staticmethod
    def test_get_agent_card_returns_none_for_unknown():
        team = _build_team()
        assert team.get_agent_card("ghost") is None

    @staticmethod
    def test_get_agent_count():
        team = _build_team()
        assert team.get_agent_count() == 0
        _add_agent(team, "a1")
        assert team.get_agent_count() == 1


# ---------------------------------------------------------------------------
# subscribe / unsubscribe
# ---------------------------------------------------------------------------

class TestBaseTeamSubscription:
    @staticmethod
    @pytest.mark.asyncio
    async def test_subscribe_delegates_to_runtime():
        team = _build_team()
        team.runtime.subscribe = AsyncMock()
        await team.subscribe("agent_a", "events")
        team.runtime.subscribe.assert_awaited_once_with("agent_a", "events")

    @staticmethod
    @pytest.mark.asyncio
    async def test_unsubscribe_delegates_to_runtime():
        team = _build_team()
        team.runtime.unsubscribe = AsyncMock()
        await team.unsubscribe("agent_a", "events")
        team.runtime.unsubscribe.assert_awaited_once_with("agent_a", "events")


# ---------------------------------------------------------------------------
# send
# ---------------------------------------------------------------------------

class TestBaseTeamSend:
    @staticmethod
    @pytest.mark.asyncio
    async def test_send_raises_when_sender_not_in_team():
        """Sender not registered -> AGENT_TEAM_AGENT_NOT_FOUND raised."""
        team = _build_team()
        _add_agent(team, "agent_b")
        with pytest.raises(Exception):
            await team.send(
                message="hello",
                recipient="agent_b",
                sender="unknown_sender",
            )

    @staticmethod
    @pytest.mark.asyncio
    async def test_send_raises_when_recipient_not_in_team():
        """Recipient not registered -> AGENT_TEAM_AGENT_NOT_FOUND raised."""
        team = _build_team()
        _add_agent(team, "agent_a")
        with pytest.raises(Exception):
            await team.send(
                message="hello",
                recipient="unknown_recipient",
                sender="agent_a",
            )

    @staticmethod
    @pytest.mark.asyncio
    async def test_send_delegates_to_runtime():
        team = _build_team()
        _add_agent(team, "agent_a")
        _add_agent(team, "agent_b")
        team.runtime.send = AsyncMock(return_value="pong")

        result = await team.send(
            message="ping",
            recipient="agent_b",
            sender="agent_a",
        )
        assert result == "pong"
        team.runtime.send.assert_awaited_once()

    @staticmethod
    @pytest.mark.asyncio
    async def test_send_passes_session_id_and_timeout():
        team = _build_team()
        _add_agent(team, "agent_a")
        _add_agent(team, "agent_b")
        team.runtime.send = AsyncMock(return_value="ok")

        await team.send(
            message="msg",
            recipient="agent_b",
            sender="agent_a",
            session_id="sess-1",
            timeout=10.0,
        )
        _, kwargs = team.runtime.send.call_args
        assert kwargs["session_id"] == "sess-1"
        assert kwargs["timeout"] == 10.0


# ---------------------------------------------------------------------------
# publish
# ---------------------------------------------------------------------------

class TestBaseTeamPublish:
    @staticmethod
    @pytest.mark.asyncio
    async def test_publish_raises_when_sender_not_in_team():
        team = _build_team()
        with pytest.raises(Exception):
            await team.publish(
                message="event",
                topic_id="events",
                sender="unknown_sender",
            )

    @staticmethod
    @pytest.mark.asyncio
    async def test_publish_delegates_to_runtime():
        team = _build_team()
        _add_agent(team, "agent_a")
        team.runtime.publish = AsyncMock(return_value=None)

        await team.publish(
            message="event",
            topic_id="code_events",
            sender="agent_a",
        )
        team.runtime.publish.assert_awaited_once()

    @staticmethod
    @pytest.mark.asyncio
    async def test_publish_passes_session_id():
        team = _build_team()
        _add_agent(team, "agent_a")
        team.runtime.publish = AsyncMock(return_value=None)

        await team.publish(
            message="evt",
            topic_id="t",
            sender="agent_a",
            session_id="sess-xyz",
        )
        _, kwargs = team.runtime.publish.call_args
        assert kwargs["session_id"] == "sess-xyz"


# ---------------------------------------------------------------------------
# invoke / stream
# ---------------------------------------------------------------------------

class TestBaseTeamInvokeStream:
    @staticmethod
    @pytest.mark.asyncio
    async def test_invoke_returns_result():
        team = _build_team()
        result = await team.invoke("test_message")
        assert result["result"] == "ok"
        assert result["message"] == "test_message"

    @staticmethod
    @pytest.mark.asyncio
    async def test_stream_yields_chunks():
        team = _build_team()
        chunks = []
        async for chunk in team.stream("test_message"):
            chunks.append(chunk)
        assert len(chunks) == 1
        assert chunks[0]["chunk"] == "test_message"

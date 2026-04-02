# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Unit tests for HandoffTeam.

Coverage:
1. __init__            -- card, config, runtime, internal state
2. add_agent           -- registers agent, returns self, duplicate safe, count
3. _get_start_agent_id -- configured start_agent, defaults to first added
4. _ensure_internal_agents -- idempotent flag, endpoint registration
5. invoke              -- delegates to _run_chain, returns result
6. stream              -- iterates without error
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.multi_agent.teams.handoff.handoff_config import (
    HandoffConfig,
    HandoffTeamConfig,
)
from openjiuwen.core.multi_agent.teams.handoff.handoff_team import HandoffTeam
from openjiuwen.core.multi_agent.schema.team_card import TeamCard
from openjiuwen.core.single_agent.schema.agent_card import AgentCard


def _card(aid: str) -> AgentCard:
    return AgentCard(id=aid, name=aid, description=f"agent {aid}")


def _tcard(tid: str = "team1") -> TeamCard:
    return TeamCard(id=tid, name=tid, description="handoff team")


class _TestableHandoffTeam(HandoffTeam):
    """Test subclass that exposes protected members via public accessors."""

    def get_internal_agents_ready(self) -> bool:
        return self._internal_agents_ready

    def set_internal_agents_ready(self, value: bool) -> None:
        self._internal_agents_ready = value

    def get_coordinator_registry(self) -> dict:
        return self._coordinator_registry

    def get_start_agent_id(self) -> str:
        return self._get_start_agent_id()

    async def ensure_internal_agents(self) -> None:
        await self._ensure_internal_agents()


def _make_team(agents=("a", "b"), tid: str = "team1") -> _TestableHandoffTeam:
    team = _TestableHandoffTeam(card=_tcard(tid))
    for aid in agents:
        team.add_agent(_card(aid), lambda a=aid: MagicMock(card=_card(a)))
    return team


class TestHandoffTeamInit:
    @staticmethod
    def test_card_stored():
        card = _tcard("t1")
        team = HandoffTeam(card=card)
        assert team.card is card

    @staticmethod
    def test_default_config_is_handoff_team_config():
        assert isinstance(HandoffTeam(card=_tcard()).config, HandoffTeamConfig)

    @staticmethod
    def test_custom_config_stored():
        cfg = HandoffTeamConfig()
        team = HandoffTeam(card=_tcard(), config=cfg)
        assert team.config is cfg

    @staticmethod
    def test_runtime_created():
        from openjiuwen.core.multi_agent.team_runtime.team_runtime import TeamRuntime
        assert isinstance(HandoffTeam(card=_tcard()).runtime, TeamRuntime)

    @staticmethod
    def test_internal_agents_not_ready_initially():
        assert _TestableHandoffTeam(card=_tcard()).get_internal_agents_ready() is False

    @staticmethod
    def test_coordinator_registry_empty_initially():
        assert _TestableHandoffTeam(card=_tcard()).get_coordinator_registry() == {}


class TestHandoffTeamAddAgent:
    @staticmethod
    def test_registers_agent_in_runtime():
        team = HandoffTeam(card=_tcard())
        team.add_agent(_card("a"), lambda: MagicMock())
        assert team.runtime.has_agent("a")

    @staticmethod
    def test_returns_self():
        team = HandoffTeam(card=_tcard())
        assert team.add_agent(_card("a"), lambda: MagicMock()) is team

    @staticmethod
    def test_duplicate_agent_no_error():
        team = HandoffTeam(card=_tcard())
        team.add_agent(_card("a"), lambda: MagicMock())
        team.add_agent(_card("a"), lambda: MagicMock())
        assert team.get_agent_count() == 1

    @staticmethod
    def test_add_multiple_agents_increments_count():
        team = HandoffTeam(card=_tcard())
        team.add_agent(_card("a"), lambda: MagicMock())
        team.add_agent(_card("b"), lambda: MagicMock())
        assert team.get_agent_count() == 2

    @staticmethod
    def test_agent_card_appears_in_team_card():
        team = HandoffTeam(card=_tcard())
        team.add_agent(_card("a"), lambda: MagicMock())
        assert "a" in [c.id for c in team.card.agent_cards]

    @staticmethod
    def test_add_agent_resets_internal_ready_flag():
        team = _TestableHandoffTeam(card=_tcard())
        team.set_internal_agents_ready(True)
        team.add_agent(_card("a"), lambda: MagicMock())
        assert team.get_internal_agents_ready() is False

    @staticmethod
    def test_method_chaining():
        team = HandoffTeam(card=_tcard())
        result = team.add_agent(_card("a"), lambda: MagicMock()).add_agent(
            _card("b"), lambda: MagicMock()
        )
        assert result is team
        assert team.get_agent_count() == 2


class TestGetStartAgentId:
    @staticmethod
    def test_uses_configured_start_agent():
        card_a = _card("a")
        cfg = HandoffTeamConfig(handoff=HandoffConfig(start_agent=card_a))
        team = _TestableHandoffTeam(card=_tcard(), config=cfg)
        team.add_agent(card_a, lambda: MagicMock())
        team.add_agent(_card("b"), lambda: MagicMock())
        assert team.get_start_agent_id() == "a"

    @staticmethod
    def test_defaults_to_first_added_agent():
        team = _TestableHandoffTeam(card=_tcard())
        team.add_agent(_card("x"), lambda: MagicMock())
        team.add_agent(_card("y"), lambda: MagicMock())
        assert team.get_start_agent_id() == "x"


class TestEnsureInternalAgents:
    @pytest.mark.asyncio
    async def test_sets_ready_flag(self):
        team = _make_team(agents=("a", "b"))
        await team.ensure_internal_agents()
        assert team.get_internal_agents_ready() is True

    @pytest.mark.asyncio
    async def test_idempotent_second_call_noop(self):
        team = _make_team(agents=("a", "b"))
        await team.ensure_internal_agents()
        await team.ensure_internal_agents()
        assert team.get_internal_agents_ready() is True

    @pytest.mark.asyncio
    async def test_registers_endpoint_agents(self):
        team = _make_team(agents=("a", "b"))
        await team.ensure_internal_agents()
        ep_a = f"__handoff_ep_{team.card.id}_a"
        ep_b = f"__handoff_ep_{team.card.id}_b"
        assert team.runtime.has_agent(ep_a)
        assert team.runtime.has_agent(ep_b)


class TestHandoffTeamInvoke:
    @pytest.mark.asyncio
    async def test_delegates_to_run_chain(self):
        team = _make_team(agents=("a",))
        with patch.object(team, "_run_chain", new=AsyncMock(return_value={"ok": True})) as mock_chain, \
             patch.object(team.runtime, "start", new=AsyncMock()), \
             patch.object(team.runtime, "stop", new=AsyncMock()):
            result = await team.invoke("hello")
        mock_chain.assert_awaited_once()
        assert result == {"ok": True}

    @pytest.mark.asyncio
    async def test_returns_run_chain_result(self):
        team = _make_team(agents=("a",))
        with patch.object(team, "_run_chain", new=AsyncMock(return_value={"answer": 42})), \
             patch.object(team.runtime, "start", new=AsyncMock()), \
             patch.object(team.runtime, "stop", new=AsyncMock()):
            assert await team.invoke("q") == {"answer": 42}

    @pytest.mark.asyncio
    async def test_invoke_with_dict_message(self):
        team = _make_team(agents=("a",))
        with patch.object(team, "_run_chain", new=AsyncMock(return_value="ok")), \
             patch.object(team.runtime, "start", new=AsyncMock()), \
             patch.object(team.runtime, "stop", new=AsyncMock()):
            result = await team.invoke({"query": "hello"})
        assert result == "ok"


class TestHandoffTeamStream:
    @pytest.mark.asyncio
    async def test_stream_completes_without_error(self):
        team = _make_team(agents=("a",))
        with patch.object(team, "_run_chain", new=AsyncMock(return_value={"out": "c"})), \
             patch.object(team.runtime, "start", new=AsyncMock()), \
             patch.object(team.runtime, "stop", new=AsyncMock()):
            chunks = [c async for c in team.stream({"q": "hi"})]
        assert isinstance(chunks, list)

    @pytest.mark.asyncio
    async def test_stream_with_string_message(self):
        team = _make_team(agents=("a",))
        with patch.object(team, "_run_chain", new=AsyncMock(return_value="done")), \
             patch.object(team.runtime, "start", new=AsyncMock()), \
             patch.object(team.runtime, "stop", new=AsyncMock()):
            chunks = [c async for c in team.stream("plain")]
        assert isinstance(chunks, list)

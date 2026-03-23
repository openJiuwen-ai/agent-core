# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Unit tests for BaseTeam - Part 1: Config, Init, Add/Remove Agent."""
import sys
from types import ModuleType
from typing import Any, AsyncIterator, Optional
from unittest.mock import MagicMock, patch

import pytest

from openjiuwen.core.multi_agent.config import TeamConfig
from openjiuwen.core.multi_agent.team import BaseTeam
from openjiuwen.core.multi_agent.team_runtime.team_runtime import TeamRuntime, RuntimeConfig
from openjiuwen.core.multi_agent.schema.team_card import TeamCard
from openjiuwen.core.single_agent.schema.agent_card import AgentCard


# ---------------------------------------------------------------------------
# Concrete subclass
# ---------------------------------------------------------------------------

class ConcreteTeam(BaseTeam):
    async def invoke(self, message, session=None) -> Any:
        return {"result": "ok", "message": message}

    async def stream(self, message, session=None) -> AsyncIterator[Any]:
        yield {"chunk": message}


def _make_team_card(team_id: str = "test_team") -> TeamCard:
    return TeamCard(id=team_id, name=team_id, description="test team")


def _make_agent_card(agent_id: str) -> AgentCard:
    return AgentCard(id=agent_id, name=agent_id, description="test agent")


def _make_runner_module():
    """Return a fake openjiuwen.core.runner module with a mock Runner."""
    mod = ModuleType("openjiuwen.core.runner")
    mock_rm = MagicMock()
    mock_rm.add_agent.return_value = MagicMock(is_err=lambda: False)
    mock_runner = MagicMock()
    mock_runner.resource_mgr = mock_rm
    mod.Runner = mock_runner
    return mod


def _build_team(
    team_id: str = "test_team",
    config: Optional[TeamConfig] = None,
    runtime: Optional[TeamRuntime] = None,
) -> ConcreteTeam:
    card = _make_team_card(team_id)
    return ConcreteTeam(card=card, config=config, runtime=runtime)


def _add_agent(team: BaseTeam, agent_id: str) -> AgentCard:
    """Register a mock agent, injecting Runner via sys.modules."""
    card = _make_agent_card(agent_id)
    mod = _make_runner_module()
    with patch.dict(
        sys.modules, {"openjiuwen.core.runner": mod}
    ):
        team.add_agent(card, lambda: MagicMock(spec=[]))
    return card


# ---------------------------------------------------------------------------
# TeamConfig
# ---------------------------------------------------------------------------

class TestTeamConfig:
    @staticmethod
    def test_default_values():
        cfg = TeamConfig()
        assert cfg.max_agents == 10
        assert cfg.max_concurrent_messages == 100
        assert cfg.message_timeout == 30.0

    @staticmethod
    def test_configure_max_agents_chaining():
        cfg = TeamConfig()
        result = cfg.configure_max_agents(5)
        assert result is cfg
        assert cfg.max_agents == 5

    @staticmethod
    def test_configure_timeout_chaining():
        cfg = TeamConfig()
        result = cfg.configure_timeout(60.0)
        assert result is cfg
        assert cfg.message_timeout == 60.0

    @staticmethod
    def test_configure_concurrency_chaining():
        cfg = TeamConfig()
        result = cfg.configure_concurrency(50)
        assert result is cfg
        assert cfg.max_concurrent_messages == 50


# ---------------------------------------------------------------------------
# BaseTeam init
# ---------------------------------------------------------------------------

class TestBaseTeamInit:
    @staticmethod
    def test_card_stored():
        card = _make_team_card("g1")
        team = ConcreteTeam(card=card)
        assert team.card is card

    @staticmethod
    def test_team_id_taken_from_card_name():
        card = _make_team_card("g1")
        team = ConcreteTeam(card=card)
        assert team.team_id == card.name

    @staticmethod
    def test_default_config_created_when_not_provided():
        team = _build_team()
        assert isinstance(team.config, TeamConfig)

    @staticmethod
    def test_custom_config_stored():
        cfg = TeamConfig(max_agents=3)
        team = _build_team(config=cfg)
        assert team.config.max_agents == 3

    @staticmethod
    def test_default_runtime_created_when_not_provided():
        team = _build_team()
        assert isinstance(team.runtime, TeamRuntime)

    @staticmethod
    def test_custom_runtime_stored():
        runtime = TeamRuntime(config=RuntimeConfig(team_id="custom_rt"))
        team = _build_team(runtime=runtime)
        assert team.runtime is runtime

    @staticmethod
    def test_configure_returns_self():
        team = _build_team()
        cfg = TeamConfig(max_agents=7)
        result = team.configure(cfg)
        assert result is team
        assert team.config.max_agents == 7

    @staticmethod
    def test_base_team_is_abstract():
        with pytest.raises(TypeError):
            BaseTeam(card=_make_team_card())  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# add_agent
# ---------------------------------------------------------------------------

class TestBaseTeamAddAgent:
    @staticmethod
    def test_add_agent_registers_in_runtime():
        team = _build_team()
        _add_agent(team, "agent_a")
        assert team.runtime.has_agent("agent_a")

    @staticmethod
    def test_add_agent_appends_card_to_team_card():
        team = _build_team()
        _add_agent(team, "agent_a")
        ids = [c.id for c in team.card.agent_cards]
        assert "agent_a" in ids

    @staticmethod
    def test_add_agent_returns_self_for_chaining():
        team = _build_team()
        card = _make_agent_card("agent_a")
        mod = _make_runner_module()
        with patch.dict(
            sys.modules, {"openjiuwen.core.runner": mod}
        ):
            result = team.add_agent(card, lambda: MagicMock(spec=[]))
        assert result is team

    @staticmethod
    def test_add_agent_increments_count():
        team = _build_team()
        _add_agent(team, "a1")
        _add_agent(team, "a2")
        assert team.get_agent_count() == 2

    @staticmethod
    def test_add_duplicate_agent_returns_self_with_warning(caplog):
        """Adding a duplicate agent logs a warning and returns self without raising."""
        team = _build_team()
        _add_agent(team, "agent_a")
        
        # Try to add the same agent again
        card = _make_agent_card("agent_a")
        mod = _make_runner_module()
        with patch.dict(
            sys.modules, {"openjiuwen.core.runner": mod}
        ):
            result = team.add_agent(card, lambda: MagicMock(spec=[]))
        
        # Should return self (chaining support)
        assert result is team
        
        # Agent count should still be 1 (not added twice)
        assert team.get_agent_count() == 1

    @staticmethod
    def test_add_agent_beyond_max_raises():
        from openjiuwen.core.common.exception.errors import BaseError
        cfg = TeamConfig(max_agents=2)
        team = _build_team(config=cfg)
        _add_agent(team, "a1")
        _add_agent(team, "a2")
        card = _make_agent_card("a3")
        mod = _make_runner_module()
        with patch.dict(
            sys.modules, {"openjiuwen.core.runner": mod}
        ):
            with pytest.raises(BaseError):
                team.add_agent(card, lambda: MagicMock(spec=[]))


# ---------------------------------------------------------------------------
# remove_agent
# ---------------------------------------------------------------------------

class TestBaseTeamRemoveAgent:
    @staticmethod
    def test_remove_agent_by_id_unregisters_from_runtime():
        team = _build_team()
        _add_agent(team, "agent_a")
        team.remove_agent("agent_a")
        assert not team.runtime.has_agent("agent_a")

    @staticmethod
    def test_remove_agent_by_id_removes_from_card_list():
        team = _build_team()
        _add_agent(team, "agent_a")
        team.remove_agent("agent_a")
        ids = [c.id for c in team.card.agent_cards]
        assert "agent_a" not in ids

    @staticmethod
    def test_remove_agent_returns_self():
        team = _build_team()
        _add_agent(team, "agent_a")
        result = team.remove_agent("agent_a")
        assert result is team

    @staticmethod
    def test_remove_nonexistent_agent_is_safe():
        team = _build_team()
        result = team.remove_agent("ghost")
        assert result is team

    @staticmethod
    def test_remove_agent_by_card():
        """Test removing agent by AgentCard instance"""
        team = _build_team()
        card = _add_agent(team, "agent_a")
        result = team.remove_agent(card)
        assert result is team
        assert not team.runtime.has_agent("agent_a")
        ids = [c.id for c in team.card.agent_cards]
        assert "agent_a" not in ids

    @staticmethod
    def test_remove_agent_by_card_nonexistent_is_safe():
        """Test removing nonexistent agent by AgentCard is safe"""
        team = _build_team()
        card = _make_agent_card("ghost")
        result = team.remove_agent(card)
        assert result is team

# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Unit tests for BaseGroup - Part 1: Config, Init, Add/Remove Agent."""
import sys
from types import ModuleType
from typing import Any, AsyncIterator, Optional
from unittest.mock import MagicMock, patch

import pytest

from openjiuwen.core.multi_agent.config import GroupConfig
from openjiuwen.core.multi_agent.group import BaseGroup
from openjiuwen.core.multi_agent.group_runtime.group_runtime import GroupRuntime, RuntimeConfig
from openjiuwen.core.multi_agent.schema.group_card import GroupCard
from openjiuwen.core.single_agent.schema.agent_card import AgentCard


# ---------------------------------------------------------------------------
# Concrete subclass
# ---------------------------------------------------------------------------

class ConcreteGroup(BaseGroup):
    async def invoke(self, message, session=None) -> Any:
        return {"result": "ok", "message": message}

    async def stream(self, message, session=None) -> AsyncIterator[Any]:
        yield {"chunk": message}


def _make_group_card(group_id: str = "test_group") -> GroupCard:
    return GroupCard(id=group_id, name=group_id, description="test group")


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


def _build_group(
    group_id: str = "test_group",
    config: Optional[GroupConfig] = None,
    runtime: Optional[GroupRuntime] = None,
) -> ConcreteGroup:
    card = _make_group_card(group_id)
    return ConcreteGroup(card=card, config=config, runtime=runtime)


def _add_agent(group: BaseGroup, agent_id: str) -> AgentCard:
    """Register a mock agent, injecting Runner via sys.modules."""
    card = _make_agent_card(agent_id)
    mod = _make_runner_module()
    with patch.dict(
        sys.modules, {"openjiuwen.core.runner": mod}
    ):
        group.add_agent(card, lambda: MagicMock(spec=[]))
    return card


# ---------------------------------------------------------------------------
# GroupConfig
# ---------------------------------------------------------------------------

class TestGroupConfig:
    @staticmethod
    def test_default_values():
        cfg = GroupConfig()
        assert cfg.max_agents == 10
        assert cfg.max_concurrent_messages == 100
        assert cfg.message_timeout == 30.0

    @staticmethod
    def test_configure_max_agents_chaining():
        cfg = GroupConfig()
        result = cfg.configure_max_agents(5)
        assert result is cfg
        assert cfg.max_agents == 5

    @staticmethod
    def test_configure_timeout_chaining():
        cfg = GroupConfig()
        result = cfg.configure_timeout(60.0)
        assert result is cfg
        assert cfg.message_timeout == 60.0

    @staticmethod
    def test_configure_concurrency_chaining():
        cfg = GroupConfig()
        result = cfg.configure_concurrency(50)
        assert result is cfg
        assert cfg.max_concurrent_messages == 50


# ---------------------------------------------------------------------------
# BaseGroup init
# ---------------------------------------------------------------------------

class TestBaseGroupInit:
    @staticmethod
    def test_card_stored():
        card = _make_group_card("g1")
        group = ConcreteGroup(card=card)
        assert group.card is card

    @staticmethod
    def test_group_id_taken_from_card_name():
        card = _make_group_card("g1")
        group = ConcreteGroup(card=card)
        assert group.group_id == card.name

    @staticmethod
    def test_default_config_created_when_not_provided():
        group = _build_group()
        assert isinstance(group.config, GroupConfig)

    @staticmethod
    def test_custom_config_stored():
        cfg = GroupConfig(max_agents=3)
        group = _build_group(config=cfg)
        assert group.config.max_agents == 3

    @staticmethod
    def test_default_runtime_created_when_not_provided():
        group = _build_group()
        assert isinstance(group.runtime, GroupRuntime)

    @staticmethod
    def test_custom_runtime_stored():
        runtime = GroupRuntime(config=RuntimeConfig(group_id="custom_rt"))
        group = _build_group(runtime=runtime)
        assert group.runtime is runtime

    @staticmethod
    def test_configure_returns_self():
        group = _build_group()
        cfg = GroupConfig(max_agents=7)
        result = group.configure(cfg)
        assert result is group
        assert group.config.max_agents == 7

    @staticmethod
    def test_base_group_is_abstract():
        with pytest.raises(TypeError):
            BaseGroup(card=_make_group_card())  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# add_agent
# ---------------------------------------------------------------------------

class TestBaseGroupAddAgent:
    @staticmethod
    def test_add_agent_registers_in_runtime():
        group = _build_group()
        _add_agent(group, "agent_a")
        assert group.runtime.has_agent("agent_a")

    @staticmethod
    def test_add_agent_appends_card_to_group_card():
        group = _build_group()
        _add_agent(group, "agent_a")
        ids = [c.id for c in group.card.agent_cards]
        assert "agent_a" in ids

    @staticmethod
    def test_add_agent_returns_self_for_chaining():
        group = _build_group()
        card = _make_agent_card("agent_a")
        mod = _make_runner_module()
        with patch.dict(
            sys.modules, {"openjiuwen.core.runner": mod}
        ):
            result = group.add_agent(card, lambda: MagicMock(spec=[]))
        assert result is group

    @staticmethod
    def test_add_agent_increments_count():
        group = _build_group()
        _add_agent(group, "a1")
        _add_agent(group, "a2")
        assert group.get_agent_count() == 2

    @staticmethod
    def test_add_duplicate_agent_returns_self_with_warning(caplog):
        """Adding a duplicate agent logs a warning and returns self without raising."""
        group = _build_group()
        _add_agent(group, "agent_a")
        
        # Try to add the same agent again
        card = _make_agent_card("agent_a")
        mod = _make_runner_module()
        with patch.dict(
            sys.modules, {"openjiuwen.core.runner": mod}
        ):
            result = group.add_agent(card, lambda: MagicMock(spec=[]))
        
        # Should return self (chaining support)
        assert result is group
        
        # Agent count should still be 1 (not added twice)
        assert group.get_agent_count() == 1

    @staticmethod
    def test_add_agent_beyond_max_raises():
        from openjiuwen.core.common.exception.errors import BaseError
        cfg = GroupConfig(max_agents=2)
        group = _build_group(config=cfg)
        _add_agent(group, "a1")
        _add_agent(group, "a2")
        card = _make_agent_card("a3")
        mod = _make_runner_module()
        with patch.dict(
            sys.modules, {"openjiuwen.core.runner": mod}
        ):
            with pytest.raises(BaseError):
                group.add_agent(card, lambda: MagicMock(spec=[]))


# ---------------------------------------------------------------------------
# remove_agent
# ---------------------------------------------------------------------------

class TestBaseGroupRemoveAgent:
    @staticmethod
    def test_remove_agent_by_id_unregisters_from_runtime():
        group = _build_group()
        _add_agent(group, "agent_a")
        group.remove_agent("agent_a")
        assert not group.runtime.has_agent("agent_a")

    @staticmethod
    def test_remove_agent_by_id_removes_from_card_list():
        group = _build_group()
        _add_agent(group, "agent_a")
        group.remove_agent("agent_a")
        ids = [c.id for c in group.card.agent_cards]
        assert "agent_a" not in ids

    @staticmethod
    def test_remove_agent_returns_self():
        group = _build_group()
        _add_agent(group, "agent_a")
        result = group.remove_agent("agent_a")
        assert result is group

    @staticmethod
    def test_remove_nonexistent_agent_is_safe():
        group = _build_group()
        result = group.remove_agent("ghost")
        assert result is group

    @staticmethod
    def test_remove_agent_by_card():
        """Test removing agent by AgentCard instance"""
        group = _build_group()
        card = _add_agent(group, "agent_a")
        result = group.remove_agent(card)
        assert result is group
        assert not group.runtime.has_agent("agent_a")
        ids = [c.id for c in group.card.agent_cards]
        assert "agent_a" not in ids

    @staticmethod
    def test_remove_agent_by_card_nonexistent_is_safe():
        """Test removing nonexistent agent by AgentCard is safe"""
        group = _build_group()
        card = _make_agent_card("ghost")
        result = group.remove_agent(card)
        assert result is group

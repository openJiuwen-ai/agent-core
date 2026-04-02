# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Unit tests for HandoffRoute, HandoffConfig, and HandoffTeamConfig.

Coverage:
1. HandoffRoute -- frozen dataclass, field values, immutability
2. HandoffConfig -- defaults, custom values, type constraints
3. HandoffTeamConfig -- inheritance, default handoff field, arbitrary types, extra fields
"""
from __future__ import annotations

import pytest

from openjiuwen.core.multi_agent.teams.handoff.handoff_config import (
    HandoffConfig,
    HandoffRoute,
    HandoffTeamConfig,
)
from openjiuwen.core.multi_agent.config import TeamConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _agent_card(aid: str) -> AgentCard:
    return AgentCard(id=aid, name=aid, description=f"agent {aid}")


def _always_true(_c):
    """Termination condition that always returns True."""
    return True


def _always_false(_c):
    """Termination condition that always returns False."""
    return False


# ---------------------------------------------------------------------------
# 1. HandoffRoute
# ---------------------------------------------------------------------------

class TestHandoffRoute:
    @staticmethod
    def test_source_and_target_stored():
        route = HandoffRoute(source="a", target="b")
        assert route.source == "a"
        assert route.target == "b"

    @staticmethod
    def test_frozen_prevents_source_mutation():
        route = HandoffRoute(source="a", target="b")
        with pytest.raises((AttributeError, TypeError)):
            route.source = "x"  # type: ignore[misc]

    @staticmethod
    def test_frozen_prevents_target_mutation():
        route = HandoffRoute(source="a", target="b")
        with pytest.raises((AttributeError, TypeError)):
            route.target = "y"  # type: ignore[misc]

    @staticmethod
    def test_equality_based_on_values():
        r1 = HandoffRoute(source="a", target="b")
        r2 = HandoffRoute(source="a", target="b")
        assert r1 == r2

    @staticmethod
    def test_inequality_different_source():
        r1 = HandoffRoute(source="a", target="b")
        r2 = HandoffRoute(source="x", target="b")
        assert r1 != r2

    @staticmethod
    def test_inequality_different_target():
        r1 = HandoffRoute(source="a", target="b")
        r2 = HandoffRoute(source="a", target="z")
        assert r1 != r2

    @staticmethod
    def test_hashable_usable_in_set():
        r = HandoffRoute(source="a", target="b")
        s = {r}
        assert r in s


# ---------------------------------------------------------------------------
# 2. HandoffConfig
# ---------------------------------------------------------------------------

class TestHandoffConfig:
    @staticmethod
    def test_default_max_handoffs():
        assert HandoffConfig().max_handoffs == 10

    @staticmethod
    def test_default_routes_empty_list():
        cfg = HandoffConfig()
        assert cfg.routes == []

    @staticmethod
    def test_default_start_agent_is_none():
        assert HandoffConfig().start_agent is None

    @staticmethod
    def test_default_termination_condition_is_none():
        assert HandoffConfig().termination_condition is None

    @staticmethod
    def test_custom_max_handoffs():
        cfg = HandoffConfig(max_handoffs=5)
        assert cfg.max_handoffs == 5

    @staticmethod
    def test_custom_start_agent():
        card = _agent_card("start")
        cfg = HandoffConfig(start_agent=card)
        assert cfg.start_agent is card

    @staticmethod
    def test_custom_routes():
        routes = [HandoffRoute("a", "b"), HandoffRoute("b", "c")]
        cfg = HandoffConfig(routes=routes)
        assert cfg.routes == routes

    @staticmethod
    def test_custom_termination_condition():
        cfg = HandoffConfig(termination_condition=_always_true)
        assert cfg.termination_condition is _always_true

    @staticmethod
    def test_max_handoffs_zero_allowed():
        cfg = HandoffConfig(max_handoffs=0)
        assert cfg.max_handoffs == 0

    @staticmethod
    def test_routes_list_is_independent_per_instance():
        cfg1 = HandoffConfig()
        cfg2 = HandoffConfig()
        cfg1.routes.append(HandoffRoute("a", "b"))
        assert cfg2.routes == []

    @staticmethod
    def test_start_agent_id_accessible():
        card = _agent_card("entry")
        cfg = HandoffConfig(start_agent=card)
        assert cfg.start_agent.id == "entry"


# ---------------------------------------------------------------------------
# 3. HandoffTeamConfig
# ---------------------------------------------------------------------------

class TestHandoffTeamConfig:
    @staticmethod
    def test_inherits_team_config():
        assert isinstance(HandoffTeamConfig(), TeamConfig)

    @staticmethod
    def test_default_handoff_is_handoff_config_instance():
        cfg = HandoffTeamConfig()
        assert isinstance(cfg.handoff, HandoffConfig)

    @staticmethod
    def test_default_handoff_max_handoffs():
        assert HandoffTeamConfig().handoff.max_handoffs == 10

    @staticmethod
    def test_custom_handoff_config():
        hc = HandoffConfig(max_handoffs=3)
        cfg = HandoffTeamConfig(handoff=hc)
        assert cfg.handoff.max_handoffs == 3

    @staticmethod
    def test_team_config_defaults_preserved():
        cfg = HandoffTeamConfig()
        assert cfg.max_agents == 10
        assert cfg.max_concurrent_messages == 100
        assert cfg.message_timeout == 30.0

    @staticmethod
    def test_arbitrary_types_allowed_for_callable():
        hc = HandoffConfig(termination_condition=_always_false)
        cfg = HandoffTeamConfig(handoff=hc)
        assert cfg.handoff.termination_condition is _always_false

    @staticmethod
    def test_configure_max_agents_chaining():
        cfg = HandoffTeamConfig()
        result = cfg.configure_max_agents(5)
        assert result is cfg
        assert cfg.max_agents == 5

    @staticmethod
    def test_configure_timeout_chaining():
        cfg = HandoffTeamConfig()
        result = cfg.configure_timeout(60.0)
        assert result is cfg
        assert cfg.message_timeout == 60.0

    @staticmethod
    def test_configure_concurrency_chaining():
        cfg = HandoffTeamConfig()
        result = cfg.configure_concurrency(50)
        assert result is cfg
        assert cfg.max_concurrent_messages == 50

    @staticmethod
    def test_handoff_config_with_routes():
        routes = [HandoffRoute("a", "b")]
        hc = HandoffConfig(routes=routes)
        cfg = HandoffTeamConfig(handoff=hc)
        assert len(cfg.handoff.routes) == 1
        assert cfg.handoff.routes[0].source == "a"

    @staticmethod
    def test_extra_fields_allowed():
        cfg = HandoffTeamConfig(custom_extra="value")
        assert cfg.custom_extra == "value"

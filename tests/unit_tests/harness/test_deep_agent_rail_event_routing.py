# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Rail callback events must be routed to the agent that actually fires them.

A DeepAgent owns two callback-manager namespaces: its own, and the inner
ReActAgent's. A rail registered on the DeepAgent has each of its callbacks
routed to one of them, and routing to the wrong one is silent — the callback
simply never runs.
"""

from __future__ import annotations

import pytest

from openjiuwen.core.single_agent.rail.base import AgentCallbackEvent
from openjiuwen.harness.deep_agent import (
    _BRIDGE_EVENTS,
    _DEEP_EVENTS,
    _OUTER_ONLY_EVENTS,
)


@pytest.mark.level0
def test_every_event_has_a_routing_decision() -> None:
    """A new event with no routing lands on the outer agent and never fires.

    ``_register_rail_selective`` falls back to the outer DeepAgent with a log
    warning, which for an inner-fired event means the rail is silently dead.
    Adding an event therefore has to come with a routing decision, and this
    assertion is what forces it.
    """
    routed = _BRIDGE_EVENTS | _OUTER_ONLY_EVENTS | _DEEP_EVENTS
    unrouted = set(AgentCallbackEvent) - routed
    assert not unrouted, f"events with no routing decision: {sorted(e.value for e in unrouted)}"


@pytest.mark.level0
def test_routing_sets_do_not_overlap() -> None:
    """One event, one destination — otherwise registration order decides."""
    assert not _BRIDGE_EVENTS & _OUTER_ONLY_EVENTS
    assert not _BRIDGE_EVENTS & _DEEP_EVENTS
    assert not _OUTER_ONLY_EVENTS & _DEEP_EVENTS


@pytest.mark.level0
def test_inner_fired_events_are_bridged() -> None:
    """These are fired by the inner ReActAgent, so they must bridge to it."""
    for event in (
        AgentCallbackEvent.BEFORE_MODEL_CALL,
        AgentCallbackEvent.AFTER_REACT_ITERATION,
        AgentCallbackEvent.ON_USER_MESSAGE,
    ):
        assert event in _BRIDGE_EVENTS

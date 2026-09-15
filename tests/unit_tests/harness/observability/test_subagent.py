# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The dispatch hook that gives every sub-agent its own agent-tier rail."""

from __future__ import annotations

from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.observability.rail import AgentObservabilityRail
from openjiuwen.harness.observability.subagent import (
    attach_subagent_observability,
    install_subagent_observability_hook,
)


class _Subagent:
    """DeepAgent stand-in exposing just the rail API the hook touches."""

    def __init__(self) -> None:
        self.rails: list[object] = []

    def configured_rails(self) -> list[object]:
        """Return the rails currently mounted on this agent."""
        return list(self.rails)

    def add_rail(self, rail: object) -> None:
        """Mount one rail."""
        self.rails.append(rail)


def test_subagent_hook_traces_every_dispatch_path(monkeypatch) -> None:
    """Any sub-agent created through create_subagent gets an observability rail.

    The builtin ``task_tool`` creates its sub-agent inside the SDK, so only a
    hook at creation reaches it — attaching from a single dispatching tool
    leaves every other path untraced.
    """
    created = _Subagent()
    monkeypatch.setattr(
        DeepAgent, "create_subagent", lambda self, *args, **kwargs: created, raising=False
    )
    monkeypatch.setattr(
        "openjiuwen.harness.observability.rail.maybe_agent_observability_rail",
        AgentObservabilityRail,
    )

    install_subagent_observability_hook()
    returned = DeepAgent.create_subagent(object(), "explore_agent", "sess-1")

    assert returned is created
    assert sum(isinstance(rail, AgentObservabilityRail) for rail in created.rails) == 1

    # Idempotent: re-installing must not stack wrappers, and a second creation
    # must not add a second rail.
    install_subagent_observability_hook()
    DeepAgent.create_subagent(object(), "explore_agent", "sess-1")

    assert sum(isinstance(rail, AgentObservabilityRail) for rail in created.rails) == 1


def test_subagent_gets_no_rail_while_observability_is_off(monkeypatch) -> None:
    """The rail guard returns None before the provider is up."""
    monkeypatch.setattr(
        "openjiuwen.harness.observability.rail.maybe_agent_observability_rail",
        lambda: None,
    )
    created = _Subagent()
    monkeypatch.setattr(
        DeepAgent, "create_subagent", lambda self, *args, **kwargs: created, raising=False
    )

    install_subagent_observability_hook()
    DeepAgent.create_subagent(object(), "explore_agent", "sess-1")

    assert created.rails == []


def test_subagent_that_already_has_a_rail_is_left_alone(monkeypatch) -> None:
    """A second rail would double every span the sub-agent emits."""
    monkeypatch.setattr(
        "openjiuwen.harness.observability.rail.maybe_agent_observability_rail",
        AgentObservabilityRail,
    )
    subagent = _Subagent()
    subagent.add_rail(AgentObservabilityRail())

    attach_subagent_observability(subagent)

    assert sum(isinstance(rail, AgentObservabilityRail) for rail in subagent.rails) == 1

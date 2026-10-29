# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The full seeding chain: manager → spec extras → derive → rail factories.

Covers the leader assembly end to end (the contract test for the wiring)
and the inprocess teammate channel: ``inprocess_spawn`` configures the
teammate with the leader's very spec object, so the bus seeded into
``spec.build_context.extras`` reaches the teammate's rail factories through
the same per-member derive.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from openjiuwen.agent_teams.extensions.events import register_team_event_types
from openjiuwen.agent_teams.rails.team_context import (
    TeamHandleKey,
    get_extension_bus,
)
from openjiuwen.agent_teams.rails.team_policy_rail import TeamPolicyRail
from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager
from openjiuwen.agent_teams.runtime.pool import ActiveTeam
from openjiuwen.agent_teams.schema.blueprint import DeepAgentSpec, TeamAgentSpec
from openjiuwen.core.events import Event, ExtensionEventBus
from openjiuwen.core.events.types import OPS_BUILD_EVENT_TYPE
from openjiuwen.harness.schema.build_context import BuildContext


async def _ready_bus() -> ExtensionEventBus:
    """Create a host-initialized bus with the team events registered."""
    bus = ExtensionEventBus()
    register_team_event_types(bus)
    await bus.trigger(Event(OPS_BUILD_EVENT_TYPE, None))
    return bus


def _seeded_spec(bus: ExtensionEventBus) -> TeamAgentSpec:
    """Build a minimal spec the way the manager seeds it before building.

    Hosts hand the spec its BuildContext (the manager seeds the bus into
    ``build_context.extras``); the default spec has none and stays unwired.
    """
    spec = TeamAgentSpec(
        agents={"leader": DeepAgentSpec()},
        team_name="research-team",
        build_context=BuildContext(language="cn"),
    )
    manager = TeamRuntimeManager()
    manager.set_extension_bus(bus)
    manager._inject_extension_bus(spec)
    return spec


def _policy_rails(harness: Any) -> list[TeamPolicyRail]:
    """Collect TeamPolicyRail instances from the harness's rail queues."""
    native = harness._native
    return [
        rail
        for rail in (*native._pending_rails, *native._registered_rails)
        if isinstance(rail, TeamPolicyRail)
    ]


@pytest.mark.asyncio
async def test_leader_spec_build_reaches_the_policy_rail() -> None:
    """Manager seeding → spec.build() → the leader's rail defers its build."""
    bus = await _ready_bus()
    spec = _seeded_spec(bus)
    agent = spec.build()

    rails = _policy_rails(agent.harness)
    assert len(rails) == 1
    rail = rails[0]
    assert rail._extension_bus is bus
    # Bus-wired rail: sections deferred to the first model call.
    assert rail._static_sections == []
    assert rail._policy_sections_ready is False
    # The build target the rail kept matches the team it was seeded for.
    assert rail._policy_build_input.team_name == "research-team"


@pytest.mark.asyncio
async def test_derive_carries_the_bus_to_inprocess_teammates() -> None:
    """inprocess_spawn configures the teammate with the leader's spec — the
    bus rides into the member factories through the per-member derive."""
    bus = await _ready_bus()
    spec = _seeded_spec(bus)
    assert spec.build_context is not None
    assert spec.build_context.extras[TeamHandleKey.EXTENSION_BUS] is bus

    # What the configurator does per member: derive + decoupled extras copy.
    member_context = spec.build_context.derive(
        member_name="researcher",
        role="teammate",
    )
    member_context.extras = dict(member_context.extras)
    assert get_extension_bus(member_context) is bus

    # And what the rail factory then sees for that member.
    from openjiuwen.agent_teams.rails.elements import build_team_policy_rail

    rail = build_team_policy_rail(
        {"member_name": "researcher", "role": "teammate", "language": "cn"},
        member_context,
    )
    assert rail._extension_bus is bus
    assert rail._static_sections == []


@pytest.mark.asyncio
async def test_unseeded_spec_builds_the_rail_locally() -> None:
    """Without a bus the same chain builds the sections at construction."""
    spec = TeamAgentSpec(agents={"leader": DeepAgentSpec()}, team_name="research-team")
    agent = spec.build()

    rails = _policy_rails(agent.harness)
    assert len(rails) == 1
    rail = rails[0]
    assert rail._extension_bus is None
    assert rail._static_sections != []
    assert rail._policy_sections_ready is True


# ---------------------------------------------------------------------------
# Host-facing wiring points: the Runner facade and the lazy resolver
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_runner_facade_injects_the_bus() -> None:
    """Runner.set_team_extension_bus reaches the manager's holders."""
    from openjiuwen.core.runner.runner import GLOBAL_RUNNER
    from openjiuwen.core.runner.team_runner import _TeamRunnerClassMixin

    assert hasattr(_TeamRunnerClassMixin, "set_team_extension_bus")
    manager = GLOBAL_RUNNER._get_team_runtime_manager()
    assert manager.extension_bus is None

    from openjiuwen.core.runner import Runner

    bus = await _ready_bus()
    Runner.set_team_extension_bus(bus)
    assert manager.extension_bus is bus
    Runner.set_team_extension_bus(None)
    assert manager.extension_bus is None


@pytest.mark.asyncio
async def test_global_runner_pool_resolver_is_lazy() -> None:
    """The lazy resolver reads the manager slot at resolve time only."""
    from openjiuwen.agent_teams.extensions.resolver import (
        BackendTeamReadView,
        GlobalRunnerPoolResolver,
    )
    from openjiuwen.core.runner.runner import GLOBAL_RUNNER

    resolver = GlobalRunnerPoolResolver()
    # A stub pool entry shaped like the structural protocol.
    entry = ActiveTeam(
        team_name="research-team",
        agent=_StubAgent(team_backend=_StubBuildBackend()),  # type: ignore[arg-type]
        current_session_id="session-1",
    )
    real_manager = GLOBAL_RUNNER._get_team_runtime_manager()
    await real_manager.pool.add(entry)
    try:
        view = await resolver.resolve("session-1", "research-team")
        assert isinstance(view, BackendTeamReadView)
        assert await resolver.resolve("session-1", "other-team") is None
        assert await resolver.resolve("other-session", "research-team") is None
        assert await resolver.resolve(None, "research-team") is None
    finally:
        await real_manager.pool.remove("research-team")


@dataclass
class _StubBuildBackend:
    """Structural backend: the view reads db and team_name (db unused here)."""

    team_name: str = "research-team"
    db: Any = None


@dataclass
class _StubAgent:
    """Structural agent holding a stub backend."""

    team_backend: Any

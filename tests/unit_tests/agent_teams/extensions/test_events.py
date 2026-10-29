# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""End-to-end team monitor events on the real ExtensionEventBus.

Covers the host wiring order, the CORE result, short-circuit semantics
and the pattern filter at the chain entry.
"""
from __future__ import annotations

import pytest

from openjiuwen.agent_teams.extensions.events import (
    TEAM_MONITOR_EVENT,
    register_team_event_types,
)
from openjiuwen.agent_teams.extensions.models import (
    TeamMonitorEventInput,
    TeamMonitorEventOutput,
)
from openjiuwen.agent_teams.monitor.models import MonitorEvent, MonitorEventType
from openjiuwen.agent_teams.prompts.loader import load_template
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.events import Event, ExtensionEventBus, HandlerLevel, SessionContext
from openjiuwen.core.events.types import OPS_BUILD_EVENT_TYPE
from openjiuwen.core.single_agent.prompts.builder import PromptSection
from openjiuwen.extensions.ops_build_handlers import team as team_ops


class _NullResolver:
    """Resolver double: no team is accessible (not used by these tests)."""

    async def resolve(self, session_id: str | None, team_name: str):
        return None


async def _ready_bus() -> ExtensionEventBus:
    """Create a bus with the team events and ops namespace built."""
    bus = ExtensionEventBus()
    register_team_event_types(bus)
    team_ops.register(bus, _NullResolver())
    await bus.trigger(Event(OPS_BUILD_EVENT_TYPE, None))
    return bus


def _monitor_input() -> TeamMonitorEventInput:
    return TeamMonitorEventInput(
        team_name="research-team",
        member_name="researcher",
        monitor_event=MonitorEvent(
            event_type=MonitorEventType.TASK_CREATED,
            team_name="research-team",
            member_name="researcher",
            task_id="t1",
            timestamp=1,
        ),
    )


@pytest.mark.asyncio
async def test_monitor_event_core_runs_without_handlers() -> None:
    """With no registered handler the chain lands in the CORE output."""
    bus = await _ready_bus()
    output = await bus.trigger(Event(TEAM_MONITOR_EVENT, _monitor_input()), SessionContext(session_id="s1"))
    assert isinstance(output, TeamMonitorEventOutput)


@pytest.mark.asyncio
async def test_monitor_event_user_short_circuit_returns_none() -> None:
    """A short-circuited notification chain returns None — no CORE run."""

    async def swallow(_event, _ops, _next_handler):
        return None

    bus = await _ready_bus()
    bus.register_event_handler(TEAM_MONITOR_EVENT, swallow, match_pattern={})
    output = await bus.trigger(Event(TEAM_MONITOR_EVENT, _monitor_input()), SessionContext(session_id="s1"))
    assert output is None


@pytest.mark.asyncio
async def test_monitor_event_pattern_filters_at_chain_entry() -> None:
    """A handler whose pattern misses never runs; the CORE tail still does."""
    ran = False

    async def never_runs(_event, _ops, _next_handler):
        nonlocal ran
        ran = True
        return None

    bus = await _ready_bus()
    bus.register_event_handler(
        TEAM_MONITOR_EVENT,
        never_runs,
        match_pattern={"team_name": "other-team"},
    )
    output = await bus.trigger(Event(TEAM_MONITOR_EVENT, _monitor_input()), SessionContext(session_id="s1"))
    assert ran is False
    assert isinstance(output, TeamMonitorEventOutput)

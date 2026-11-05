# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Host wiring for the monitor event: the bridge lifecycle on the runtime."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from openjiuwen.agent_teams.extensions.events import (
    TEAM_MONITOR_EVENT,
    build_monitor_event_dispatcher,
    register_team_event_types,
)
from openjiuwen.agent_teams.extensions.models import TeamMonitorEventInput
from openjiuwen.agent_teams.extensions.monitor_event_bridge import TeamMonitorEventBridge
from openjiuwen.agent_teams.monitor.models import MonitorEvent, MonitorEventType
from openjiuwen.agent_teams.runtime.manager import TeamRuntimeManager
from openjiuwen.agent_teams.runtime.pool import ActiveTeam
from openjiuwen.core.events import Event, ExtensionEventBus, HandlerLevel
from openjiuwen.core.events.types import OPS_BUILD_EVENT_TYPE


async def _ready_bus() -> ExtensionEventBus:
    """Create a host-initialized bus with the team events registered."""
    bus = ExtensionEventBus()
    register_team_event_types(bus)
    await bus.trigger(Event(OPS_BUILD_EVENT_TYPE, None))
    return bus


def _monitor_event() -> MonitorEvent:
    return MonitorEvent(
        event_type=MonitorEventType.TASK_CREATED,
        team_name="research-team",
        member_name="researcher",
        task_id="t1",
        timestamp=1,
    )


@dataclass
class _StubAgent:
    """Structural stand-in for the leader TeamAgent of one pool entry."""

    listeners: list[Any] = field(default_factory=list)
    lifecycle: str = "temporary"
    stopped: bool = False

    def add_event_listener(self, handler: Any) -> None:
        self.listeners.append(handler)

    def remove_event_listener(self, handler: Any) -> None:
        self.listeners.remove(handler)

    async def stop_coordination(self) -> None:
        self.stopped = True

    async def is_shutdown_requested(self) -> bool:
        return True



@pytest.mark.asyncio
async def test_dispatcher_runs_the_monitor_event_chain() -> None:
    """The dispatcher wraps the event, triggers, and delivers the input."""
    seen: list[TeamMonitorEventInput] = []

    async def record(event, _ops, next_handler):
        seen.append(event.event_input)
        return await next_handler(event)

    bus = await _ready_bus()
    bus.register_event_handler(TEAM_MONITOR_EVENT, record, match_pattern={})
    dispatcher = build_monitor_event_dispatcher(bus)

    await dispatcher(_monitor_event(), "session-1")
    assert len(seen) == 1
    assert seen[0].monitor_event.task_id == "t1"


@pytest.mark.asyncio
async def test_dispatcher_swallows_trigger_faults() -> None:
    """A trigger failure is logged and dropped, never propagated to the bridge."""
    bus = await _ready_bus()
    bus.unregister_event_type(TEAM_MONITOR_EVENT)  # unknown event type now
    dispatcher = build_monitor_event_dispatcher(bus)
    await dispatcher(_monitor_event(), "session-1")  # must not raise


# ---------------------------------------------------------------------------
# Manager wiring: bus holder + bridge mount/dismount
# ---------------------------------------------------------------------------


@pytest.mark.level0
def test_manager_holds_extension_bus() -> None:
    """The bus is optional state on the manager with a setter."""
    manager = TeamRuntimeManager()
    assert manager.extension_bus is None
    bus = ExtensionEventBus()
    manager.set_extension_bus(bus)
    assert manager.extension_bus is bus
    manager.set_extension_bus(None)
    assert manager.extension_bus is None


@pytest.mark.asyncio
async def test_bridge_mounted_only_with_bus() -> None:
    """No bus means no bridge and no listener; a bus mounts and starts one."""
    manager = TeamRuntimeManager()
    agent = _StubAgent()
    assert await manager._start_monitor_event_bridge(agent, "research-team", "session-1") is None
    assert agent.listeners == []

    manager.set_extension_bus(await _ready_bus())
    bridge = await manager._start_monitor_event_bridge(agent, "research-team", "session-1")
    assert isinstance(bridge, TeamMonitorEventBridge)
    assert bridge.is_running
    assert agent.listeners == [bridge.on_internal_event]
    await bridge.stop()


@pytest.mark.asyncio
async def test_stop_team_dismounts_bridge() -> None:
    """stop_team stops the bridge, detaches the listener, clears the entry."""
    manager = TeamRuntimeManager()
    agent = _StubAgent()
    manager.set_extension_bus(await _ready_bus())
    bridge = await manager._start_monitor_event_bridge(agent, "research-team", "session-1")
    await manager.pool.add(
        ActiveTeam(
            team_name="research-team",
            agent=agent,  # type: ignore[arg-type]
            current_session_id="session-1",
            monitor_event_bridge=bridge,
        ),
    )

    assert await manager.stop_team(team_name="research-team", session_id="session-1")
    assert not bridge.is_running
    assert agent.listeners == []
    assert agent.stopped is True
    assert await manager.pool.get("research-team") is None


@pytest.mark.asyncio
async def test_finalize_stop_path_dismounts_bridge() -> None:
    """The finalize stop branch (non-persistent) also tears the bridge down."""
    manager = TeamRuntimeManager()
    agent = _StubAgent()
    manager.set_extension_bus(await _ready_bus())
    bridge = await manager._start_monitor_event_bridge(agent, "research-team", "session-1")
    await manager.pool.add(
        ActiveTeam(
            team_name="research-team",
            agent=agent,  # type: ignore[arg-type]
            current_session_id="session-1",
            monitor_event_bridge=bridge,
        ),
    )

    await manager.finalize(team_name="research-team", session_id="session-1")
    assert not bridge.is_running
    assert agent.listeners == []
    assert await manager.pool.get("research-team") is None

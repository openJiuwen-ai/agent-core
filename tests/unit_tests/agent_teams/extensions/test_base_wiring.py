# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Host-facing wiring points: the Runner facade and the lazy resolver."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from openjiuwen.core.events import ExtensionEventBus


def test_runner_facade_injects_the_bus() -> None:
    """Runner.set_team_extension_bus reaches the manager's holders."""
    from openjiuwen.core.runner.runner import GLOBAL_RUNNER
    from openjiuwen.core.runner.team_runner import _TeamRunnerClassMixin

    assert hasattr(_TeamRunnerClassMixin, "set_team_extension_bus")
    manager = GLOBAL_RUNNER._get_team_runtime_manager()
    previous = manager.extension_bus
    try:
        from openjiuwen.core.runner import Runner

        bus = ExtensionEventBus()
        Runner.set_team_extension_bus(bus)
        assert manager.extension_bus is bus
        Runner.set_team_extension_bus(None)
        assert manager.extension_bus is None
    finally:
        manager.set_extension_bus(previous)


@pytest.mark.asyncio
async def test_global_runner_pool_resolver_is_lazy() -> None:
    """The lazy resolver reads the manager slot at resolve time only."""
    from openjiuwen.agent_teams.extensions.resolver import (
        BackendTeamReadView,
        GlobalRunnerPoolResolver,
    )
    from openjiuwen.agent_teams.runtime.pool import ActiveTeam
    from openjiuwen.core.runner.runner import GLOBAL_RUNNER

    resolver = GlobalRunnerPoolResolver()

    @dataclass
    class _StubBuildBackend:
        team_name: str = "research-team"
        db: Any = None

    @dataclass
    class _StubAgent:
        team_backend: Any = None

    entry = ActiveTeam(
        team_name="research-team",
        agent=_StubAgent(team_backend=_StubBuildBackend()),  # type: ignore[arg-type]
        current_session_id="session-1",
    )
    manager = GLOBAL_RUNNER._get_team_runtime_manager()
    await manager.pool.add(entry)
    try:
        view = await resolver.resolve("session-1", "research-team")
        assert isinstance(view, BackendTeamReadView)
        assert await resolver.resolve("session-1", "other-team") is None
        assert await resolver.resolve("other-session", "research-team") is None
        assert await resolver.resolve(None, "research-team") is None
    finally:
        await manager.pool.remove("research-team")

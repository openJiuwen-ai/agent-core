# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Host-facing wiring points: the Runner facade and the database resolver."""

from __future__ import annotations

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
async def test_database_resolver_resolves_from_the_factory() -> None:
    """The resolver calls the db factory at resolve time and answers from rows."""
    from openjiuwen.agent_teams.context import reset_session_id, set_session_id
    from openjiuwen.agent_teams.extensions.resolver import (
        BackendTeamReadView,
        DatabaseResolver,
    )
    from openjiuwen.agent_teams.tools.database import TeamDatabase
    from openjiuwen.agent_teams.tools.database.config import DatabaseConfig

    token = set_session_id("session-1")
    try:
        database = TeamDatabase(DatabaseConfig(connection_string=":memory:"))
        await database.initialize()
        await database.team.create_team("research-team", "Research", "leader")
    finally:
        reset_session_id(token)

    async def factory() -> TeamDatabase:
        return database

    try:
        resolver = DatabaseResolver(factory)
        view = await resolver.resolve("session-1", "research-team")
        assert isinstance(view, BackendTeamReadView)
        # The session id selects dynamic tables, not access: a team with
        # rows answers from any session that knows its name.
        assert await resolver.resolve("other-session", "research-team") is not None
        assert await resolver.resolve("session-1", "other-team") is None
        assert await resolver.resolve(None, "research-team") is None
    finally:
        await database.close()

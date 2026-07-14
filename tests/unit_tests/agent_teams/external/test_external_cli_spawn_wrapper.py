# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for external CLI spawn wrapper lifecycle."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from openjiuwen.agent_teams.schema.blueprint import DeepAgentSpec, TeamAgentSpec
from openjiuwen.agent_teams.schema.status import MemberMode
from openjiuwen.agent_teams.schema.team import TeamLifecycle, TeamRole, TeamRuntimeContext, TeamSpec
from openjiuwen.agent_teams.spawn import external_cli_spawn as spawn_mod
from openjiuwen.core.runner.runner import Runner


class _FakeRuntime:
    def __init__(self) -> None:
        self.stopped = False

    async def start(self, *, team_session: Any | None = None) -> None:
        """Start fake runtime."""

    async def stop(self) -> None:
        """Record stop calls."""
        self.stopped = True

    def outputs(self) -> Any:
        """Return an empty async iterator."""
        return _empty_outputs()

    async def send(self, content: Any, *, immediate: bool = False) -> Any:
        """Accept runtime input."""
        _ = content, immediate
        return None

    async def abort(self, *, immediate: bool = False) -> None:
        """Accept abort calls."""
        _ = immediate

    async def pause(self) -> None:
        """Accept pause calls."""

    async def subscribe(self, **kwargs: Any) -> None:
        """Accept subscriptions."""
        _ = kwargs

    def set_background_task_controller(self, controller: Any) -> None:
        """Accept background controller wiring."""
        _ = controller

    def find_rails(self, rail_type: Any) -> list[Any]:
        """Return no mounted rails."""
        _ = rail_type
        return []


class _FakeTeamAgent:
    def __init__(self, spec: TeamAgentSpec) -> None:
        self.spec = spec
        self.team_backend = None


async def _empty_outputs() -> Any:
    """Yield no runtime output."""
    if False:
        yield None


@pytest.mark.asyncio
@pytest.mark.level0
async def test_external_cli_spawn_stops_runtime_on_cancel(monkeypatch):
    """Cancelling the spawned task should stop the external runtime."""
    runtime = _FakeRuntime()
    started = asyncio.Event()
    release = asyncio.Event()

    async def _fake_build_cli_runtime(*args: Any, **kwargs: Any) -> _FakeRuntime:
        _ = args, kwargs
        return runtime

    async def _fake_run_agent_team(*args: Any, **kwargs: Any) -> None:
        _ = args, kwargs
        started.set()
        await release.wait()

    monkeypatch.setattr(spawn_mod, "build_cli_runtime", _fake_build_cli_runtime)
    monkeypatch.setattr(Runner, "run_agent_team", _fake_run_agent_team)

    spec = TeamAgentSpec(
        agents={"leader": DeepAgentSpec()},
        team_name="ext_team",
        display_name="Ext",
        lifecycle=TeamLifecycle.PERSISTENT,
        teammate_mode=MemberMode.BUILD_MODE,
    )
    team_spec = TeamSpec(team_name="ext_team", display_name="Ext")
    ctx = TeamRuntimeContext(
        role=TeamRole.TEAMMATE,
        member_name="claude-1",
        cli_agent="claude",
        team_spec=team_spec,
    )
    handle = await spawn_mod.external_cli_spawn(
        _FakeTeamAgent(spec),
        ctx,
        session_id="sess-1",
    )
    await started.wait()

    await handle.force_kill()
    release.set()

    assert runtime.stopped


@pytest.mark.asyncio
@pytest.mark.level0
async def test_external_cli_spawn_resume_uses_empty_initial_query(monkeypatch):
    """Resumed external CLI members should wait for mailbox input."""
    runtime = _FakeRuntime()
    started = asyncio.Event()
    build_kwargs: dict[str, Any] = {}
    run_inputs: dict[str, Any] = {}

    async def _fake_build_cli_runtime(*args: Any, **kwargs: Any) -> _FakeRuntime:
        _ = args
        build_kwargs.update(kwargs)
        return runtime

    async def _fake_run_agent_team(*args: Any, **kwargs: Any) -> None:
        _ = kwargs
        run_inputs.update(args[1])
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(spawn_mod, "build_cli_runtime", _fake_build_cli_runtime)
    monkeypatch.setattr(Runner, "run_agent_team", _fake_run_agent_team)

    spec = TeamAgentSpec(
        agents={"leader": DeepAgentSpec()},
        team_name="ext_team",
        display_name="Ext",
        lifecycle=TeamLifecycle.PERSISTENT,
        teammate_mode=MemberMode.BUILD_MODE,
    )
    team_spec = TeamSpec(team_name="ext_team", display_name="Ext")
    ctx = TeamRuntimeContext(
        role=TeamRole.TEAMMATE,
        member_name="claude-1",
        cli_agent="claude",
        team_spec=team_spec,
    )

    handle = await spawn_mod.external_cli_spawn(
        _FakeTeamAgent(spec),
        ctx,
        session_id="sess-1",
        resume_external_backend=True,
    )
    await started.wait()
    await handle.force_kill()

    assert build_kwargs["resume_external_backend"] is True
    assert run_inputs == {"query": ""}

# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for external CLI spawn wrapper lifecycle."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from openjiuwen.agent_teams.runtime.metadata import (
    merge_external_session_id,
    read_external_session_id,
)
from openjiuwen.agent_teams.schema.blueprint import DeepAgentSpec, TeamAgentSpec
from openjiuwen.agent_teams.schema.build_context import BuildContext
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
    def __init__(self, spec: TeamAgentSpec, team_session: Any | None = None) -> None:
        self.spec = spec
        self.team_backend = None
        self.session_manager = SimpleNamespace(team_session=team_session)


class _FakeTeamSession:
    def __init__(self) -> None:
        self.state: dict[str, Any] = {}
        self.flush_count = 0

    def update_state(self, data: dict[str, Any]) -> None:
        self.state.update(data)

    def get_state(self, key: str | None = None) -> Any:
        return self.state if key is None else self.state.get(key)

    async def flush_checkpoint(self) -> None:
        self.flush_count += 1


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
async def test_external_cli_spawn_without_initial_message_uses_empty_query(monkeypatch):
    """External CLI members should wait for mailbox input when no prompt is provided."""
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
    )
    await started.wait()
    await handle.force_kill()

    assert build_kwargs["resume_external_backend"] is False
    assert run_inputs == {"query": ""}


@pytest.mark.asyncio
@pytest.mark.level0
async def test_external_cli_spawn_resume_passes_backend_flag(monkeypatch):
    """Resumed external CLI members pass the resume flag with no startup prompt."""
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


@pytest.mark.asyncio
@pytest.mark.level0
async def test_codex_spawn_restores_and_checkpoints_member_thread_id(monkeypatch):
    """Cold Codex rebuilds resume only their own checkpointed SDK thread."""
    runtime = _FakeRuntime()
    started = asyncio.Event()
    build_kwargs: dict[str, Any] = {}

    async def _fake_build_cli_runtime(*args: Any, **kwargs: Any) -> _FakeRuntime:
        _ = args
        build_kwargs.update(kwargs)
        return runtime

    async def _fake_run_agent_team(*args: Any, **kwargs: Any) -> None:
        _ = args, kwargs
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
        external_cli_agents=[{"cli_agent": "codex", "codex_bin": "/opt/codex"}],
    )
    session = _FakeTeamSession()
    merge_external_session_id(session, "ext_team", "codex-1", "codex", "thread-old")
    agent = _FakeTeamAgent(spec, session)
    ctx = TeamRuntimeContext(
        role=TeamRole.TEAMMATE,
        member_name="codex-1",
        cli_agent="codex",
        team_spec=TeamSpec(team_name="ext_team", display_name="Ext"),
    )

    handle = await spawn_mod.external_cli_spawn(
        agent,
        ctx,
        session_id="sess-1",
        resume_external_backend=True,
    )
    await started.wait()

    assert build_kwargs["external_session_id"] == "thread-old"
    assert build_kwargs["codex_bin"] == "/opt/codex"
    await build_kwargs["on_external_session_id"]("thread-new")
    assert read_external_session_id(session, "ext_team", "codex-1", "codex") == "thread-new"
    assert session.flush_count == 1

    await handle.force_kill()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_external_cli_spawn_resolves_worktree_cwd_and_add_dirs(monkeypatch, tmp_path):
    """External members use worktree cwd and expose project/team roots as extra dirs."""
    runtime = _FakeRuntime()
    started = asyncio.Event()
    build_kwargs: dict[str, Any] = {}

    async def _fake_build_cli_runtime(*args: Any, **kwargs: Any) -> _FakeRuntime:
        _ = args
        build_kwargs.update(kwargs)
        return runtime

    async def _fake_run_agent_team(*args: Any, **kwargs: Any) -> None:
        _ = args, kwargs
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(spawn_mod, "build_cli_runtime", _fake_build_cli_runtime)
    monkeypatch.setattr(Runner, "run_agent_team", _fake_run_agent_team)

    team_workspace = str(tmp_path / "team-workspace")
    project_dir = str(tmp_path / "project")
    worktree_path = str(tmp_path / "worktree")

    spec = TeamAgentSpec(
        agents={"leader": DeepAgentSpec()},
        team_name="ext_team",
        display_name="Ext",
        lifecycle=TeamLifecycle.PERSISTENT,
        teammate_mode=MemberMode.BUILD_MODE,
        workspace={"enabled": True, "root_path": team_workspace},
        build_context=BuildContext(project_dir=project_dir),
        external_cli_agents=[{"cli_agent": "claude"}],
    )
    team_spec = TeamSpec(team_name="ext_team", display_name="Ext")
    ctx = TeamRuntimeContext(
        role=TeamRole.TEAMMATE,
        member_name="claude-1",
        cli_agent="claude",
        team_spec=team_spec,
        worktree_path=worktree_path,
    )

    handle = await spawn_mod.external_cli_spawn(
        _FakeTeamAgent(spec),
        ctx,
        session_id="sess-1",
    )
    await started.wait()
    await handle.force_kill()

    assert build_kwargs["cwd"] == worktree_path
    assert build_kwargs["add_dirs"] == (project_dir, team_workspace)


@pytest.mark.asyncio
@pytest.mark.level0
async def test_external_cli_spawn_explicit_cwd_wins_and_others_become_add_dirs(monkeypatch, tmp_path):
    """Explicit external cwd has priority; other candidate roots remain accessible."""
    runtime = _FakeRuntime()
    started = asyncio.Event()
    build_kwargs: dict[str, Any] = {}

    async def _fake_build_cli_runtime(*args: Any, **kwargs: Any) -> _FakeRuntime:
        _ = args
        build_kwargs.update(kwargs)
        return runtime

    async def _fake_run_agent_team(*args: Any, **kwargs: Any) -> None:
        _ = args, kwargs
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(spawn_mod, "build_cli_runtime", _fake_build_cli_runtime)
    monkeypatch.setattr(Runner, "run_agent_team", _fake_run_agent_team)

    team_workspace = str(tmp_path / "team-workspace")
    project_dir = str(tmp_path / "project")
    worktree_path = str(tmp_path / "worktree")
    custom_cwd = str(tmp_path / "custom")

    spec = TeamAgentSpec(
        agents={"leader": DeepAgentSpec()},
        team_name="ext_team",
        display_name="Ext",
        lifecycle=TeamLifecycle.PERSISTENT,
        teammate_mode=MemberMode.BUILD_MODE,
        workspace={"enabled": True, "root_path": team_workspace},
        build_context=BuildContext(project_dir=project_dir),
        external_cli_agents=[{"cli_agent": "claude", "cwd": custom_cwd}],
    )
    team_spec = TeamSpec(team_name="ext_team", display_name="Ext")
    ctx = TeamRuntimeContext(
        role=TeamRole.TEAMMATE,
        member_name="claude-1",
        cli_agent="claude",
        team_spec=team_spec,
        worktree_path=worktree_path,
    )

    handle = await spawn_mod.external_cli_spawn(
        _FakeTeamAgent(spec),
        ctx,
        session_id="sess-1",
    )
    await started.wait()
    await handle.force_kill()

    assert build_kwargs["cwd"] == custom_cwd
    assert build_kwargs["add_dirs"] == (worktree_path, project_dir, team_workspace)


@pytest.mark.asyncio
@pytest.mark.level0
async def test_external_cli_spawn_keeps_explicit_initial_message(monkeypatch):
    """Explicit initial prompts are still delivered to the external runtime."""
    runtime = _FakeRuntime()
    started = asyncio.Event()
    run_inputs: dict[str, Any] = {}

    async def _fake_build_cli_runtime(*args: Any, **kwargs: Any) -> _FakeRuntime:
        _ = args, kwargs
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
        initial_message="hello",
        session_id="sess-1",
    )
    await started.wait()
    await handle.force_kill()

    assert run_inputs == {"query": "hello"}

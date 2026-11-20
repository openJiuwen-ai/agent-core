# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The session background-task controller attached to in-process teammates.

The controller only ever reached the leader (``run_agent_team_streaming``
attach), so a teammate's ``SwarmflowTool`` saw ``background_task_controller
is None``: its runs never registered, stop_by_owner was a no-op, and a team
pause could not park them. The spawn path now hands the leader's controller
to the teammate right after ``configure`` (before it, the teammate has no
harness and the call would be a no-op), so every member's runs live in one
session registry.
"""

from __future__ import annotations

import asyncio
import contextlib
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from openjiuwen.agent_teams.agent.team_agent import TeamAgent
from openjiuwen.agent_teams.harness.team_harness import TeamHarness
from openjiuwen.agent_teams.runtime.background_task_controller import BackgroundTaskController
from openjiuwen.agent_teams.schema.blueprint import TeamAgentSpec
from openjiuwen.agent_teams.schema.build_context import BuildContext
from openjiuwen.agent_teams.schema.deep_agent_spec import DeepAgentSpec
from openjiuwen.agent_teams.schema.team import TeamRole, TeamRuntimeContext, TeamSpec
from openjiuwen.agent_teams.spawn.inprocess_spawn import inprocess_spawn
from openjiuwen.agent_teams.workflow.engine.errors import BackendError
from openjiuwen.agent_teams.workflow.tool_swarmflow import SwarmflowTool
from openjiuwen.core.runner.runner import Runner
from openjiuwen.core.single_agent.schema.agent_card import AgentCard


def _swarmflow_spec() -> TeamAgentSpec:
    return TeamAgentSpec(
        team_name="wf_team",
        agents={"leader": DeepAgentSpec(), "teammate": DeepAgentSpec()},
        enable_swarmflow=True,
        build_context=BuildContext(),
    )


def _member_ctx(member_name: str) -> TeamRuntimeContext:
    return TeamRuntimeContext(
        role=TeamRole.TEAMMATE,
        member_name=member_name,
        team_spec=TeamSpec(team_name="wf_team", display_name="wf_team", leader_member_name="leader"),
    )


# ---------------------------------------------------------------------------
# The property pair (TeamHarness read side + TeamAgent passthrough)
# ---------------------------------------------------------------------------


def _bare_harness(controller: Any = None) -> TeamHarness:
    """A TeamHarness with only the controller fields materialized."""
    harness = TeamHarness.__new__(TeamHarness)
    harness._native = None
    harness._bg_controller = controller
    return harness


def test_team_harness_exposes_the_attached_controller():
    """set → the read property returns the same instance; unset → None."""
    harness = _bare_harness()
    assert harness.background_task_controller is None

    controller = object()
    harness.set_background_task_controller(controller)

    assert harness.background_task_controller is controller


def test_team_agent_passthrough_reads_the_harness_controller():
    """The TeamAgent property forwards to its harness; None before a runtime."""
    agent = TeamAgent(AgentCard(id="a", name="a", description="a"))
    assert agent.background_task_controller is None

    controller = object()
    agent._configurator.harness = _bare_harness(controller)

    assert agent.background_task_controller is controller


# ---------------------------------------------------------------------------
# The spawn-side attach (ordering: configure first, then the controller)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inprocess_spawn_attaches_the_leader_controller_after_configure(monkeypatch):
    """The teammate receives the leader's controller — the SAME instance —
    after configure (a pre-configure call would hit a harness-less no-op)."""
    calls: list[Any] = []
    controller = object()
    leader = SimpleNamespace(
        spec=_swarmflow_spec(),
        share_workspace_cache_with=lambda _other: None,
        share_swarmflow_with=lambda _other: None,
        share_checkpoints_with=lambda _other: None,
        background_task_controller=controller,
    )

    def fake_configure(self, spec, ctx, **kwargs):
        calls.append("configure")

    def fake_set_controller(self, ctl):
        calls.append(("set_controller", ctl))

    async def fake_run_agent_team(agent, inputs, **kwargs):
        return None

    monkeypatch.setattr(TeamAgent, "configure", fake_configure)
    monkeypatch.setattr(TeamAgent, "set_background_task_controller", fake_set_controller)
    monkeypatch.setattr(Runner, "run_agent_team", fake_run_agent_team)

    handle = await inprocess_spawn(leader, _member_ctx("worker-1"))

    assert calls == ["configure", ("set_controller", controller)]
    await handle._task


@pytest.mark.asyncio
async def test_inprocess_spawn_without_a_leader_controller_attaches_nothing(monkeypatch):
    """An embedder that holds no controller (or a leader spawned before any
    attach) attaches nothing — the teammate keeps its None, exactly as
    before this chain existed."""
    calls: list[Any] = []
    leader = SimpleNamespace(
        spec=_swarmflow_spec(),
        share_workspace_cache_with=lambda _other: None,
        share_swarmflow_with=lambda _other: None,
        share_checkpoints_with=lambda _other: None,
        background_task_controller=None,
    )

    def fake_configure(self, spec, ctx, **kwargs):
        calls.append("configure")

    def fake_set_controller(self, ctl):
        calls.append(("set_controller", ctl))

    async def fake_run_agent_team(agent, inputs, **kwargs):
        return None

    monkeypatch.setattr(TeamAgent, "configure", fake_configure)
    monkeypatch.setattr(TeamAgent, "set_background_task_controller", fake_set_controller)
    monkeypatch.setattr(Runner, "run_agent_team", fake_run_agent_team)

    handle = await inprocess_spawn(leader, _member_ctx("worker-1"))

    assert calls == ["configure"]
    await handle._task


# ---------------------------------------------------------------------------
# What the attach unlocks: registration + teardown + pause over one registry
# ---------------------------------------------------------------------------


class _StubAsyncRuntime:
    """AsyncToolRuntime stand-in: ``cancel`` cancels the real task, and the
    ``_tasks`` map lets the controller's unwind-wait observe it."""

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[Any]] = {}

    async def cancel(self, task_id: str) -> None:
        task = self._tasks.get(task_id)
        if task is not None:
            task.cancel()


class _TeammateHarnessStub:
    """The parent-agent surface ``run_background`` reads off a teammate."""

    def __init__(self, controller: Any, member_name: str) -> None:
        self.model = "m"
        self.background_task_controller = controller
        self.build_context = SimpleNamespace(role="teammate", member_name=member_name)
        self.async_tool_runtime = _StubAsyncRuntime()


class _RecMessager:
    def __init__(self) -> None:
        self.published: list[Any] = []

    async def publish(self, *, topic_id, message):
        self.published.append((topic_id, message))


def _teammate_tool(controller: Any, member_name: str = "worker-1") -> SwarmflowTool:
    tool = object.__new__(SwarmflowTool)
    tool._parent_agent = _TeammateHarnessStub(controller, member_name)
    tool._messager = None
    tool._team_name = "wf_team"
    tool._language = "cn"
    tool._model_resolver = None
    tool._worker_base_spec = None
    tool._human_base_spec = None
    tool._budget = None
    tool._governor = None
    tool._team_backend = None
    return tool


def _inputs(run_id: str) -> dict[str, Any]:
    return {
        "_run_id": run_id,
        "_agent_gate": None,
        "_workflow_ticket": None,
        "_completion_ctx": {},
        "script_path": "/tmp/flow.py",
    }


async def _launch_and_register(tool: SwarmflowTool, run_id: str, task_id: str) -> asyncio.Task[Any]:
    """Run run_background as a live task whose engine call blocks until aborted
    (like a real run); the handle registers via on_backend_ready and stays."""

    async def fake_run_swarmflow(*_a, on_backend_ready=None, **_k):
        on_backend_ready(object())
        await asyncio.sleep(3600)

    async def run() -> Any:
        with patch(
            "openjiuwen.agent_teams.workflow.runner.run_swarmflow",
            side_effect=fake_run_swarmflow,
        ):
            return await tool.run_background(task_id, _inputs(run_id))

    task = asyncio.create_task(run())
    runtime = tool._parent_agent.async_tool_runtime
    runtime._tasks[task_id] = task
    for _ in range(200):
        if run_id in tool._parent_agent.background_task_controller._active:
            return task
        await asyncio.sleep(0)
    raise AssertionError(f"{run_id} never registered in the controller")


@pytest.mark.asyncio
async def test_teammate_run_registers_its_handle_in_the_controller():
    """A teammate-launched run lands in the session registry (owner member +
    the launching tool as relaunch host) — the registration the missing
    attach used to skip entirely."""
    controller = BackgroundTaskController()
    tool = _teammate_tool(controller)

    task = await _launch_and_register(tool, "wf_reg1", "task-1")
    try:
        handle = controller._active["wf_reg1"]
        assert handle.owner_member == "worker-1"
        assert handle.launcher is tool
        assert handle.native is tool._parent_agent
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_teammate_teardown_gracefully_stops_its_registered_run():
    """The full chain the attach enables: the teammate harness's permanent
    dispose drives stop_by_owner on the shared controller — the run unwinds
    with the terminal stop reason and a WORKFLOW_STOPPED event, not a bare
    cancel that leaves the card running. The round-end stop() recycles only
    the native: the run is hosted by the member runtime and the team-level
    controller, so it must stay alive across rounds."""
    controller = BackgroundTaskController()
    tool = _teammate_tool(controller)
    tool._messager = _RecMessager()
    unwind_reasons: list[str] = []

    async def fake_run_swarmflow(*_a, on_backend_ready=None, abort_event=None, **_k):
        on_backend_ready(object())
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            unwind_reasons.append(abort_event.reason)
            raise

    async def run() -> Any:
        with patch(
            "openjiuwen.agent_teams.workflow.runner.run_swarmflow",
            side_effect=fake_run_swarmflow,
        ):
            return await tool.run_background("task-2", _inputs("wf_stop1"))

    task = asyncio.create_task(run())
    tool._parent_agent.async_tool_runtime._tasks["task-2"] = task
    for _ in range(200):
        if "wf_stop1" in controller._active:
            break
        await asyncio.sleep(0)

    # A TeamHarness mirroring the attached teammate.
    harness = TeamHarness.__new__(TeamHarness)
    harness._native = None
    harness._bg_controller = controller
    harness._member_name = "worker-1"
    harness._active_agent_session = None

    # Round-end stop: native-only recycle, the run keeps running.
    await harness.stop()
    assert not task.cancelled()
    assert "wf_stop1" in controller._active

    # Permanent teardown: the owned run stops through the controller.
    await harness.dispose()
    with pytest.raises(BackendError):
        await task  # cancelled by the controller's three-step abort

    assert unwind_reasons == ["stop"]  # graceful: the engine saw the terminal reason
    kinds = [m.payload["kind"] for _, m in tool._messager.published]
    assert "workflow_stopped" in kinds  # the embedder's card closes
    assert "wf_stop1" not in controller._active  # terminal: stopped, not parked
    assert "wf_stop1" not in controller._paused


@pytest.mark.asyncio
async def test_team_pause_parks_a_teammate_run_in_the_shared_registry():
    """A session-level pause(None) parks a teammate's run like any other —
    the busy/pause semantics the embedder drives over the whole team."""
    controller = BackgroundTaskController()
    tool = _teammate_tool(controller, member_name="worker-2")

    task = await _launch_and_register(tool, "wf_park1", "task-3")

    parked = await controller.pause(None)
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert parked is True
    assert "wf_park1" in controller._paused
    assert controller._paused["wf_park1"].owner_member == "worker-2"
    assert controller._paused["wf_park1"].abort_event.reason == "pause"


# ---------------------------------------------------------------------------
# The leader-side contract the attach must not disturb
# ---------------------------------------------------------------------------


def test_leader_controller_survives_the_read_path_unchanged():
    """The leader attach (team_runner) uses the same set method; the read
    property returns whatever was attached — the spawn path re-reads it on
    every spawn, so a later attach (or a controller-less embedder) is
    picked up, never a stale one."""
    controller = BackgroundTaskController()
    harness = _bare_harness()
    harness.set_background_task_controller(controller)
    assert harness.background_task_controller is controller

    fresh = BackgroundTaskController()
    harness.set_background_task_controller(fresh)
    assert harness.background_task_controller is fresh

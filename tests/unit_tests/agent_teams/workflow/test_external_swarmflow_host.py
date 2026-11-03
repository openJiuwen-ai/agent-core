# coding: utf-8
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from openjiuwen.agent_teams.workflow.external_swarmflow_host import ExternalSwarmflowHost
from openjiuwen.agent_teams.workflow.tool_swarmflow import SwarmflowTool


class _RecordingRuntime:
    """Member runtime double recording non-immediate sends."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, bool]] = []

    async def send(self, text: Any, *, immediate: bool = False) -> None:
        self.sent.append((str(text), immediate))


class _Ctx:
    member_name = "codex-1"


def _make_host(controller: Any = None) -> tuple[ExternalSwarmflowHost, _RecordingRuntime]:
    runtime = _RecordingRuntime()
    host = ExternalSwarmflowHost(build_context=_Ctx(), background_task_controller=controller)
    host.bind_member_runtime(runtime)
    return host, runtime


def test_host_provides_the_parent_agent_surface_the_tool_probes():
    """The host stands in for the NativeHarness the tool expects."""
    host, _ = _make_host()
    assert host.build_context.member_name == "codex-1"
    assert host.model is None
    assert host.background_task_controller is None
    assert host.async_tool_runtime is not None


def test_tool_constructs_over_the_host_with_the_controller_launcher_slot():
    """A SwarmflowTool over the host registers itself as relaunch host."""
    from openjiuwen.agent_teams.runtime.background_task_controller import BackgroundTaskController

    ctl = BackgroundTaskController()
    host, _ = _make_host(controller=ctl)
    tool = SwarmflowTool(parent_agent=host, messager=None, team_name="t", model_resolver=lambda n: None)
    assert ctl._launcher is tool
    assert tool._executor_member_name() == "codex-1"


@pytest.mark.asyncio
async def test_completion_injection_forwards_to_member_runtime():
    """The inject callback delivers text as a non-immediate send."""
    host, runtime = _make_host()
    await host.async_tool_runtime.inject("run done")
    assert runtime.sent == [("run done", False)]


@pytest.mark.asyncio
async def test_injection_before_runtime_bound_drops_instead_of_raising():
    """An unbound host must not crash a completing run (teardown race)."""
    host = ExternalSwarmflowHost(build_context=_Ctx())
    await host.async_tool_runtime.inject("late")
    # no runtime — dropped, no exception


@pytest.mark.asyncio
async def test_launch_and_completion_round_trip_over_the_host():
    """launch_async_tool schedules; completion reaches the member's turns."""
    host, runtime = _make_host()

    async def _work() -> str:
        await asyncio.sleep(0)
        return "result-42"

    host.launch_async_tool("task-1", _work, tool_name="swarmflow", description="probe")
    for _ in range(50):
        if runtime.sent:
            break
        await asyncio.sleep(0.02)
    assert len(runtime.sent) == 1
    text, immediate = runtime.sent[0]
    assert "result-42" in text
    assert immediate is False


@pytest.mark.asyncio
async def test_background_failure_injects_error_text():
    """A failing background run surfaces its error text to the member."""
    host, runtime = _make_host()

    async def _boom() -> Any:
        raise RuntimeError("engine died")

    host.launch_async_tool("task-2", _boom, tool_name="swarmflow", description="probe")
    for _ in range(50):
        if runtime.sent:
            break
        await asyncio.sleep(0.02)
    text, _ = runtime.sent[0]
    assert "engine died" in text


@pytest.mark.asyncio
async def test_controller_pause_path_reaches_host_runtime():
    """A registered run pauses through the controller's three-step abort.

    The ticket's ``native`` is the host; the controller cancels the run task
    through ``native.async_tool_runtime`` exactly like a NativeHarness ticket.
    """
    from openjiuwen.agent_teams.runtime.background_task_controller import (
        BackgroundTaskController,
        SwarmflowRunHandle,
    )
    from openjiuwen.agent_teams.workflow.engine.runtime import AbortSignal

    ctl = BackgroundTaskController()
    host, _ = _make_host(controller=ctl)
    release = asyncio.Event()

    async def _hang() -> Any:
        await release.wait()

    host.launch_async_tool("task-3", _hang, tool_name="swarmflow", description="probe")
    task = host.async_tool_runtime._tasks["task-3"]
    handle = SwarmflowRunHandle(
        task_id="task-3",
        run_id="wf_probe",
        abort_event=AbortSignal(),
        backend=None,
        native=host,
        inputs={},
        session_id="sess",
        launcher=None,
        owner_member="codex-1",
    )
    ctl.register(handle)
    ok = await ctl.pause("wf_probe")
    assert ok
    assert task.cancelled()
    assert ctl.is_paused("wf_probe")
    release.set()

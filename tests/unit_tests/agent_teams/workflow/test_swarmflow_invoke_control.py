# coding: utf-8
from __future__ import annotations
import asyncio
import pytest
from unittest.mock import AsyncMock
from openjiuwen.agent_teams.workflow.tool_swarmflow import SwarmflowTool


class _FakeController:
    def __init__(self):
        self.calls = []
    async def pause(self, run_id): self.calls.append(("pause", run_id)); return True
    async def resume(self, run_id, *, tool=None): self.calls.append(("resume", run_id)); return True
    async def stop(self, run_id): self.calls.append(("stop", run_id)); return True


def _make_tool(controller):
    tool = object.__new__(SwarmflowTool)
    tool._parent_agent = type("P", (), {"background_task_controller": controller})()
    return tool


@pytest.mark.asyncio
async def test_pause_action_calls_controller():
    ctl = _FakeController()
    tool = _make_tool(ctl)
    out = await tool.invoke({"resume_id": "wf_1", "action": "pause"})
    assert out.success and ctl.calls == [("pause", "wf_1")]


@pytest.mark.asyncio
async def test_resume_action_calls_controller():
    ctl = _FakeController()
    tool = _make_tool(ctl)
    out = await tool.invoke({"resume_id": "wf_1", "action": "resume"})
    assert out.success and ctl.calls == [("resume", "wf_1")]


@pytest.mark.asyncio
async def test_stop_action_calls_controller():
    ctl = _FakeController()
    tool = _make_tool(ctl)
    out = await tool.invoke({"resume_id": "wf_1", "action": "stop"})
    assert out.success and ctl.calls == [("stop", "wf_1")]


@pytest.mark.asyncio
async def test_resume_id_without_action_requires_a_script_source():
    """resume_id without action is a re-launch — it still needs script_path/script."""
    ctl = _FakeController()
    tool = _make_tool(ctl)
    out = await tool.invoke({"resume_id": "wf_1"})
    assert not out.success
    # Falls through to the launch path, which requires a script source (not
    # "action is required" — that gate is gone).
    assert "script" in out.error.lower()


@pytest.mark.asyncio
async def test_resume_relaunches_on_the_current_harness_not_the_captured_one():
    """A resume issued from a rebuilt leader harness must relaunch on THAT harness.

    Team pause stops the leader harness; RESUME_FROM_PAUSE rebuilds it (a new
    NativeHarness + a new SwarmflowTool). The relaunch closure captured the OLD
    tool at launch, so a naive ``self._parent_agent.launch_async_tool`` would
    hang the resumed run off the dead harness: it runs, but its completion
    injection hits a stopped harness and the leader never learns it finished
    (no report, no idle, lamp never goes off).
    """
    from openjiuwen.agent_teams.runtime.background_task_controller import (
        BackgroundTaskController, SwarmflowRunHandle,
    )
    from openjiuwen.agent_teams.workflow.engine.runtime import AbortSignal

    launched_on: list[str] = []

    def _harness(name: str):
        class _H:
            model = "m"
            build_context = None
            background_task_controller = None
            def launch_async_tool(self, task_id, coro_factory, *, tool_name, description):
                launched_on.append(name)
        return _H()

    ctl = BackgroundTaskController()
    old_harness, new_harness = _harness("old"), _harness("new")
    old_harness.background_task_controller = ctl
    new_harness.background_task_controller = ctl

    def _tool(harness):
        t = object.__new__(SwarmflowTool)
        t._parent_agent = harness
        t._card = type("C", (), {"name": "swarmflow"})()
        return t

    old_tool, new_tool = _tool(old_harness), _tool(new_harness)
    inputs = {"script_path": "/x.py", "_run_id": "wf_1"}

    class _Backend:
        async def abort_sessions(self): pass

    class _RT:
        _tasks: dict = {}
        async def cancel(self, task_id): return True

    old_harness.async_tool_runtime = _RT()
    # Mirrors run_background's registration: the closure binds the OLD tool.
    ctl.register(SwarmflowRunHandle(
        task_id="t1", run_id="wf_1", abort_event=AbortSignal(),
        backend=_Backend(), native=old_harness,
        relaunch=old_tool._make_relaunch(inputs, "sess"),
    ))
    await ctl.pause("wf_1")

    out = await new_tool.invoke({"resume_id": "wf_1", "action": "resume"})

    assert out.success
    assert launched_on == ["new"]

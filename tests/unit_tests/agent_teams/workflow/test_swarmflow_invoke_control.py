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
    async def resume(self, run_id): self.calls.append(("resume", run_id)); return True
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
async def test_tool_registers_itself_as_controller_launcher():
    """Every SwarmflowTool build registers as the controller's relaunch host.

    The team tool rail rebuilds the tool with each leader NativeHarness cycle;
    a team pause tears the old harness down, so the ticket that launched a run
    must be relaunched by whichever tool belongs to the live cycle.
    """
    from openjiuwen.agent_teams.runtime.background_task_controller import BackgroundTaskController

    ctl = BackgroundTaskController()

    def _harness():
        class _H:
            model = "m"
            build_context = None
            background_task_controller = ctl
        return _H()

    old_tool = SwarmflowTool(parent_agent=_harness(), messager=None, team_name="t", model_resolver=None)
    assert ctl._launcher is old_tool
    new_tool = SwarmflowTool(parent_agent=_harness(), messager=None, team_name="t", model_resolver=None)
    assert ctl._launcher is new_tool  # newest cycle wins

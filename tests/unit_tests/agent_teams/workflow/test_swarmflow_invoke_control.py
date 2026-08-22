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

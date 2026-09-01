# coding: utf-8
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from openjiuwen.agent_teams.workflow.tool_swarmflow import SwarmflowTool


class _FakeController:
    def __init__(self):
        self.calls = []

    async def pause(self, run_id):
        self.calls.append(("pause", run_id))
        return True

    async def resume(self, run_id):
        self.calls.append(("resume", run_id))
        return True

    async def stop(self, run_id):
        self.calls.append(("stop", run_id))
        return True


class _FakeGovernor:
    """Minimal governor: admit always succeeds (returns a ticket + gate)."""

    def __init__(self):
        self.admitted = []

    async def admit_workflow(self):
        self.admitted.append(True)
        return type("A", (), {"ticket": object(), "agent_gate": object()})()

    def release_workflow(self, ticket):
        pass

    def new_agent_gate(self):
        return object()

    def snapshot(self):
        return type("S", (), {"active_workflows": 1, "max_workflows": 1})()


def _make_tool(controller):
    tool = object.__new__(SwarmflowTool)
    tool._parent_agent = type("P", (), {"background_task_controller": controller})()
    tool._governor = _FakeGovernor()
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
    """resume_id alone (no action) resolves script_path from the journal.

    When the journal launch record is absent, the launch path must reject it with a script
    source error (not fall through to a raw governor admission).
    """
    ctl = _FakeController()
    tool = _make_tool(ctl)
    # No journal launch record — journal resolve returns None.
    tool._resolve_resume_record = AsyncMock(return_value=None)
    tool._restore_resume_args_from_journal = AsyncMock(return_value=None)
    out = await tool.invoke({"resume_id": "wf_1"})
    assert not out.success
    assert "script" in out.error.lower()

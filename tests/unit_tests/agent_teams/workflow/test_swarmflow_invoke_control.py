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
    def is_paused(self, run_id=None): return False


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


class _MissController(_FakeController):
    async def stop(self, run_id): self.calls.append(("stop", run_id)); return False


class _RecMessager:
    def __init__(self): self.published = []
    async def publish(self, *, topic_id, message): self.published.append((topic_id, message))


@pytest.mark.asyncio
async def test_stop_on_unregistered_run_announces_stopped_without_seal():
    """After a cold start the controller holds no ticket for a run the embedder
    still shows as paused. stop must still close the card: publish
    WORKFLOW_STOPPED on the team topic (no journal seal — a manual
    resume_id+script_path relaunch stays possible), and report success.
    """
    from openjiuwen.agent_teams.context import set_session_id
    from openjiuwen.agent_teams.schema.events import TeamEvent

    ctl = _MissController()
    tool = _make_tool(ctl)
    tool._messager = _RecMessager()
    tool._team_name = "t"
    set_session_id("s1")

    out = await tool.invoke({"resume_id": "wf_cold", "action": "stop"})

    assert out.success and out.data["status"] == "done"
    assert ctl.calls == [("stop", "wf_cold")]
    (topic, msg), = tool._messager.published
    assert "s1" in topic and "t" in topic
    assert msg.event_type == TeamEvent.WORKFLOW_PROGRESS
    assert msg.payload["kind"] == "workflow_stopped"
    assert msg.payload["run_id"] == "wf_cold"


@pytest.mark.asyncio
async def test_resume_on_unregistered_run_still_reports_not_found():
    """Only stop has the close-the-card fallback; resume needs a real ticket."""
    class _Ctl(_FakeController):
        async def resume(self, run_id): return False
    tool = _make_tool(_Ctl())
    tool._messager = _RecMessager()
    tool._team_name = "t"
    out = await tool.invoke({"resume_id": "wf_cold", "action": "resume"})
    assert not out.success and out.data["status"] == "not_found"
    assert tool._messager.published == []


@pytest.mark.asyncio
async def test_stop_on_paused_run_announces_stopped():
    """A paused run has already unwound: the controller only drops its ticket
    and the engine never emits WORKFLOW_STOPPED. The tool must announce it so
    the embedder's card closes — same as the cold-start case, but here the
    controller reports success.
    """
    from openjiuwen.agent_teams.context import set_session_id

    class _Ctl(_FakeController):
        def __init__(self):
            super().__init__(); self.paused = {"wf_p"}
        def is_paused(self, run_id=None):
            return run_id in self.paused
        async def stop(self, run_id):
            self.calls.append(("stop", run_id)); return self.paused.discard(run_id) is None and run_id == "wf_p"

    ctl = _Ctl()
    tool = _make_tool(ctl)
    tool._messager = _RecMessager()
    tool._team_name = "t"
    set_session_id("s1")

    out = await tool.invoke({"resume_id": "wf_p", "action": "stop"})

    assert out.success
    (_topic, msg), = tool._messager.published
    assert msg.payload["kind"] == "workflow_stopped" and msg.payload["run_id"] == "wf_p"


@pytest.mark.asyncio
async def test_stop_on_active_run_does_not_double_announce():
    """An active run's stop is announced by the engine while it unwinds."""
    class _Ctl(_FakeController):
        def is_paused(self, run_id=None): return False
    tool = _make_tool(_Ctl())
    tool._messager = _RecMessager()
    tool._team_name = "t"
    out = await tool.invoke({"resume_id": "wf_a", "action": "stop"})
    assert out.success and tool._messager.published == []

# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Leader-side swarmflow wiring: WorkflowHandler narration + SwarmflowTool launch.

No real LLM / team: a fake round captures ``deliver_input`` and a fake harness
captures ``launch_async_tool``, so the progress-event → narration path and the
tool → background-launch path are verified deterministically. Completion is fed
back by the async-tool framework (not narrated by the handler), so the handler
only narrates mid-run milestones (started / phase).
"""
from __future__ import annotations

import asyncio

from openjiuwen.agent_teams.agent.coordination.handlers.workflow import WorkflowHandler
from openjiuwen.agent_teams.schema.events import (
    EventMessage,
    TeamEvent,
    WorkflowProgressTeamEvent,
)
from openjiuwen.agent_teams.prompts.sections import TeamSectionName, build_team_static_sections
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.agent_teams.workflow.tool_swarmflow import SwarmflowTool


class _FakeRound:
    """Captures deliver_input; satisfies the round/lifecycle host surface."""

    def __init__(self) -> None:
        self.delivered: list[str] = []

    async def deliver_input(self, content, *, use_steer: bool = True) -> None:
        self.delivered.append(content)


class _FakeBlueprint:
    def __init__(self, role: TeamRole) -> None:
        self.role = role


class _FakeRuntime:
    """Stand-in for AsyncToolRuntime exposing only ``has_running``."""

    def __init__(self) -> None:
        self.running: set[str] = set()

    def has_running(self, tool_name: str) -> bool:
        return tool_name in self.running


class _FakeHarness:
    """Stand-in for NativeHarness: captures launch_async_tool, exposes runtime."""

    def __init__(self) -> None:
        self.model = None
        self.launched: list[tuple] = []
        self._runtime = _FakeRuntime()

    @property
    def async_tool_runtime(self) -> _FakeRuntime:
        return self._runtime

    def launch_async_tool(self, task_id, coro_factory, *, tool_name, description) -> None:
        self.launched.append((task_id, tool_name, description))


def _handler(role: TeamRole) -> tuple[WorkflowHandler, _FakeRound]:
    host = _FakeRound()
    handler = WorkflowHandler(host, _FakeBlueprint(role), infra=None, poll_ctrl=None)
    return handler, host


def _event(kind: str, *, phase: str | None = None, name: str | None = None) -> EventMessage:
    return EventMessage.from_event(
        WorkflowProgressTeamEvent(team_name="t", kind=kind, phase=phase, workflow_name=name)
    )


def _tool(harness: _FakeHarness, language: str = "cn") -> SwarmflowTool:
    return SwarmflowTool(
        parent_agent=harness,
        messager=None,
        team_backend=None,
        team_name="t",
        model_resolver=None,
        language=language,
    )


def test_leader_narrates_started_and_phase_but_not_completion():
    """started / phase narrate; completion is fed back by the framework, not here."""
    handler, host = _handler(TeamRole.LEADER)
    asyncio.run(handler.on_workflow_progress(_event("workflow_started", name="research")))
    asyncio.run(handler.on_workflow_progress(_event("phase", phase="Search")))
    asyncio.run(handler.on_workflow_progress(_event("workflow_completed", name="research")))
    assert len(host.delivered) == 2
    assert "research" in host.delivered[0]
    assert "Search" in host.delivered[1]


def test_per_agent_events_are_not_narrated():
    """agent_started / agent_completed are too chatty — they are not delivered."""
    handler, host = _handler(TeamRole.LEADER)
    asyncio.run(handler.on_workflow_progress(_event("agent_started", phase="Search")))
    asyncio.run(handler.on_workflow_progress(_event("agent_completed", phase="Search")))
    assert host.delivered == []


def test_non_leader_never_narrates():
    """Only the leader is the spectator; a teammate ignores progress events."""
    handler, host = _handler(TeamRole.TEAMMATE)
    asyncio.run(handler.on_workflow_progress(_event("phase", phase="Search")))
    assert host.delivered == []


def test_swarmflow_tool_launches_and_returns_immediately():
    """The tool launches in the background and reports 'launched' with a task id."""
    harness = _FakeHarness()
    tool = _tool(harness)
    out = asyncio.run(tool.invoke({"script_path": "/tmp/flow.py", "args": "question"}))
    assert out.success is True
    assert out.data["status"] == "launched"
    assert "task_id" in out.data
    assert len(harness.launched) == 1
    assert harness.launched[0][1] == "swarmflow"


def test_swarmflow_tool_requires_script_path():
    """Missing script_path fails fast at the tool boundary."""
    tool = _tool(_FakeHarness(), language="en")
    out = asyncio.run(tool.invoke({}))
    assert out.success is False
    assert "script_path" in (out.error or "")


def test_swarmflow_tool_refuses_concurrent_run():
    """A second launch is refused while one swarmflow is already running."""
    harness = _FakeHarness()
    harness.async_tool_runtime.running.add("swarmflow")
    tool = _tool(harness)
    out = asyncio.run(tool.invoke({"script_path": "/tmp/flow.py"}))
    assert out.success is False
    assert "in progress" in (out.error or "")
    assert harness.launched == []


def _section_names(role: TeamRole, *, enable_swarmflow: bool) -> list[str]:
    return [
        s.name
        for s in build_team_static_sections(
            role=role,
            persona="",
            member_name="m",
            enable_swarmflow=enable_swarmflow,
        )
    ]


def test_swarmflow_section_only_for_enabled_leader():
    """The spectator sub-mode section is leader-only and capability-gated."""
    assert TeamSectionName.SWARMFLOW in _section_names(TeamRole.LEADER, enable_swarmflow=True)
    assert TeamSectionName.SWARMFLOW not in _section_names(TeamRole.LEADER, enable_swarmflow=False)
    assert TeamSectionName.SWARMFLOW not in _section_names(TeamRole.TEAMMATE, enable_swarmflow=True)

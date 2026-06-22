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
from typing import Any

from openjiuwen.agent_teams.agent.coordination.handlers.workflow import WorkflowHandler
from openjiuwen.agent_teams.schema.events import (
    EventMessage,
    TeamEvent,
    WorkflowProgressTeamEvent,
)
from openjiuwen.agent_teams.workflow.engine.progress import PhasePlan
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


def _event(kind: str, *, phase: str | None = None, name: str | None = None,
           prompt: str | None = None, model: str | None = None,
           phases: list[PhasePlan] | None = None, label: str | None = None,
           outcome: str | None = None, text: str | None = None,
           correlation_id: str | None = None) -> EventMessage:
    return EventMessage.from_event(
        WorkflowProgressTeamEvent(
            team_name="t", kind=kind, phase=phase, workflow_name=name,
            prompt=prompt, model=model, phases=phases, label=label,
            outcome=outcome, text=text, correlation_id=correlation_id,
        )
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


def test_leader_narrates_human_prompt_with_correlation_id():
    """A human_prompt is narrated with the question and the reply correlation id."""
    handler, host = _handler(TeamRole.LEADER)
    asyncio.run(
        handler.on_workflow_progress(
            _event("human_prompt", label="oncall", prompt="approve rollout?", correlation_id="c-42")
        )
    )
    assert len(host.delivered) == 1
    line = host.delivered[0]
    assert "approve rollout?" in line and "oncall" in line and "c-42" in line


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


def test_swarmflow_tool_requires_a_script_source():
    """No script source at all fails fast at the tool boundary."""
    tool = _tool(_FakeHarness(), language="en")
    out = asyncio.run(tool.invoke({}))
    assert out.success is False
    assert "script_path" in (out.error or "")


def test_swarmflow_tool_rejects_unsupported_sources():
    """script / name / resume_id are on the surface but not wired to execution yet."""
    tool = _tool(_FakeHarness())
    for src in ("script", "name", "resume_id"):
        out = asyncio.run(tool.invoke({src: "x"}))
        assert out.success is False
        assert "not supported yet" in (out.error or ""), (src, out.error)


def test_swarmflow_tool_refuses_concurrent_run():
    """A second launch is refused while one swarmflow is already running."""
    harness = _FakeHarness()
    harness.async_tool_runtime.running.add("swarmflow")
    tool = _tool(harness)
    out = asyncio.run(tool.invoke({"script_path": "/tmp/flow.py"}))
    assert out.success is False
    assert "in progress" in (out.error or "")
    assert harness.launched == []


def test_workflow_started_payload_carries_phases():
    """The workflow_started event payload includes the META phases plan."""
    msg = _event("workflow_started", name="research",
                 phases=[PhasePlan(title="Search"), PhasePlan(title="Analyze"), PhasePlan(title="Report")])
    payload = msg.get_payload()
    assert isinstance(payload, WorkflowProgressTeamEvent)
    assert len(payload.phases) == 3
    assert payload.phases[0].title == "Search"
    assert payload.phases[1].title == "Analyze"
    assert payload.phases[2].title == "Report"


def test_workflow_started_payload_accepts_meta_dict_phases():
    """META phases are normalized to PhasePlan with title and description."""
    meta_phases = [
        PhasePlan(title="发牌", description="分配身份"),
        PhasePlan(title="游戏进行"),
        PhasePlan(title="结算"),
    ]
    msg = _event("workflow_started", name="werewolf", phases=meta_phases)
    payload = msg.get_payload()
    assert isinstance(payload, WorkflowProgressTeamEvent)
    assert len(payload.phases) == 3
    assert payload.phases[0].title == "发牌"
    assert payload.phases[0].description == "分配身份"
    assert payload.phases[1].title == "游戏进行"
    assert payload.phases[1].description is None


def test_agent_started_payload_carries_prompt_and_model():
    """The agent_started event payload includes prompt and model hint."""
    msg = _event("agent_started", phase="Search", prompt="Find recent papers", model="claude-opus")
    payload = msg.get_payload()
    assert isinstance(payload, WorkflowProgressTeamEvent)
    assert payload.prompt == "Find recent papers"
    assert payload.model == "claude-opus"


def test_payload_backward_compatible_without_new_fields():
    """Events without prompt/model/phases still deserialize correctly."""
    msg = _event("phase", phase="Search")
    payload = msg.get_payload()
    assert isinstance(payload, WorkflowProgressTeamEvent)
    assert payload.prompt is None
    assert payload.model is None
    assert payload.phases is None


def test_workflow_failed_payload_carries_error():
    """The workflow_failed event payload carries the error in the text field."""
    msg = _event("workflow_failed", name="research", text="RuntimeError: boom")
    payload = msg.get_payload()
    assert isinstance(payload, WorkflowProgressTeamEvent)
    assert payload.kind == "workflow_failed"
    assert payload.text == "RuntimeError: boom"


def test_agent_failed_payload_carries_error():
    """The agent_failed event payload carries the failure reason in text."""
    msg = _event("agent_failed", phase="Search", label="search-agent", text="failed after 3 attempts")
    payload = msg.get_payload()
    assert isinstance(payload, WorkflowProgressTeamEvent)
    assert payload.kind == "agent_failed"
    assert payload.label == "search-agent"
    assert payload.text == "failed after 3 attempts"


def test_workflow_failed_not_narrated_by_handler():
    """WORKFLOW_FAILED is not narrated by the handler — fed back by async_tool framework."""
    handler, host = _handler(TeamRole.LEADER)
    asyncio.run(handler.on_workflow_progress(_event("workflow_failed", name="research", text="error")))
    assert host.delivered == []


def test_agent_failed_not_narrated_by_handler():
    """AGENT_FAILED is not narrated by the handler — per-agent progress is too chatty."""
    handler, host = _handler(TeamRole.LEADER)
    asyncio.run(handler.on_workflow_progress(_event("agent_failed", phase="Search", label="agent-1", text="failed")))
    assert host.delivered == []

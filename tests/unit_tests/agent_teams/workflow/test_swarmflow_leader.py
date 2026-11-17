# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Swarmflow wiring: WorkflowHandler narration (launcher-routed) + SwarmflowTool launch.

No real LLM / team: a fake round captures ``deliver_input`` and a fake harness
captures ``launch_async_tool``, so the progress-event → narration path and the
tool → background-launch path are verified deterministically. Completion is fed
back by the async-tool framework (not narrated by the handler); pause/resume
are not narrated either (the run card flips from the raw events), so the
handler narrates the mid-run milestones only (started / phase / human turns).
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import fields
from typing import Any
from unittest.mock import patch

import pytest

from openjiuwen.agent_teams.agent.coordination.handlers.workflow import WorkflowHandler
from openjiuwen.agent_teams.i18n import set_language
from openjiuwen.agent_teams.schema.events import (
    EventMessage,
    WorkflowProgressTeamEvent,
)
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.agent_teams.workflow.concurrency import ConcurrencyGovernor, ConcurrencyLimits
from openjiuwen.agent_teams.workflow.engine.progress import PhasePlan
from openjiuwen.agent_teams.workflow.tool_swarmflow import SwarmflowTool


class _FakeRound:
    """Captures deliver_input; satisfies the round/lifecycle host surface."""

    def __init__(self) -> None:
        self.delivered: list[str] = []

    async def deliver_input(self, content, *, use_steer: bool = True) -> None:
        self.delivered.append(content)


class _FakeBlueprint:
    def __init__(self, role: TeamRole, member_name: str | None = None) -> None:
        self.role = role
        self.member_name = member_name


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
        # (format_completed, format_failed) parallel to ``launched`` — lets a
        # test render the completion injection the harness would receive.
        self.launch_formatters: list[tuple] = []
        self._runtime = _FakeRuntime()
        self.build_context = None

    @property
    def async_tool_runtime(self) -> _FakeRuntime:
        return self._runtime

    def launch_async_tool(
        self,
        task_id,
        coro_factory,
        *,
        tool_name,
        description,
        format_completed=None,
        format_failed=None,
    ) -> None:
        self.launched.append((task_id, tool_name, description))
        self.launch_formatters.append((format_completed, format_failed))


class _FailingLaunchHarness(_FakeHarness):
    def launch_async_tool(self, *args, **kwargs) -> None:
        raise RuntimeError("launch failed")


def _handler(role: TeamRole, member_name: str | None = None) -> tuple[WorkflowHandler, _FakeRound]:
    host = _FakeRound()
    handler = WorkflowHandler(host, _FakeBlueprint(role, member_name), infra=None, poll_ctrl=None)
    return handler, host


def test_progress_model_fallback_is_native_started_only():
    """The base-spec model fallback applies to a native agent_started with no
    hint and nothing else.

    A third-party node with no hint runs on the CLI's own default model: naming
    the leader's model there showed a model the CLI never ran until the real
    report arrived. Terminal events never fall back, or a model-less
    completion would overwrite what the node already shows.
    """
    from types import SimpleNamespace

    from openjiuwen.agent_teams.workflow.tool_swarmflow import resolve_progress_model

    def ev(kind, model=None, agent_type=None):
        return SimpleNamespace(kind=kind, model=model, agent_type=agent_type)

    assert resolve_progress_model(ev("agent_started"), "deepseek-flash") == "deepseek-flash"
    assert resolve_progress_model(ev("agent_started", agent_type="claude"), "deepseek-flash") is None
    assert resolve_progress_model(ev("agent_started", model="glm-5", agent_type="claude"), "deepseek-flash") == "glm-5"
    assert resolve_progress_model(ev("agent_completed"), "deepseek-flash") is None
    assert resolve_progress_model(ev("agent_completed", model="GLM-5.3"), "deepseek-flash") == "GLM-5.3"


def test_team_event_schema_carries_activity_fields():
    """AGENT_ACTIVITY's four structured fields must survive the engine -> team-event hop.

    The builder threads them by name; a schema gap here silently degrades every
    activity row to an id-less, name-less 'tool_call' default on the consumer
    side (the pairing and the member-call-list mirror both collapse).
    """
    from openjiuwen.agent_teams.schema.events import WorkflowProgressTeamEvent
    from openjiuwen.agent_teams.workflow.engine.progress import WorkflowProgressEvent

    for field in ("activity_type", "tool_call_id", "tool_name", "content"):
        assert field in {f.name for f in fields(WorkflowProgressEvent)}
        assert field in WorkflowProgressTeamEvent.model_fields
    ev = WorkflowProgressTeamEvent(
        team_name="t", kind="agent_activity", activity_type="tool_result",
        tool_call_id="tc_1", tool_name="bash", content="ls -la",
    )
    dumped = ev.model_dump()
    assert dumped["activity_type"] == "tool_result"
    assert dumped["tool_call_id"] == "tc_1"
    assert dumped["tool_name"] == "bash"
    assert dumped["content"] == "ls -la"


def _event(kind: str, *, phase: str | None = None, name: str | None = None,
           run_id: str | None = "wf_abc123def456", prompt: str | None = None,
           model: str | None = None,
           phases: list[PhasePlan] | None = None, label: str | None = None,
           outcome: str | None = None, text: str | None = None,
           correlation_id: str | None = None,
           executor: str | None = None) -> EventMessage:
    return EventMessage.from_event(
        WorkflowProgressTeamEvent(
            team_name="t", kind=kind, phase=phase, workflow_name=name,
            run_id=run_id, prompt=prompt, model=model, phases=phases, label=label,
            outcome=outcome, text=text, correlation_id=correlation_id,
            executor=executor,
        )
    )


def _tool(
    harness: _FakeHarness,
    language: str = "cn",
    governor: ConcurrencyGovernor | None = None,
) -> SwarmflowTool:
    if governor is None:
        l2 = 4
        governor = ConcurrencyGovernor(
            ConcurrencyLimits(max_workflows=16, max_agents_total=64),
            agents_per_run_cap=l2,
        )
    return SwarmflowTool(
        parent_agent=harness,
        messager=None,
        team_name="t",
        model_resolver=None,
        concurrency_governor=governor,
        language=language,
    )


def test_leader_narrates_started_and_phase_but_not_completion():
    """started / phase narrate; completion is fed back by the framework, not here."""
    handler, host = _handler(TeamRole.LEADER)
    asyncio.run(handler.on_workflow_progress(_event("workflow_started", name="research")))
    asyncio.run(handler.on_workflow_progress(_event("phase", phase="Search")))
    asyncio.run(handler.on_workflow_progress(_event("workflow_completed", name="research")))
    assert len(host.delivered) == 2
    # Milestones are wrapped in <team-event kind="workflow"> (F_46 inbound XML).
    assert '<team-event kind="workflow">' in host.delivered[0]
    assert "research" in host.delivered[0]
    assert "wf_abc123def456" in host.delivered[0]
    assert '<team-event kind="workflow">' in host.delivered[1]
    assert "Search" in host.delivered[1]
    assert "wf_abc123def456" in host.delivered[1]


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
    assert '<team-event kind="workflow">' in line
    assert "approve rollout?" in line and "oncall" in line and "c-42" in line


def test_narration_follows_the_executor():
    """Whoever hosts the run gets its milestones; everyone else stays out.

    A teammate-hosted run's progress reaches that teammate only — the leader
    and unrelated members are not spectators. Leader-hosted runs name the
    leader (``executor="leader"``), and events without team context
    (``executor=None``, legacy included) fall back to the leader loop.
    """
    tm_handler, tm_host = _handler(TeamRole.TEAMMATE, member_name="teammate-a")
    asyncio.run(tm_handler.on_workflow_progress(_event("phase", phase="Search", executor="teammate-a")))
    assert len(tm_host.delivered) == 1
    assert "Search" in tm_host.delivered[0]

    lead_handler, lead_host = _handler(TeamRole.LEADER, member_name="leader")
    asyncio.run(lead_handler.on_workflow_progress(_event("phase", phase="Search", executor="teammate-a")))
    assert lead_host.delivered == []

    other_handler, other_host = _handler(TeamRole.TEAMMATE, member_name="teammate-b")
    asyncio.run(other_handler.on_workflow_progress(_event("phase", phase="Search", executor="teammate-a")))
    assert other_host.delivered == []

    # Leader-hosted runs name the leader as executor and narrate there.
    asyncio.run(lead_handler.on_workflow_progress(_event("phase", phase="Search", executor="leader")))
    assert len(lead_host.delivered) == 1

    # Events without team context (executor=None, legacy included) fall back
    # to the leader loop; a teammate loop stays quiet.
    asyncio.run(lead_handler.on_workflow_progress(_event("phase", phase="Search")))
    assert len(lead_host.delivered) == 2

    asyncio.run(tm_handler.on_workflow_progress(_event("phase", phase="Search")))
    assert len(tm_host.delivered) == 1


def test_pause_and_resume_are_not_narrated():
    """Pause does NOT reach any loop as narration — a parked run waits
    silently for the user's decision (narration woke the launcher and invited
    spontaneous resumes). The run card still flips: a resume relaunch
    re-enters the engine, so WORKFLOW_STARTED (kept-tree semantics for
    relaunch_kind="resume") plus the replayed milestones flip the paused
    cards; workflow_state consumes those events directly. The replayed
    started narration is the known L2 replay-narration leftover.
    """
    tm_handler, tm_host = _handler(TeamRole.TEAMMATE, member_name="teammate-a")
    asyncio.run(tm_handler.on_workflow_progress(_event("workflow_paused", executor="teammate-a")))
    assert tm_host.delivered == []

    lead_handler, lead_host = _handler(TeamRole.LEADER, member_name="leader")
    asyncio.run(lead_handler.on_workflow_progress(_event("workflow_paused", executor="teammate-a")))
    asyncio.run(lead_handler.on_workflow_progress(_event("workflow_paused")))
    assert lead_host.delivered == []


def test_swarmflow_tool_launches_and_returns_immediately():
    """The tool launches in the background and reports launched with run_id + task_id."""
    harness = _FakeHarness()
    tool = _tool(harness)
    out = asyncio.run(tool.invoke({"script_path": "/tmp/flow.py", "args": "question"}))
    assert out.success is True
    assert out.data["status"] == "launched"
    assert "task_id" in out.data
    assert out.data["run_id"].startswith("wf_")
    assert len(harness.launched) == 1
    assert harness.launched[0][1] == "swarmflow"
    mapped = tool.render_for_llm(out)
    assert out.data["run_id"] in mapped
    assert out.data["task_id"] in mapped


def test_swarmflow_launched_message_distinguishes_run_id_from_task_id():
    """Launched receipt shows both ids; Leader counts parallel runs by run_id only."""
    tool = _tool(_FakeHarness())
    run_id = "wf_abc123def456"
    task_id = "wxyz1234"
    mapped = tool.format_launched_message(run_id=run_id, task_id=task_id, script_path="/tmp/flow.py")
    assert run_id in mapped
    assert task_id in mapped
    assert "/tmp/flow.py" in mapped
    assert run_id != task_id


def test_format_completed_uses_per_run_completion_ctx():
    """Each invoke's completion_ctx supplies summarize_run for that run only."""
    from openjiuwen.agent_teams.workflow.schema import AgentActivity, PhaseRecord, WorkflowRun

    tool = _tool(_FakeHarness())
    run_a = "wf_runaaaaaaa"
    run_b = "wf_runbbbbbbb"

    def _run(phases: int, agents_per_phase: int) -> WorkflowRun:
        run = WorkflowRun(name="wf", status="completed")
        for p in range(phases):
            acts = [
                AgentActivity(label=f"a{j}", status="done")
                for j in range(agents_per_phase)
            ]
            run.phases.append(PhaseRecord(title=f"p{p}", agents=acts))
        return run

    class _FakeObserver:
        def __init__(self, run: WorkflowRun) -> None:
            self.run = run

    ctx_a: dict[str, Any] = {"observer": _FakeObserver(_run(2, 1))}
    ctx_b: dict[str, Any] = {"observer": _FakeObserver(_run(5, 1))}

    out_a = tool.format_completed_injection("ok", run_id=run_a, completion_ctx=ctx_a)
    out_b = tool.format_completed_injection("ok", run_id=run_b, completion_ctx=ctx_b)
    assert run_a in out_a and "2 phases, 2 agents" in out_a
    assert run_b in out_b and "5 phases, 5 agents" in out_b
    assert "2 phases, 2 agents" not in out_b


def _overrun_observer(events: list[Any]) -> Any:
    """Fake observer: folds nothing, just replays raw events + a stub run."""
    from openjiuwen.agent_teams.workflow.schema import WorkflowRun

    class _Obs:
        def __init__(self) -> None:
            self.events = events
            self.run = WorkflowRun(name="wf", status="completed")

    return _Obs()


def test_format_completed_appends_overrun_block_when_run_finished_past_ceiling():
    """A completed run whose ledger blew the per-run ceiling gets redesign feedback."""
    from openjiuwen.agent_teams.workflow.engine.progress import (
        ProgressKind,
        WorkflowProgressEvent,
    )

    events = [
        WorkflowProgressEvent(
            kind=ProgressKind.AGENT_COMPLETED,
            phase="research",
            label="a",
            tokens=120_000,
            budget={"total": None, "spent": 120_000, "remaining": None, "exhausted": False},
            workflow_budget={"total": 90_000, "spent": 120_000, "remaining": 0, "exhausted": True},
        ),
        WorkflowProgressEvent(
            kind=ProgressKind.AGENT_COMPLETED,
            phase="draft",
            label="b",
            tokens=195_000,
            budget={"total": None, "spent": 315_000, "remaining": None, "exhausted": False},
            workflow_budget={"total": 90_000, "spent": 315_000, "remaining": 0, "exhausted": True},
        ),
        WorkflowProgressEvent(kind=ProgressKind.WORKFLOW_COMPLETED),
    ]
    tool = _tool(_FakeHarness())
    out = tool.format_completed_injection(
        "ok", run_id="wf_overrun1", completion_ctx={"observer": _overrun_observer(events)}
    )
    assert "[Swarmflow 完成]" in out
    assert "[Swarmflow 超预算完成]" in out
    assert "spent=315000/90000" in out
    # Phase ranking is cost-ordered: draft(195000) beats research(120000).
    assert "draft(195000), research(120000)" in out
    assert "重新设计工作流" in out
    assert "wf_overrun1" in out


def test_format_completed_no_overrun_block_when_within_ceiling():
    """A completed run inside its ceiling reports plain success, no overrun text."""
    from openjiuwen.agent_teams.workflow.engine.progress import (
        ProgressKind,
        WorkflowProgressEvent,
    )

    events = [
        WorkflowProgressEvent(
            kind=ProgressKind.AGENT_COMPLETED,
            phase="research",
            label="a",
            tokens=40_000,
            budget={"total": None, "spent": 40_000, "remaining": None, "exhausted": False},
            workflow_budget={"total": 90_000, "spent": 40_000, "remaining": 50_000, "exhausted": False},
        ),
        WorkflowProgressEvent(kind=ProgressKind.WORKFLOW_COMPLETED),
    ]
    tool = _tool(_FakeHarness())
    out = tool.format_completed_injection(
        "ok", run_id="wf_within1", completion_ctx={"observer": _overrun_observer(events)}
    )
    assert "[Swarmflow 完成]" in out
    assert "超预算" not in out


def test_format_completed_overrun_block_reports_session_scope_with_run_contrast():
    """Session-scope overrun wins the report and carries the per-run contrast."""
    from openjiuwen.agent_teams.workflow.engine.progress import (
        ProgressKind,
        WorkflowProgressEvent,
    )

    events = [
        WorkflowProgressEvent(
            kind=ProgressKind.AGENT_COMPLETED,
            phase="research",
            label="a",
            tokens=500_000,
            budget={"total": 400_000, "spent": 500_000, "remaining": 0, "exhausted": True},
            workflow_budget={"total": 90_000, "spent": 500_000, "remaining": 0, "exhausted": True},
        ),
        WorkflowProgressEvent(kind=ProgressKind.WORKFLOW_COMPLETED),
    ]
    tool = _tool(_FakeHarness())
    out = tool.format_completed_injection(
        "ok", run_id="wf_session1", completion_ctx={"observer": _overrun_observer(events)}
    )
    assert "[Swarmflow 超预算完成]" in out
    assert "session（会话总额）" in out
    assert "spent=500000/400000" in out
    assert "Run 级对照：spent=500000/90000" in out
    assert "请求上调预算" in out


def test_swarmflow_tool_requires_a_script_source():
    """No script source at all fails fast at the tool boundary."""
    tool = _tool(_FakeHarness(), language="en")
    out = asyncio.run(tool.invoke({}))
    assert out.success is False
    assert "script_path" in (out.error or "")


def test_swarmflow_tool_rejects_unsupported_sources():
    """name is on the surface but not wired to execution yet.

    (resume_id alone is no longer "not supported yet": resume_id+action is the
    control entry, and resume_id without action fails with its own message —
    covered in test_swarmflow_invoke_control.py.)
    """
    tool = _tool(_FakeHarness())
    out = asyncio.run(tool.invoke({"name": "x"}))
    assert out.success is False
    assert "not supported yet" in (out.error or "")


def test_swarmflow_tool_launches_inline_script(tmp_path):
    """Inline ``script`` is materialised at invoke; the receipt reports its path."""
    from openjiuwen.agent_teams import paths

    paths.configure_openjiuwen_home(tmp_path)
    try:
        harness = _FakeHarness()
        tool = _tool(harness)
        out = asyncio.run(tool.invoke({"script": "META = {'name': 'x'}\nasync def run(args):\n    return 1\n"}))
        assert out.success is True
        assert out.data["status"] == "launched"
        run_id = out.data["run_id"]
        assert run_id.startswith("wf_")
        # inline source is written to a per-run file; the receipt carries the path
        script_path = out.data["script_path"]
        assert script_path.endswith(os.path.join("workflows", "x", f"script-{run_id}.py"))
        assert os.path.isfile(script_path)
        assert len(harness.launched) == 1
        assert harness.launched[0][2] == f"swarmflow: {script_path}"
        assert script_path in tool.render_for_llm(out)
    finally:
        paths.reset_openjiuwen_home()


def test_swarmflow_tool_inline_scripts_with_same_name_do_not_overwrite(tmp_path):
    """Two inline runs sharing META.name get separate script files.

    A shared script.py let the second run's materialisation overwrite the
    first one's source; resuming the first then loaded the second's script.
    With per-run names each run keeps its own snapshot.
    """
    from openjiuwen.agent_teams import paths

    paths.configure_openjiuwen_home(tmp_path)
    try:
        harness = _FakeHarness()
        tool = _tool(harness)
        first = asyncio.run(tool.invoke({"script": "META = {'name': 'x'}\nasync def run(args):\n    return 1\n"}))
        second = asyncio.run(tool.invoke({"script": "META = {'name': 'x'}\nasync def run(args):\n    return 2\n"}))
        assert first.success is True and second.success is True
        path_a, path_b = first.data["script_path"], second.data["script_path"]
        assert path_a != path_b
        with open(path_a, encoding="utf-8") as f:
            assert "return 1" in f.read()
        with open(path_b, encoding="utf-8") as f:
            assert "return 2" in f.read()
        assert os.path.isfile(path_a) and os.path.isfile(path_b)
    finally:
        paths.reset_openjiuwen_home()


def test_swarmflow_tool_inline_script_relaunch_reuses_resume_id_path(tmp_path):
    """A resume_id relaunch reuses the run_id (same per-run script file path)."""
    from openjiuwen.agent_teams import paths

    paths.configure_openjiuwen_home(tmp_path)
    try:
        harness = _FakeHarness()
        tool = _tool(harness)
        source = "META = {'name': 'x'}\nasync def run(args):\n    return 1\n"
        first = asyncio.run(tool.invoke({"script": source, "resume_id": "wf_abc123"}))
        assert first.success is True
        assert first.data["run_id"] == "wf_abc123"
        script_path = first.data["script_path"]
        assert script_path.endswith(os.path.join("workflows", "x", "script-wf_abc123.py"))
        # a relaunch under the same resume_id rewrites the same file
        relaunch = asyncio.run(tool.invoke({"script": source, "resume_id": "wf_abc123"}))
        assert relaunch.success is True
        assert relaunch.data["script_path"] == script_path
    finally:
        paths.reset_openjiuwen_home()


def test_cold_start_relaunch_hosts_completion_injection_on_the_recoverer(tmp_path):
    """A cold-start recovery relaunch is the leader's own tool round: the
    leader, reading the resume advisory, calls swarmflow with
    resume_id + script_path from ITS harness — so the background task (and
    its terminal completion injection) lands on the recoverer's runtime,
    never routed back to the departed member.
    """
    from types import SimpleNamespace

    from openjiuwen.agent_teams import paths

    paths.configure_openjiuwen_home(tmp_path)
    try:
        source = "META = {'name': 'recover'}\nasync def run(args):\n    return 7\n"
        member = _FakeHarness()
        member.build_context = SimpleNamespace(role="teammate", member_name="alice")
        member_tool = _tool(member)
        first = asyncio.run(
            member_tool.invoke({"script": source, "resume_id": "wf_recover1"})
        )
        assert first.success is True
        assert first.data["run_id"] == "wf_recover1"
        script_path = first.data["script_path"]
        assert len(member.launched) == 1

        # Cold start: a fresh process holds no controller tickets, so the
        # recovery goes through the leader's own tool invocation.
        recoverer = _FakeHarness()
        recoverer.build_context = SimpleNamespace(role="leader", member_name=None)
        recoverer_tool = _tool(recoverer)
        out = asyncio.run(
            recoverer_tool.invoke({"resume_id": "wf_recover1", "script_path": script_path})
        )
        assert out.success is True
        assert out.data["run_id"] == "wf_recover1"

        # the recoverer hosts the relaunch; the member's runtime is untouched
        assert len(recoverer.launched) == 1
        assert len(member.launched) == 1

        # the completion injection the recoverer will receive names the run
        format_completed, _format_failed = recoverer.launch_formatters[0]
        assert format_completed is not None
        assert "wf_recover1" in format_completed("done")
    finally:
        paths.reset_openjiuwen_home()


def test_resume_relaunch_registers_terminal_formatters(tmp_path):
    """The controller-plane relaunch registers the same terminal formatters as
    a fresh invoke, so the resumed run's completion injection names the run:
    whoever hosts the relaunch (the member when alive, the leader fallback when
    it is gone) learns WHICH run finished instead of the generic
    async-tool text."""
    from openjiuwen.agent_teams import paths

    paths.configure_openjiuwen_home(tmp_path)
    try:
        harness = _FakeHarness()
        tool = _tool(harness)
        inputs = {
            "_run_id": "wf_resumefmt",
            "_workflow_ticket": None,
            "_agent_gate": None,
            "_completion_ctx": {},
            "script_path": "/tmp/flow.py",
        }
        tool.relaunch(inputs, "sess-1")

        assert len(harness.launched) == 1
        format_completed, format_failed = harness.launch_formatters[0]
        assert format_completed is not None
        assert "wf_resumefmt" in format_completed("done")
        assert format_failed is not None
        assert "wf_resumefmt" in format_failed("boom")
    finally:
        paths.reset_openjiuwen_home()


def test_swarmflow_tool_refuses_when_concurrent_cap_reached():
    """A second launch is refused when the governor L1 cap is exhausted."""
    harness = _FakeHarness()
    governor = ConcurrencyGovernor(
        ConcurrencyLimits(max_workflows=1, max_agents_total=4),
        agents_per_run_cap=2,
    )
    tool = _tool(harness, governor=governor)
    first = asyncio.run(tool.invoke({"script_path": "/tmp/flow.py"}))
    assert first.success is True
    second = asyncio.run(tool.invoke({"script_path": "/tmp/other.py"}))
    assert second.success is False
    assert "(1/1)" in (second.error or "")
    assert len(harness.launched) == 1


def test_invoke_release_ticket_when_launch_fails():
    """Admit succeeds but launch_async_tool fails — L1 ticket is released for retry."""
    governor = ConcurrencyGovernor(
        ConcurrencyLimits(max_workflows=1, max_agents_total=4),
        agents_per_run_cap=2,
    )
    failing_tool = _tool(_FailingLaunchHarness(), governor=governor)
    failed = asyncio.run(failing_tool.invoke({"script_path": "/tmp/flow.py"}))
    assert failed.success is False
    assert "launch failed" in (failed.error or "")

    harness = _FakeHarness()
    retry_tool = _tool(harness, governor=governor)
    ok = asyncio.run(retry_tool.invoke({"script_path": "/tmp/flow.py"}))
    assert ok.success is True
    assert len(harness.launched) == 1


def test_swarmflow_tool_allows_up_to_max_workflows():
    """L1 cap allows exactly max_workflows concurrent admits."""
    harness = _FakeHarness()
    governor = ConcurrencyGovernor(
        ConcurrencyLimits(max_workflows=3, max_agents_total=12),
        agents_per_run_cap=2,
    )
    tool = _tool(harness, governor=governor)
    for i in range(3):
        out = asyncio.run(tool.invoke({"script_path": f"/tmp/flow{i}.py"}))
        assert out.success is True
    blocked = asyncio.run(tool.invoke({"script_path": "/tmp/extra.py"}))
    assert blocked.success is False
    assert "(3/3)" in (blocked.error or "")
    assert len(harness.launched) == 3


def test_swarmflow_concurrent_launches_get_distinct_run_ids():
    """SDD §7: max_workflows=2 — both launch; each invoke mints a unique run_id."""
    harness = _FakeHarness()
    governor = ConcurrencyGovernor(
        ConcurrencyLimits(max_workflows=2, max_agents_total=8),
        agents_per_run_cap=2,
    )
    tool = _tool(harness, governor=governor)
    run_ids: list[str] = []
    for path in ("/tmp/flow_a.py", "/tmp/flow_b.py"):
        out = asyncio.run(tool.invoke({"script_path": path}))
        assert out.success is True
        run_ids.append(out.data["run_id"])
    assert len(run_ids) == 2
    assert run_ids[0] != run_ids[1]
    assert all(rid.startswith("wf_") for rid in run_ids)


@pytest.mark.asyncio
async def test_run_background_finally_releases_governor_on_cancel():
    """SDD §7: run_background.finally releases L1 even when the run is cancelled."""
    governor = ConcurrencyGovernor(
        ConcurrencyLimits(max_workflows=1, max_agents_total=4),
        agents_per_run_cap=2,
    )
    tool = _tool(_FakeHarness(), governor=governor)
    admission = await governor.admit_workflow()
    assert admission is not None
    inputs = {
        "_run_id": "wf_cancelrelease",
        "_workflow_ticket": admission.ticket,
        "_agent_gate": admission.agent_gate,
        "_completion_ctx": {},
        "script_path": "/tmp/flow.py",
    }
    with patch(
        "openjiuwen.agent_teams.workflow.runner.run_swarmflow",
        side_effect=asyncio.CancelledError(),
    ):
        with pytest.raises(asyncio.CancelledError):
            await tool.run_background("task-cancel", inputs)
    assert await governor.admit_workflow() is not None


def test_swarmflow_leader_messages_use_i18n_per_language():
    """Launched / started / phase / completed carry the same run_id in cn and en."""
    run_id = "wf_abc123def456"
    task_id = "wxyz1234"
    workflow_name = "research"
    phase = "Search"

    for lang, started_hint, phase_hint, completed_hint in (
        ("cn", "编排", "进入阶段", "完成"),
        ("en", "Orchestration", "entering phase", "completed"),
    ):
        set_language(lang)
        tool = _tool(_FakeHarness(), language=lang)
        launched = tool.format_launched_message(run_id=run_id, task_id=task_id, script_path="/tmp/flow.py")
        assert run_id in launched and task_id in launched

        completed = tool.format_completed_injection("2 phases, 3 agents", run_id=run_id)
        assert run_id in completed
        assert completed_hint in completed

        failed = tool.format_failed_injection("boom", run_id=run_id)
        assert run_id in failed

        handler, host = _handler(TeamRole.LEADER)
        asyncio.run(
            handler.on_workflow_progress(
                _event("workflow_started", name=workflow_name, run_id=run_id)
            )
        )
        asyncio.run(handler.on_workflow_progress(_event("phase", phase=phase, run_id=run_id)))
        assert len(host.delivered) == 2
        assert run_id in host.delivered[0]
        assert started_hint in host.delivered[0]
        assert workflow_name in host.delivered[0]
        assert run_id in host.delivered[1]
        assert phase_hint in host.delivered[1]
        assert phase in host.delivered[1]


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


def test_agent_started_payload_carries_agent_type():
    """agent_type rides the cross-process team event; absent means native."""
    ext = EventMessage.from_event(
        WorkflowProgressTeamEvent(team_name="t", kind="agent_started", label="ext", agent_type="codex")
    ).get_payload()
    native = _event("agent_started", label="native").get_payload()
    assert ext.agent_type == "codex"
    assert native.agent_type is None


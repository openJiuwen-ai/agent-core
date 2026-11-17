# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""WorkflowObserver.summarize_run + the event -> 4-layer ``WorkflowRun`` fold."""
from __future__ import annotations

from openjiuwen.agent_teams.workflow.engine.progress import ProgressKind, WorkflowProgressEvent
from openjiuwen.agent_teams.workflow.observer import summarize_run
from openjiuwen.agent_teams.workflow.schema import (
    AgentActivity,
    PhaseRecord,
    WorkflowRun,
    build_workflow_run_from_events,
)


def test_summarize_run_counts_phases_and_agents():
    """The summary folds the 4-layer run into 'N phases, M agents'."""
    run = WorkflowRun(
        phases=[
            PhaseRecord(title="Search", agents=[AgentActivity(label="a"), AgentActivity(label="b")]),
            PhaseRecord(title="Synthesize", agents=[AgentActivity(label="c")]),
        ]
    )
    assert summarize_run(run) == "2 phases, 3 agents"


def test_summarize_run_handles_empty():
    """An empty run reports zero phases and agents."""
    assert summarize_run(WorkflowRun()) == "0 phases, 0 agents"


def test_agent_activity_is_folded_into_its_node():
    """AGENT_ACTIVITY narration lands on the running agent's activity trail."""
    events = [
        WorkflowProgressEvent(kind=ProgressKind.WORKFLOW_STARTED, name="wf"),
        WorkflowProgressEvent(kind=ProgressKind.PHASE, phase="Build"),
        WorkflowProgressEvent(
            kind=ProgressKind.AGENT_STARTED, phase="Build", label="coder", agent_id="a1"
        ),
        WorkflowProgressEvent(
            kind=ProgressKind.AGENT_ACTIVITY,
            phase="Build",
            label="coder",
            agent_id="a1",
            message="tool: write_file",
        ),
        WorkflowProgressEvent(
            kind=ProgressKind.AGENT_ACTIVITY,
            phase="Build",
            label="coder",
            agent_id="a1",
            message="tool: bash",
        ),
    ]

    run = build_workflow_run_from_events(events)

    agent = run.phases[0].agents[0]
    assert agent.agent_id == "a1"
    assert agent.activity == ["tool: write_file", "tool: bash"]


def test_agent_activity_disambiguates_same_label_by_agent_id():
    """Two same-label nodes: activity is attributed by agent_id, not label order."""
    events = [
        WorkflowProgressEvent(kind=ProgressKind.PHASE, phase="Build"),
        WorkflowProgressEvent(
            kind=ProgressKind.AGENT_STARTED, phase="Build", label="coder", agent_id="a1"
        ),
        WorkflowProgressEvent(
            kind=ProgressKind.AGENT_STARTED, phase="Build", label="coder", agent_id="a2"
        ),
        WorkflowProgressEvent(
            kind=ProgressKind.AGENT_ACTIVITY,
            phase="Build",
            label="coder",
            agent_id="a2",
            message="tool: a2-only",
        ),
    ]

    run = build_workflow_run_from_events(events)

    first, second = run.phases[0].agents
    assert first.activity == []
    assert second.activity == ["tool: a2-only"]


def test_agent_activity_falls_back_to_label_without_agent_id():
    """Producers that omit agent_id still attribute activity by label."""
    events = [
        WorkflowProgressEvent(kind=ProgressKind.PHASE, phase="Build"),
        WorkflowProgressEvent(kind=ProgressKind.AGENT_STARTED, phase="Build", label="coder"),
        WorkflowProgressEvent(
            kind=ProgressKind.AGENT_ACTIVITY, phase="Build", label="coder", message="tool: x"
        ),
    ]

    run = build_workflow_run_from_events(events)

    assert run.phases[0].agents[0].activity == ["tool: x"]

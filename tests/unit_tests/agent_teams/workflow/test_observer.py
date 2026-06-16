# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""WorkflowObserver.summarize_run: the one-line leader-facing run summary."""
from __future__ import annotations

from openjiuwen.agent_teams.workflow.observer import summarize_run
from openjiuwen.agent_teams.workflow.schema import AgentActivity, PhaseRecord, WorkflowRun


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

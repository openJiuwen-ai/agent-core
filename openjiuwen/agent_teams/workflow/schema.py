# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The 4-layer structured representation of a swarmflow run.

``WorkflowRun`` → ``PhaseRecord`` → ``AgentActivity`` → ``{prompt, activity,
outcome}`` is the shape the TUI console renders (Phase ▸ agents ▸ one agent's
prompt / activity / outcome). It is built by replaying the engine's
:class:`WorkflowProgressEvent` stream — the preprocessing dry-run (MockBackend)
produces the same shape with zero network, so the frontend can show the planned
workflow before it executes for real.

The ``to_frontend`` transport channel is intentionally left as a stub on the
observer; this module only owns the data model and the event → model fold.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from openjiuwen.agent_teams.workflow.engine.progress import ProgressKind, WorkflowProgressEvent

_NO_PHASE = "(unphased)"


class AgentActivity(BaseModel):
    """One ``agent()`` call: its prompt, narration trail, and outcome (layer 4)."""

    label: str | None = None
    prompt: str | None = None
    activity: list[str] = Field(default_factory=list)
    outcome: str | None = None
    status: str = "running"  # "running" until its AGENT_COMPLETED arrives


class PhaseRecord(BaseModel):
    """One ``phase()`` group and the agents that ran under it (layer 2 + 3)."""

    title: str
    agents: list[AgentActivity] = Field(default_factory=list)


class WorkflowRun(BaseModel):
    """The whole run: ordered phases (layer 1) and overall status."""

    name: str | None = None
    status: str = "running"  # "running" until WORKFLOW_COMPLETED
    phases: list[PhaseRecord] = Field(default_factory=list)


def build_workflow_run_from_events(events: list[WorkflowProgressEvent]) -> WorkflowRun:
    """Fold a progress-event stream into the 4-layer ``WorkflowRun``.

    Robust to the concurrent fan-out of ``parallel`` / ``pipeline`` (agent
    start/complete events from different branches interleave): agents are
    matched to their phase by ``event.phase`` and to their completion by the
    most recent still-running activity with the same label in that phase.
    """
    run = WorkflowRun()
    phases: dict[str, PhaseRecord] = {}

    def phase_for(title: str | None) -> PhaseRecord:
        key = title or _NO_PHASE
        rec = phases.get(key)
        if rec is None:
            rec = PhaseRecord(title=key)
            phases[key] = rec
            run.phases.append(rec)
        return rec

    for ev in events:
        if ev.kind == ProgressKind.WORKFLOW_STARTED:
            run.name = ev.message
        elif ev.kind == ProgressKind.WORKFLOW_COMPLETED:
            run.status = "completed"
        elif ev.kind == ProgressKind.WORKFLOW_FAILED:
            run.status = "failed"
        elif ev.kind == ProgressKind.PHASE:
            phase_for(ev.phase)
        elif ev.kind == ProgressKind.AGENT_STARTED:
            phase_for(ev.phase).agents.append(
                AgentActivity(label=ev.label, prompt=ev.prompt, status="running")
            )
        elif ev.kind == ProgressKind.AGENT_COMPLETED:
            activity = _latest_running(phase_for(ev.phase), ev.label)
            if activity is not None:
                activity.outcome = ev.outcome
                activity.status = "completed"
        elif ev.kind == ProgressKind.AGENT_FAILED:
            activity = _latest_running(phase_for(ev.phase), ev.label)
            if activity is not None:
                activity.outcome = ev.message
                activity.status = "failed"
        elif ev.kind == ProgressKind.LOG:
            rec = phase_for(ev.phase)
            target = _latest_running(rec, None) or (rec.agents[-1] if rec.agents else None)
            if target is not None and ev.message:
                target.activity.append(ev.message)
    return run


def _latest_running(rec: PhaseRecord, label: str | None) -> AgentActivity | None:
    """Most recent still-running agent in ``rec`` (optionally matching label)."""
    for activity in reversed(rec.agents):
        if activity.status != "running":
            continue
        if label is None or activity.label == label:
            return activity
    return None


__all__ = [
    "AgentActivity",
    "PhaseRecord",
    "WorkflowRun",
    "build_workflow_run_from_events",
]

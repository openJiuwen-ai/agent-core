# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Observer that turns engine progress events into the 4-layer run + a feed.

A single ``WorkflowObserver`` is wired into a run as the engine's
``progress_sink``. It does two things with each :class:`WorkflowProgressEvent`:

1. accumulates it so ``observer.run`` can fold the stream into the 4-layer
   :class:`WorkflowRun` at any time (live snapshot or final);
2. forwards it to an optional ``on_event`` callback — the leader-broadcast path
   injects one here to publish each event onto the team bus (Phase 3), so the
   spectator leader can narrate progress. Phase 2 leaves it unset.

``to_frontend`` is the (stubbed) transport seam for the TUI console — it returns
the serialized 4-layer run; actually shipping it to a frontend is future work.
"""
from __future__ import annotations

from typing import Callable

from openjiuwen.agent_teams.workflow.engine.progress import WorkflowProgressEvent
from openjiuwen.agent_teams.workflow.schema import WorkflowRun, build_workflow_run_from_events
from openjiuwen.core.common.logging import team_logger


class WorkflowObserver:
    """Accumulates progress events; optionally fans each out to a callback."""

    def __init__(self, on_event: Callable[[WorkflowProgressEvent], None] | None = None) -> None:
        self._events: list[WorkflowProgressEvent] = []
        self._on_event = on_event

    def emit(self, event: WorkflowProgressEvent) -> None:
        """Engine ``progress_sink``: accumulate, then fan out (best-effort)."""
        self._events.append(event)
        if self._on_event is None:
            return
        try:
            self._on_event(event)
        except Exception as exc:
            team_logger.debug("workflow observer on_event failed: %s", exc)

    @property
    def events(self) -> list[WorkflowProgressEvent]:
        return list(self._events)

    @property
    def run(self) -> WorkflowRun:
        """Fold the accumulated events into the 4-layer run (live snapshot)."""
        return build_workflow_run_from_events(self._events)

    def to_frontend(self) -> dict:
        """Serialize the 4-layer run for the TUI console.

        STUB: returns the JSON-able run. Shipping it over a transport to an
        actual frontend console is intentionally left unimplemented (the
        feature spec defers the transport).
        """
        return self.run.model_dump()


def summarize_run(run: WorkflowRun) -> str:
    """Render a one-line run summary for the leader-facing completion message.

    Folds the 4-layer run into a compact "N phases, M agents" line so the
    spectator leader can report orchestration scale alongside the script's
    return value. Swarmflow-specific (depends on ``WorkflowRun``); the generic
    result rendering lives in the async-tool framework.

    Args:
        run: The accumulated 4-layer run snapshot.

    Returns:
        A single line such as ``"3 phases, 7 agents"``.
    """
    phase_count = len(run.phases)
    agent_count = sum(len(phase.agents) for phase in run.phases)
    return f"{phase_count} phases, {agent_count} agents"


__all__ = ["WorkflowObserver", "summarize_run"]

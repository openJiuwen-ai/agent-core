# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Swarmflow progress handler — narrate orchestration progress to the leader.

A swarmflow run (launched by the ``swarmflow()`` tool, executing in the leader's
process) publishes ``WORKFLOW_PROGRESS`` events on the team topic with a
non-leader ``sender_id`` so the leader's own coordination loop receives them
(``kernel`` only self-filters events whose ``sender_id`` equals the local
member name). This handler turns the phase / start / completion milestones into
a short narration line and feeds it to the leader via ``deliver_input`` — the
spectator leader then streams a user-facing report. Per-agent progress is
intentionally NOT narrated (too chatty); it lives in the 4-layer ``WorkflowRun``
the observer accumulates.

Mirrors the established TaskBoard / Message handler pattern: the event only
reaches the leader's DeepAgent as input — coordination makes no decision here.
"""
from __future__ import annotations

from typing import ClassVar

from openjiuwen.agent_teams.agent.coordination.event_bus import CoordinationEvent
from openjiuwen.agent_teams.agent.coordination.handlers.base import BaseCoordinationHandler
from openjiuwen.agent_teams.i18n import t
from openjiuwen.agent_teams.schema.events import TeamEvent
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.core.common.logging import team_logger

# Progress kinds we narrate. These mirror ``workflow.engine.progress.ProgressKind``
# string values but are duplicated as literals so coordination does not import
# the workflow engine package (one-way dependency: workflow -> agent_teams core).
# Completion is NOT narrated here: the final result (and failures) are fed back
# by the NativeHarness async-tool framework, so only mid-run milestones remain.
_KIND_WORKFLOW_STARTED = "workflow_started"
_KIND_PHASE = "phase"


class WorkflowHandler(BaseCoordinationHandler):
    """Narrate swarmflow phase/lifecycle milestones to the spectator leader."""

    EVENT_METHOD_MAP: ClassVar[dict[str, str]] = {
        TeamEvent.WORKFLOW_PROGRESS: "on_workflow_progress",
    }

    async def on_workflow_progress(self, event: CoordinationEvent) -> None:
        """Render a phase/lifecycle milestone and deliver it to the leader."""
        if self._blueprint.role != TeamRole.LEADER:
            return
        try:
            payload = event.get_payload()
        except Exception as exc:
            team_logger.debug("workflow progress payload decode failed: %s", exc)
            return
        line = self._render(payload.kind, payload.workflow_name, payload.phase)
        if line is None:
            return
        await self._round.deliver_input(line, use_steer=True)

    @staticmethod
    def _render(kind: str, workflow_name: str | None, phase: str | None) -> str | None:
        """Map a progress kind to a narration line; None for non-milestones."""
        name = workflow_name or "workflow"
        if kind == _KIND_WORKFLOW_STARTED:
            return t("workflow.started", name=name)
        if kind == _KIND_PHASE:
            return t("workflow.phase", phase=phase or "?")
        return None

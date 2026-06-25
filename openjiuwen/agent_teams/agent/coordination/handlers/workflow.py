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

from typing import TYPE_CHECKING, ClassVar

from openjiuwen.agent_teams.agent.coordination.event_bus import CoordinationEvent
from openjiuwen.agent_teams.agent.coordination.handlers.base import BaseCoordinationHandler
from openjiuwen.agent_teams.i18n import t
from openjiuwen.agent_teams.inbound_render import render_event
from openjiuwen.agent_teams.schema.events import TeamEvent
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.core.common.logging import team_logger

if TYPE_CHECKING:
    from openjiuwen.agent_teams.schema.events import WorkflowProgressTeamEvent

# Progress kinds we narrate. These mirror ``workflow.engine.progress.ProgressKind``
# string values but are duplicated as literals so coordination does not import
# the workflow engine package (one-way dependency: workflow -> agent_teams core).
# Completion is NOT narrated here: the final result (and failures) are fed back
# by the NativeHarness async-tool framework. Mid-run milestones (start / phase)
# and the human-input wait (human_prompt / human_replied) are narrated.
_KIND_WORKFLOW_STARTED = "workflow_started"
_KIND_PHASE = "phase"
_KIND_HUMAN_PROMPT = "human_prompt"
_KIND_HUMAN_REPLIED = "human_replied"


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
        line = self._render(payload)
        if line is None:
            return
        # Wrap the milestone narration in <team-event kind="workflow"> so the
        # leader reads it through the same inbound-XML contract as task / message
        # events (F_46). The body already distinguishes started / phase /
        # human_prompt / human_replied in prose, so one stable kind suffices.
        await self._round.deliver_input(render_event(kind="workflow", body=line), use_steer=True)

    @staticmethod
    def _render(payload: "WorkflowProgressTeamEvent") -> str | None:
        """Map a progress kind to a narration line; None for non-milestones.

        Beyond the start / phase milestones, a ``human_prompt`` is narrated so the
        run never looks stalled while it waits on a person — the line carries the
        question and the ``correlation_id`` a UI uses to route the reply back
        (``HumanAgentMessage(target="swarmflow:<corr>")``).
        """
        kind = payload.kind
        if kind == _KIND_WORKFLOW_STARTED:
            return t("workflow.started", name=payload.workflow_name or "workflow")
        if kind == _KIND_PHASE:
            return t("workflow.phase", phase=payload.phase or "?")
        if kind == _KIND_HUMAN_PROMPT:
            return t(
                "workflow.human_prompt",
                label=payload.label or "human",
                prompt=payload.prompt or "",
                corr=payload.correlation_id or "",
            )
        if kind == _KIND_HUMAN_REPLIED:
            return t("workflow.human_replied", label=payload.label or "human")
        return None

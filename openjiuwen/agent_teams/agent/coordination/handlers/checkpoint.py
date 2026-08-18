# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Checkpoint creation handler — announce snapshot names to the leader.

A member that saves a named checkpoint publishes a ``CHECKPOINT_CREATED``
event on the team topic. The leader receives it (its coordination loop only
self-filters events whose ``sender_id`` equals the local member name) and
this handler delivers the snapshot name to the leader as a framework
``<team-event kind="checkpoint">`` with an ``announcement-only`` note.

The event — not a ``send_message`` — is the delivery channel on purpose: a
mailbox message from a member carries a reply hint and can prompt the leader
to answer, polluting its context; a framework event with an
``announcement-only`` note is informational and explicitly asks for no reply.
Coordination makes no decision here — it only relays the name for fork
coordination (``list_checkpoints`` / ``fork="<name>"``).
"""
from __future__ import annotations

from typing import ClassVar

from openjiuwen.agent_teams.agent.coordination.event_bus import CoordinationEvent
from openjiuwen.agent_teams.agent.coordination.handlers.base import BaseCoordinationHandler
from openjiuwen.agent_teams.i18n import t
from openjiuwen.agent_teams.inbound_render import render_event
from openjiuwen.agent_teams.schema.events import CheckpointCreatedEvent, TeamEvent
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.core.common.logging import team_logger


class CheckpointHandler(BaseCoordinationHandler):
    """Deliver checkpoint-created announcements to the leader's context."""

    EVENT_METHOD_MAP: ClassVar[dict[str, str]] = {
        TeamEvent.CHECKPOINT_CREATED: "on_checkpoint_created",
    }

    async def on_checkpoint_created(self, event: CoordinationEvent) -> None:
        """Render the snapshot name and deliver it as an announcement."""
        if self._blueprint.role != TeamRole.LEADER:
            return
        try:
            payload: CheckpointCreatedEvent = event.get_payload()
        except Exception as exc:
            team_logger.debug("checkpoint_created payload decode failed: %s", exc)
            return
        body = t(
            "checkpoint.created_body",
            name=payload.name,
            member=payload.member_name or "?",
            count=str(payload.message_count),
            description=f" ({payload.description})" if payload.description else "",
        )
        # Append (use_steer=False): the name is informational and should not
        # interrupt the leader's current round. The announcement-only note
        # explicitly asks for no reply.
        await self._round.deliver_input(
            render_event(
                kind="checkpoint",
                body=body,
                note_kind="announcement-only",
                note_text=t("checkpoint.created_note"),
            ),
            use_steer=False,
        )

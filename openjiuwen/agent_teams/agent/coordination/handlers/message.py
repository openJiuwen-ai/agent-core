# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Message-domain coordination events.

Owns MESSAGE / BROADCAST routing, periodic mailbox polling, and the
on-shutdown mailbox drain. Drain registers a fan-out callback on
``TeamEvent.MEMBER_SHUTDOWN`` — ``MemberHandler.on_member_event``
processes the lifecycle state change first, then this handler drains
its own mailbox in ``use_steer`` mode so any final messages reach the
agent before tear-down.
"""

from __future__ import annotations

from typing import Any, ClassVar

from openjiuwen.agent_teams.agent.coordination.event_bus import (
    CoordinationEvent,
    InnerEventType,
)
from openjiuwen.agent_teams.agent.coordination.handlers.base import BaseCoordinationHandler
from openjiuwen.agent_teams.i18n import t
from openjiuwen.agent_teams.schema.events import EventMessage, MessageEvent, TeamEvent
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.core.common.logging import team_logger


class MessageHandler(BaseCoordinationHandler):
    """Handle MESSAGE / BROADCAST / POLL_MAILBOX + drain on member shutdown.

    Leader does extra work on MESSAGE / BROADCAST: auto-acks
    teammate→user replies (the ``user`` pseudo-member has no agent
    process polling its mailbox) and notifies the SDK's human-agent
    inbound callbacks. All members then resume polls and drain their
    unread mailbox.

    On ``MEMBER_SHUTDOWN`` this handler registers a fan-out callback
    that drains the local mailbox before the agent process tears down,
    so any in-flight message reaches the agent in steer mode. Member
    state transitions are MemberHandler's concern; this is purely the
    drain.
    """

    EVENT_METHOD_MAP: ClassVar[dict[str, str]] = {
        TeamEvent.MESSAGE: "on_message_or_broadcast",
        TeamEvent.BROADCAST: "on_message_or_broadcast",
        InnerEventType.POLL_MAILBOX.value: "on_poll_mailbox",
        # Fan-out: MemberHandler.on_member_event runs first to process
        # the lifecycle state, then this drain runs to flush any final
        # messages before the agent tears down.
        TeamEvent.MEMBER_SHUTDOWN: "on_member_shutdown_drain",
    }

    async def on_message_or_broadcast(self, event: EventMessage) -> None:
        """Handle MESSAGE / BROADCAST events.

        Leader does extra work: auto-acks teammate→user replies (the
        ``user`` pseudo-member has no agent process polling its mailbox)
        and notifies the SDK's human-agent inbound callbacks. All
        members then resume polls and drain their unread mailbox.
        """
        member_name = self._blueprint.member_name
        if not member_name or self._infra.message_manager is None:
            return
        if self._blueprint.role == TeamRole.LEADER:
            if event.event_type == TeamEvent.MESSAGE:
                await self._ack_user_bound_message(event)
            await self._notify_human_agent_inbound(event)
        await self._poll.resume_polls()
        await self._process_unread_messages(member_name)

    async def on_poll_mailbox(self, event) -> None:
        """Periodic mailbox sweep: drain any unread messages."""
        member_name = self._blueprint.member_name
        team_logger.debug("poll mailbox: member_name={}", member_name)
        if member_name and self._infra.message_manager:
            await self._process_unread_messages(member_name)

    async def on_member_shutdown_drain(self, event: EventMessage) -> None:
        """Drain own mailbox when this teammate is the one shutting down.

        Leader does not drain on shutdown events — it observes other
        members' shutdowns at the lifecycle level. Only the teammate
        whose own ``member_name`` matches the event's payload drains.
        Steer mode ensures the messages land even if the agent is in
        the middle of a round.
        """
        if self._blueprint.role == TeamRole.LEADER:
            return
        member_name = self._blueprint.member_name
        if not member_name or self._infra.message_manager is None:
            return
        target_id = event.get_payload().member_name
        if target_id is None or target_id != member_name:
            return
        await self._process_unread_messages(member_name, use_steer=True)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _process_unread_messages(self, member_name: str, *, use_steer: bool = True) -> None:
        """Read unread messages, feed to agent one by one, loop until no new messages.

        Args:
            member_name: Current member ID.
            use_steer: When True, use steer instead of follow_up for running agent.
        """
        seen_ids: set[str] = set()

        while True:
            all_unread = await self._read_all_unread(member_name)
            new_messages = [m for m in all_unread if m.message_id not in seen_ids]

            if not new_messages:
                break

            team_logger.info("[{}] processing {} unread messages (steer={})", member_name, len(new_messages), use_steer)
            for msg in new_messages:
                seen_ids.add(msg.message_id)
                if self._round.has_pending_interrupt():
                    team_logger.info(
                        "[{}] deferring mailbox message {} until pending interrupt is resolved",
                        member_name,
                        msg.message_id,
                    )
                    return
                text = self._format_message(msg)
                team_logger.debug("[{}] message from={}, id={}", member_name, msg.from_member_name, msg.message_id)

                await self._round.deliver_input(text, use_steer=use_steer)
                await self._infra.message_manager.mark_message_read(msg.message_id, member_name)

    async def _notify_human_agent_inbound(self, event: CoordinationEvent) -> None:
        """Forward a team-side message to the SDK's human-agent callbacks.

        The leader observes every MESSAGE / BROADCAST event on the team
        topic. For point-to-point messages addressed to a human agent
        we fire the recipient's callback; for broadcasts we fire every
        registered callback whose owner is not the broadcast sender (so
        a human agent doesn't get its own broadcast echoed back).

        The lookup goes through ``TeamBackend.get_human_agent_inbound``
        — the registry the SDK populates via
        ``TeamRuntimeManager.register_human_agent_inbound``. Missing
        message metadata (e.g. body lookup failure) is logged and
        swallowed so a notification glitch never breaks the dispatch
        loop.
        """
        backend = self._infra.team_backend
        mm = self._infra.message_manager
        if backend is None or mm is None:
            return

        from openjiuwen.agent_teams.interaction.payload import HumanAgentInboundEvent

        payload: MessageEvent = event.get_payload()
        message_id = payload.message_id
        sender = payload.from_member_name
        is_broadcast = event.event_type == TeamEvent.BROADCAST

        try:
            row = await mm.db.message.get_message(message_id)
        except Exception as exc:
            team_logger.warning(
                "human_agent on_inbound: failed to load message %s: %s",
                message_id,
                exc,
            )
            return
        if row is None:
            return

        body = row.content
        ts = row.timestamp

        if is_broadcast:
            recipients = [name for name in backend.human_agent_names() if name != sender]
        else:
            target = payload.to_member_name
            if not backend.is_human_agent(target):
                return
            recipients = [target]

        for recipient in recipients:
            callback = backend.get_human_agent_inbound(recipient)
            if callback is None:
                continue
            evt = HumanAgentInboundEvent(
                member_name=recipient,
                sender=sender,
                body=body,
                broadcast=is_broadcast,
                message_id=message_id,
                timestamp=ts or 0,
            )
            try:
                result = callback(evt)
                if hasattr(result, "__await__"):
                    await result
            except Exception as exc:
                team_logger.warning(
                    "human_agent on_inbound callback for %s raised: %s",
                    recipient,
                    exc,
                )

    async def _ack_user_bound_message(self, event: CoordinationEvent) -> None:
        """Mark a teammate→user direct message as read on the user's behalf.

        The leader observes every direct-message event on the team topic;
        when the recipient is the ``user`` pseudo-member (no real polling
        process), the leader flips ``is_read`` so the message does not
        accumulate as unread and keep waking the dispatcher.
        """
        payload: MessageEvent = event.get_payload()
        if payload.to_member_name != "user":
            return
        mm = self._infra.message_manager
        if mm is None:
            return
        await mm.mark_message_read(payload.message_id, "user")
        team_logger.debug(
            "leader auto-acked user-bound message {} from {}",
            payload.message_id,
            payload.from_member_name,
        )

    async def _read_all_unread(self, member_name: str) -> list[Any]:
        """Read all unread messages (direct + broadcast).

        Returns merged list sorted by timestamp descending (newest first).
        """
        mm = self._infra.message_manager
        direct = await mm.get_messages(to_member_name=member_name, unread_only=True)
        broadcasts = await mm.get_broadcast_messages(member_name=member_name, unread_only=True)
        merged = list(direct) + list(broadcasts)
        merged.sort(key=lambda m: m.timestamp, reverse=True)
        return merged

    @staticmethod
    def _format_message(msg: Any) -> str:
        """Format one TeamMessage for agent input.

        Includes message_id so the agent can call mark_message_read,
        and distinguishes direct vs broadcast messages.
        """
        msg_type = t("dispatcher.msg_type_broadcast") if msg.broadcast else t("dispatcher.msg_type_direct")
        return t(
            "dispatcher.msg_received",
            msg_type=msg_type,
            message_id=msg.message_id,
            sender=msg.from_member_name,
            content=msg.content,
        )

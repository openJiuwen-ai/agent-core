# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
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
from openjiuwen.agent_teams.timefmt import format_time_context
from openjiuwen.agent_teams.tools.database.engine import get_current_time
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

        Teammate-only: the leader observes other members' shutdowns at
        the lifecycle level, and a human agent has no autonomous round —
        draining its mailbox would ``deliver_input`` and resurrect a
        round just as the avatar is collapsing (its own teardown rides
        ``MemberHandler`` → ``shutdown_self`` instead). Only the teammate
        whose own ``member_name`` matches the event's payload drains.
        Steer mode ensures the messages land even if the agent is in
        the middle of a round.
        """
        if self._blueprint.role != TeamRole.TEAMMATE:
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

        The role lookup happens once up front: a member is or is not a
        human-agent for the lifetime of this drain, so per-message
        ``is_human_agent`` checks would just churn the same backend
        call. The flag selects the harness-input template — see
        ``_format_message``.

        Args:
            member_name: Current member ID.
            use_steer: When True, use steer instead of follow_up for running agent.
        """
        seen_ids: set[str] = set()
        backend = self._infra.team_backend
        is_human_agent = backend is not None and await backend.is_human_agent(member_name)
        is_bridge = self._blueprint.role == TeamRole.BRIDGE_AGENT and backend is not None


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
                if is_bridge:
                    text = await self._bridge_deliverable_for(member_name, msg)
                else:
                    text = self._format_message(msg, is_human_agent=is_human_agent, now_ms=get_current_time())
                team_logger.debug("[{}] message from={}, id={}", member_name, msg.from_member_name, msg.message_id)

                await self._round.deliver_input(text, use_steer=use_steer)
                await self._infra.message_manager.mark_message_read(msg.message_id, member_name)

    async def _bridge_deliverable_for(self, member_name: str, msg: Any) -> str:
        """Build the text delivered to a bridge avatar's DeepAgent.

        The bridge avatar is a full local teammate, but inbound team
        messages must first be auto-forwarded to its remote backing
        agent (via ``BridgeProtocolAdapter.relay``) so the remote's
        text reply is part of the avatar's next context. The avatar
        then schedules — it decides whether to ``send_message`` the
        remote reply back to the team, claim/complete tasks, or stay
        silent. The local LLM passes the remote output through verbatim;
        the explicit instructions in the composed template enforce that
        contract.

        Falls back to ``REMOTE_UNAVAILABLE_SENTINEL`` when no adapter
        is wired or when the adapter raises — the bridge then degrades
        to a normal teammate-style mailbox round.
        """
        from openjiuwen.agent_teams.agent.bridge_inbound_compose import compose_bridge_inbound
        from openjiuwen.agent_teams.agent.bridge_outbound_wrap import wrap_outbound_to_remote
        from openjiuwen.agent_teams.interaction.bridge_protocol import REMOTE_UNAVAILABLE_SENTINEL
        from openjiuwen.agent_teams.schema.team import (
            BridgeMailboxInjectMode,
        )

        backend = self._infra.team_backend
        spec = backend.get_bridge_member_spec(member_name) if backend is not None else None
        # Defensive: if the role inference labelled this member as a
        # bridge but the spec dict has no entry (e.g. mid-recovery race),
        # fall back to the plain teammate format. A bridge avatar is
        # never a human_agent, so the human-forwarding template stays off.
        if spec is None:
            return self._format_message(msg, is_human_agent=False, now_ms=get_current_time())

        language = "cn"
        team_spec = self._blueprint.team_spec
        if team_spec is not None and team_spec.language:
            language = team_spec.language

        outbound_text = wrap_outbound_to_remote(
            sender=msg.from_member_name,
            sender_display_name=await self._lookup_display_name(msg.from_member_name),
            sender_role=await self._lookup_role(msg.from_member_name),
            sender_persona=await self._lookup_persona(msg.from_member_name),
            body=msg.content,
            broadcast=bool(getattr(msg, "broadcast", False)),
            task_hint=None,
            mode=spec.mailbox_inject_mode or BridgeMailboxInjectMode.PASSTHROUGH,
            language=language,
        )

        adapter = backend.get_bridge_adapter(member_name) if backend is not None else None
        remote_reply: str = REMOTE_UNAVAILABLE_SENTINEL
        if adapter is not None:
            try:
                remote_reply = await adapter.relay(member_name=member_name, text=outbound_text)
            except Exception as exc:
                team_logger.warning(
                    "bridge_agent[%s] relay raised: %s — falling back to sentinel",
                    member_name,
                    exc,
                )
                remote_reply = REMOTE_UNAVAILABLE_SENTINEL

        return compose_bridge_inbound(
            original_sender=msg.from_member_name,
            original_body=msg.content,
            remote_reply=remote_reply,
            language=language,
            time_info=format_time_context(msg.timestamp, get_current_time()),
        )

    async def _lookup_display_name(self, member_name: str) -> Any:
        backend = self._infra.team_backend
        if backend is None or backend.db is None:
            return None
        try:
            row = await backend.db.member.get_member(member_name, backend.team_name)
        except Exception:
            return None
        return row.display_name if row is not None else None

    async def _lookup_persona(self, member_name: str) -> Any:
        backend = self._infra.team_backend
        if backend is None or backend.db is None:
            return None
        try:
            row = await backend.db.member.get_member(member_name, backend.team_name)
        except Exception:
            return None
        return row.desc if row is not None else None

    async def _lookup_role(self, member_name: str) -> Any:
        backend = self._infra.team_backend
        if backend is None:
            return None
        if await backend.is_human_agent(member_name):
            return TeamRole.HUMAN_AGENT
        if backend.is_bridge_agent(member_name):
            return TeamRole.BRIDGE_AGENT
        # The leader uses the team's leader_member_name from team_spec.
        team_spec = self._blueprint.team_spec
        if team_spec is not None and member_name == team_spec.leader_member_name:
            return TeamRole.LEADER
        return TeamRole.TEAMMATE

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
            recipients = [name for name in await backend.human_agent_names() if name != sender]
        else:
            target = payload.to_member_name
            if not await backend.is_human_agent(target):
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

    def _format_message(self, msg: Any, *, is_human_agent: bool, now_ms: int) -> str:
        """Format one TeamMessage for agent input.

        Includes message_id so the agent can call mark_message_read,
        and distinguishes direct vs broadcast messages. The message's
        send time is rendered as ``<absolute local time> (<relative
        diff>)`` so the agent can judge recency and ordering — mailbox
        delivery is often delayed, and a bare epoch tells the LLM
        nothing about how stale the message is.

        Rendering is role-aware. A teammate / leader sees
        ``dispatcher.msg_received`` ("reply via send_message if the
        sender is waiting"). A human_agent avatar sees
        ``hitt.msg_received_for_human``, which frames the message as a
        notification for the controlling human and tells the avatar LLM
        not to autonomously call ``send_message`` — the avatar's
        outbound actions are driven only by Inbox instructions from
        its controller.

        Args:
            msg: The team message row to render.
            is_human_agent: Whether the recipient is a human-agent avatar.
            now_ms: Current millisecond UTC epoch, the relative-time anchor.
        """
        msg_type = t("dispatcher.msg_type_broadcast") if msg.broadcast else t("dispatcher.msg_type_direct")
        key = "hitt.msg_received_for_human" if is_human_agent else "dispatcher.msg_received"
        return t(
            key,
            msg_type=msg_type,
            message_id=msg.message_id,
            sender=msg.from_member_name,
            content=msg.content,
            time_info=format_time_context(msg.timestamp, now_ms),
        )

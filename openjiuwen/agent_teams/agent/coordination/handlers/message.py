# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
"""Message-domain coordination events.

Owns MESSAGE / BROADCAST routing, periodic mailbox polling, and the
on-shutdown mailbox drain. Drain registers a fan-out callback on
``TeamEvent.MEMBER_SHUTDOWN`` — ``MemberHandler.on_member_event``
processes the lifecycle state change first, then this handler drains
its own mailbox in ``use_steer`` mode so any final messages reach the
agent before tear-down.

Every path here that feeds the local harness goes through
``_harness_input_blocked`` first, which is where a departing member's graceful
teardown actually happens: a member with shutdown requested and no round in
flight settles straight to SHUTDOWN instead of being woken for a final round,
and a member that is already SHUTDOWN is never fed at all.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

from openjiuwen.agent_teams.agent.coordination.event_bus import (
    CoordinationEvent,
    InnerEventType,
)
from openjiuwen.agent_teams.agent.coordination.handlers.base import BaseCoordinationHandler
from openjiuwen.agent_teams.i18n import t
from openjiuwen.agent_teams.inbound_render import (
    INBOUND_TYPE_BROADCAST,
    INBOUND_TYPE_DIRECT,
    render_inbound,
)
from openjiuwen.agent_teams.message_template import ExpandedMessage, expand_message
from openjiuwen.agent_teams.schema.events import EventMessage, MessageEvent, TeamEvent
from openjiuwen.agent_teams.schema.status import MemberStatus
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
        """Drain own mailbox when this member is the one shutting down.

        Non-leader only, and only for the member the event names. Steer mode
        ensures a final message lands even if the agent is mid-round.

        Every role rides this same path, human agents included. The
        harness-input gate in ``_process_unread_messages`` already refuses to
        wake an idle harness for a departing member — it settles it straight to
        SHUTDOWN instead — so draining can no longer resurrect a round on an
        avatar that is collapsing. A member with a round in flight steers its
        final messages into it and closes at round-end; an idle one settles.
        That leaves nothing for a role branch to decide here.
        """
        if self._blueprint.role == TeamRole.LEADER:
            return
        member_name = self._blueprint.member_name
        if not member_name:
            return
        target_id = event.get_payload().member_name
        if target_id is None or target_id != member_name:
            return
        # No ``message_manager`` guard here: the settle decision lives inside
        # ``_process_unread_messages``' gate, and a member's teardown must not
        # hinge on whether the mailbox subsystem happens to be wired.
        await self._process_unread_messages(member_name, use_steer=True)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _harness_input_blocked(self, member_name: str) -> bool:
        """Whether this member's harness must not be fed — settling it if idle.

        Every path in this handler that feeds the local harness passes through
        here first. Three outcomes, keyed on the member's persisted status:

        * ``SHUTDOWN`` — the member is gone. Never feed a dead harness; drop the
          delivery.
        * ``SHUTDOWN_REQUESTED`` with no round in flight — the member is on its
          way out with nothing running to ride. Waking the harness for one last
          round, only to hand it a "you are being shut down" notice, burns an
          LLM round to say goodbye. Settle straight to SHUTDOWN instead.
        * ``SHUTDOWN_REQUESTED`` with a round in flight — let the delivery
          through. It steers into the running round, whose end closes the stream
          (the teardown teammates have always ridden).

        The status must come from the DB, not from the ``MEMBER_SHUTDOWN``
        event: ``shutdown_member`` writes SHUTDOWN_REQUESTED, *then* sends the
        shutdown notice, *then* publishes the event. The notice therefore lands
        as an ordinary MESSAGE first, and a gate keyed on the event would let it
        wake an idle harness before the event ever arrives.

        The real human behind a human-agent avatar is unaffected either way: the
        leader pushes team messages to the controller's inbound callback
        (``_notify_human_agent_inbound``) independently of this avatar's harness,
        so skipping the harness never costs the person a message.

        The leader is exempt — it is never a ``shutdown_member`` target and has
        to survive ``clean_team`` to serve the next interaction.
        """
        if self._blueprint.role == TeamRole.LEADER:
            return False
        backend = self._infra.team_backend
        if backend is None:
            return False

        status = await backend.get_member_status(member_name)
        if status == MemberStatus.SHUTDOWN.value:
            team_logger.debug("[{}] harness input dropped: member is already SHUTDOWN", member_name)
            return True
        if status != MemberStatus.SHUTDOWN_REQUESTED.value:
            return False
        if self._round.has_in_flight_round():
            return False

        team_logger.info(
            "[{}] shutdown requested with no round in flight; settling to SHUTDOWN without a final round",
            member_name,
        )
        await self._lifecycle.shutdown_self()
        return True

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
        if await self._harness_input_blocked(member_name):
            return
        if self._infra.message_manager is None:
            return

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
            # Collect delivered messages and batch their read-state write into
            # a single transaction after the loop — one commit (one fsync)
            # instead of one per message. The finally guarantees already
            # delivered messages are marked even if delivery raises or an
            # interrupt cuts the drain short; undelivered ones stay unread
            # for the next poll. The read-state details (direct is_read rows
            # vs the single broadcast watermark) live in
            # ``TeamMessageManager.mark_messages_read`` — the handler just
            # hands over the raw delivered objects.
            delivered: list[Any] = []
            interrupted = False
            try:
                for msg in new_messages:
                    seen_ids.add(msg.message_id)
                    if self._round.has_pending_interrupt():
                        # Approval messages are admitted to resume_interrupt;
                        # all other messages are deferred until the interrupt clears.
                        approval_data = self._try_parse_approval_payload(msg)
                        if approval_data is not None:
                            team_logger.info(
                                "[{}] admitting approval message {} to resume interrupt",
                                member_name,
                                msg.message_id,
                            )
                            await self._infra.message_manager.mark_message_read(msg.message_id, member_name)
                            await self._round.resume_interrupt(approval_data)
                            continue
                        team_logger.info(
                            "[{}] deferring mailbox message {} until pending interrupt is resolved",
                            member_name,
                            msg.message_id,
                        )
                        interrupted = True
                        break
                    expanded = await self._expand(msg)
                    if is_bridge:
                        text = await self._bridge_deliverable_for(member_name, msg, expanded=expanded)
                    else:
                        text = self._format_message(
                            msg,
                            expanded=expanded,
                            is_human_agent=is_human_agent,
                            now_ms=get_current_time(),
                        )
                    team_logger.debug("[{}] message from={}, id={}", member_name, msg.from_member_name, msg.message_id)

                    await self._round.deliver_input(text, use_steer=use_steer)
                    delivered.append(msg)
            finally:
                if delivered:
                    await self._infra.message_manager.mark_messages_read(delivered, member_name)
            if interrupted:
                return

    def _language(self) -> str:
        """Team language for delivery-time template rendering."""
        team_spec = self._blueprint.team_spec
        if team_spec is not None and team_spec.language:
            return team_spec.language
        return "cn"

    async def _expand(self, msg: Any) -> ExpandedMessage:
        """Render a message row's delivery text (F_63 two-phase templating).

        Ordinary messages pass through with their content. A framework
        template message carries only ``meta``, and its document is rendered
        here — against the task row as it stands *now*, so a handoff that sat
        in an offline member's mailbox never delivers a stale task brief.
        """
        backend = self._infra.team_backend
        db = backend.db if backend is not None else None

        async def _get_task(task_id: str) -> Any:
            return await db.task.get_task(task_id) if db is not None else None

        async def _get_member(name: str) -> Any:
            return await db.member.get_member(name, backend.team_name) if db is not None else None

        return await expand_message(
            msg,
            task_getter=_get_task,
            member_getter=_get_member,
            language=self._language(),
        )

    async def _bridge_deliverable_for(self, member_name: str, msg: Any, *, expanded: ExpandedMessage) -> str:
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

        The relayed body is the *expanded* text: a remote executor has no DB,
        so it could not render a framework template itself (F_63).
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
            return self._format_message(
                msg,
                expanded=expanded,
                is_human_agent=False,
                now_ms=get_current_time(),
            )

        language = self._language()

        outbound_text = wrap_outbound_to_remote(
            sender=msg.from_member_name,
            sender_display_name=await self._lookup_display_name(msg.from_member_name),
            sender_role=await self._lookup_role(msg.from_member_name),
            sender_desc=await self._lookup_desc(msg.from_member_name),
            body=expanded.body,
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
            original_body=expanded.body,
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

    async def _lookup_desc(self, member_name: str) -> Any:
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

        # A human agent can be a scheduled team's assignee or reviewer, so the
        # row may be a framework template whose content is empty — expand it
        # here too, or the controller would be pushed a blank message (F_63).
        body = (await self._expand(row)).body
        ts = row.timestamp

        # Recipients are the humans still reachable — a fully SHUTDOWN member has
        # left the team and its controller has no business still being fed the
        # team's traffic. A member with shutdown merely *requested* stays in:
        # ``shutdown_member`` flips the status before it sends the notice, so
        # excluding it here would drop the one message that tells its controller
        # it was removed.
        if is_broadcast:
            recipients = [name for name in await backend.reachable_human_agent_names() if name != sender]
        else:
            target = payload.to_member_name
            if not await backend.is_reachable_human_agent(target):
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

    def _format_message(self, msg: Any, *, expanded: ExpandedMessage, is_human_agent: bool, now_ms: int) -> str:
        """Render one TeamMessage as ``<team-inbound>`` XML for agent input.

        The message body goes verbatim inside the ``<team-inbound>`` element
        (sender / message_id / type / time as attributes), and the
        framework-added hint goes in a separate ``<team-note>`` — so the LLM
        sees a clean boundary between the message and what the runtime
        appended. ``message_id`` is carried so the agent can call
        ``mark_message_read``; the send time is rendered as ``<absolute local
        time> (<relative diff>)`` so the agent can judge recency (mailbox
        delivery is often delayed). The envelope is uniform across every team
        message — a framework template message differs only in where its body
        came from (rendered at delivery, see ``_expand``), not in its shape.

        Rendering is role-aware. A teammate / leader gets a ``reply-hint``
        note — except on a framework template message, which is answered with
        a tool call (start work, cast a vote), never with a reply. A
        human_agent avatar gets ``for="controller"`` plus a ``hitt-silence``
        note: the message is framed as a notification for the controlling
        human, and the load-bearing "stay silent" constraint keeps the avatar
        from autonomously calling ``send_message`` — its outbound actions are
        driven only by Inbox instructions from its controller.

        Args:
            msg: The team message row to render.
            expanded: The delivery-time body plus whether it came from a
                framework template.
            is_human_agent: Whether the recipient is a human-agent avatar.
            now_ms: Current millisecond UTC epoch, the relative-time anchor.
        """
        msg_type = INBOUND_TYPE_BROADCAST if msg.broadcast else INBOUND_TYPE_DIRECT
        time_info = format_time_context(msg.timestamp, now_ms)
        if is_human_agent:
            return render_inbound(
                content=expanded.body,
                sender=msg.from_member_name,
                message_id=msg.message_id,
                msg_type=msg_type,
                time_info=time_info,
                for_controller=True,
                note_kind="hitt-silence",
                note_text=t("hitt.silence_note"),
            )
        note_kind = None if expanded.is_template else "reply-hint"
        note_text = None if expanded.is_template else t("dispatcher.reply_hint", sender=msg.from_member_name)
        return render_inbound(
            content=expanded.body,
            sender=msg.from_member_name,
            message_id=msg.message_id,
            msg_type=msg_type,
            time_info=time_info,
            note_kind=note_kind,
            note_text=note_text,
        )

    @staticmethod
    def _try_parse_approval_payload(msg: Any) -> dict | None:
        """Try to parse a tool-approval result from a message.

        Returns the parsed approval dict if the message is a
        ``protocol="json"` message with ``type == "tool_approval_result"``,
        or ``None`` otherwise.  Parses JSON only once.
        """
        if msg.protocol != "json" or not msg.content:
            return None
        try:
            data = json.loads(msg.content)
        except (json.JSONDecodeError, TypeError):
            return None
        if isinstance(data, dict) and data.get("type") == "tool_approval_result":
            return data
        return None

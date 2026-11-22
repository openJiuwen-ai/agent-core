# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Group mailbox policy over the shared message delivery lifecycle."""

import asyncio

from openjiuwen.agent_teams.agent.coordination.handlers.message import MessageHandler
from openjiuwen.agent_teams.context import get_session_id
from openjiuwen.agent_teams.group_chat.handler import context_for, conversation_message, group_metadata
from openjiuwen.agent_teams.message_template import ExpandedMessage
from openjiuwen.agent_teams.schema.events import TeamEvent
from openjiuwen.agent_teams.schema.status import MemberStatus
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.core.common.logging import team_logger


_MAX_HOST_NOTIFICATIONS = 32
_HOST_NOTIFICATION_TIMEOUT = 10


class GroupMessageHandler(MessageHandler):
    """Consume group mentions with excerpts and chronological watermark updates.

    The dispatcher selects exactly one mailbox handler per event. Lifecycle
    gates, interrupts, bridge delivery and marking successful input are shared
    with the ordinary handler; all group-specific policy lives here.
    """

    def __init__(self, host, blueprint, infra, poll_ctrl) -> None:
        super().__init__(host, blueprint, infra, poll_ctrl)
        self._host_notifications: dict[tuple[str, str], asyncio.Task[None]] = {}

    async def handles(self, event) -> bool:
        if getattr(event, "event_type", None) == TeamEvent.MEMBER_SHUTDOWN:
            status = await self._infra.team_backend.get_member_status(self._blueprint.member_name)
            if status == MemberStatus.SHUTDOWN.value:
                return False
            if status == MemberStatus.SHUTDOWN_REQUESTED.value and not self._round.has_in_flight_round():
                return False
        if getattr(event, "event_type", None) == TeamEvent.BROADCAST:
            row = await self._infra.team_backend.db.message.get_message(event.get_payload().message_id)
            if row is not None and group_metadata(row):
                return True
        if not self._blueprint.member_name or self._infra.message_manager is None:
            return False
        messages = await self._infra.message_manager.get_broadcast_messages(
            member_name=self._blueprint.member_name, unread_only=True,
        )
        return any(group_metadata(message) for message in messages)

    async def on_message_or_broadcast(self, event) -> None:
        if event.event_type == TeamEvent.BROADCAST:
            backend = self._infra.team_backend
            if self._blueprint.role == TeamRole.LEADER and backend is not None:
                self._schedule_host_notification(backend, event.get_payload().message_id)
            await self.start_mentioned_members()
        await super().on_message_or_broadcast(event)

    def _schedule_host_notification(self, backend, message_id: str) -> None:
        key = (get_session_id(), message_id)
        if key in self._host_notifications:
            return
        if len(self._host_notifications) >= _MAX_HOST_NOTIFICATIONS:
            team_logger.warning("Host-managed notification queue is full; host must retry {}", message_id)
            return
        task = asyncio.create_task(self._notify_host(backend, message_id))
        self._host_notifications[key] = task
        task.add_done_callback(lambda _: self._host_notifications.pop(key, None))

    async def _notify_host(self, backend, message_id: str) -> None:
        try:
            async with asyncio.timeout(_HOST_NOTIFICATION_TIMEOUT):
                row = await backend.db.message.get_message(message_id)
                if row is not None and group_metadata(row):
                    await backend.notify_host_managed_inbound(conversation_message(row, get_session_id()))
        except Exception:
            team_logger.error("Host-managed notification failed; host must retry {}", message_id, exc_info=True)

    def cancel_host_notifications(self) -> None:
        """Cancel best-effort notifications before the session is released."""
        for task in self._host_notifications.values():
            task.cancel()

    async def start_mentioned_members(self) -> None:
        """Start pending group recipients through the existing member lifecycle."""
        backend = self._infra.team_backend
        if self._blueprint.role != TeamRole.LEADER or backend is None:
            return
        try:
            members = await backend.db.message.get_unread_group_members(backend.team_name)
            for member_name in members:
                if member_name != self._blueprint.member_name:
                    await self._lifecycle.auto_start_member(member_name)
        except Exception:
            team_logger.error("Failed to start mentioned group members", exc_info=True)

    async def _read_all_unread(self, member_name):
        messages = await super()._read_all_unread(member_name)
        # One message per drain iteration commits its watermark before the
        # next excerpt, including ordinary broadcasts sharing the same cursor.
        return sorted(messages, key=lambda message: (message.timestamp, message.message_id))[:1]

    async def _expand(self, message):
        if message.broadcast and group_metadata(message):
            body = await context_for(self._infra.team_backend, self._blueprint.member_name, message)
            return ExpandedMessage(body=body, is_template=False)
        return await super()._expand(message)

    def _format_message(self, message, *, expanded, is_human_agent):
        if message.broadcast and group_metadata(message):
            return expanded.body
        return super()._format_message(message, expanded=expanded, is_human_agent=is_human_agent)

    def _try_parse_approval_payload(self, message):
        if message.broadcast and group_metadata(message):
            return None
        return super()._try_parse_approval_payload(message)

    def _render_external_runtime_failed(self, message):
        if message.broadcast and group_metadata(message):
            return None
        return super()._render_external_runtime_failed(message)

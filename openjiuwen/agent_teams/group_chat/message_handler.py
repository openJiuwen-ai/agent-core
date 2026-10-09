# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Group mailbox policy over the shared message delivery lifecycle."""

from openjiuwen.agent_teams.agent.coordination.handlers.message import MessageHandler
from openjiuwen.agent_teams.group_chat.handler import context_for, group_metadata
from openjiuwen.agent_teams.message_template import ExpandedMessage
from openjiuwen.agent_teams.schema.events import TeamEvent
from openjiuwen.agent_teams.schema.status import MemberStatus
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.core.common.logging import team_logger


class GroupMessageHandler(MessageHandler):
    """Consume group mentions with excerpts and chronological watermark updates.

    The dispatcher selects exactly one mailbox handler per event. Lifecycle
    gates, interrupts, bridge delivery and marking successful input are shared
    with the ordinary handler; all group-specific policy lives here.
    """

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
            await self.start_mentioned_members()
        await super().on_message_or_broadcast(event)

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

    def _format_message(self, message, *, expanded, is_human_agent, now_ms):
        if message.broadcast and group_metadata(message):
            return expanded.body
        return super()._format_message(
            message, expanded=expanded, is_human_agent=is_human_agent, now_ms=now_ms,
        )

    def _try_parse_approval_payload(self, message):
        if message.broadcast and group_metadata(message):
            return None
        return super()._try_parse_approval_payload(message)

    def _render_external_runtime_failed(self, message):
        if message.broadcast and group_metadata(message):
            return None
        return super()._render_external_runtime_failed(message)

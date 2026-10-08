# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Deliver a group-chat mention as an excerpt instead of the raw broadcast."""

from __future__ import annotations

from typing import Any

from openjiuwen.agent_teams.agent.coordination.handlers.message import MessageHandler
from openjiuwen.agent_teams.context import get_session_id
from openjiuwen.agent_teams.group_chat.handler import render_context
from openjiuwen.agent_teams.group_chat.meta import group_metadata
from openjiuwen.agent_teams.message_template import ExpandedMessage
from openjiuwen.agent_teams.schema.events import TeamEvent


class GroupMessageHandler(MessageHandler):
    """Mailbox handler used only while a group-chat row is in the unread set."""

    async def handles(self, event: Any) -> bool:
        """Whether this event must drain oldest-first because a group row is involved."""
        member_name = self._blueprint.member_name
        if event.event_type == TeamEvent.MEMBER_SHUTDOWN and member_name:
            if await self._harness_input_blocked(member_name):
                return False
        if event.event_type == TeamEvent.BROADCAST and group_metadata(await self._load_event_row(event)):
            return True
        if not member_name or self._infra.message_manager is None:
            return False
        broadcasts = await self._infra.message_manager.get_broadcast_messages(
            member_name=member_name,
            unread_only=True,
        )
        return any(group_metadata(row) for row in broadcasts)

    async def on_message_or_broadcast(self, event: Any) -> None:
        await self._start_mentioned_members()
        await super().on_message_or_broadcast(event)

    async def on_poll_mailbox(self, event: Any) -> None:
        await self._start_mentioned_members()
        await super().on_poll_mailbox(event)

    async def _read_all_unread(self, member_name: str) -> list[Any]:
        merged = await super()._read_all_unread(member_name)
        if not any(group_metadata(row) for row in merged):
            return merged
        merged.sort(key=lambda row: (row.timestamp, row.message_id))
        return merged[:1]

    async def _expand(self, msg: Any) -> ExpandedMessage:
        meta = group_metadata(msg)
        if not meta:
            return await super()._expand(msg)
        backend = self._infra.team_backend
        member_name = self._blueprint.member_name
        if backend is None or not member_name:
            return ExpandedMessage(body=msg.content or "", is_template=True)
        from openjiuwen.agent_teams.group_chat.handler import conversation_message

        session_id = str(meta.get("session_id") or get_session_id() or "")
        rows = await backend.db.message.get_team_messages(backend.team_name, broadcast=True)
        projected = [
            message for row in rows
            if (message := conversation_message(row, session_id)) is not None
        ]
        after = await backend.db.message.get_broadcast_read_at(backend.team_name, member_name)
        try:
            body = render_context(
                projected,
                msg.message_id,
                after,
                str(backend.group_conversation().history_path),
            )
        except (ValueError, OSError):
            return ExpandedMessage(body=msg.content or "", is_template=True)
        return ExpandedMessage(body=body, is_template=True)

    def _format_message(
        self,
        msg: Any,
        *,
        expanded: ExpandedMessage,
        is_human_agent: bool,
        now_ms: int,
        suppress_reply_hint: bool = False,
    ) -> str:
        if group_metadata(msg):
            return expanded.body
        return super()._format_message(
            msg,
            expanded=expanded,
            is_human_agent=is_human_agent,
            now_ms=now_ms,
            suppress_reply_hint=suppress_reply_hint,
        )

    @staticmethod
    def _try_parse_approval_payload(msg: Any) -> dict | None:
        if group_metadata(msg):
            return None
        return MessageHandler._try_parse_approval_payload(msg)

    async def _start_mentioned_members(self) -> None:
        starter = getattr(self._lifecycle, "start_mentioned_members", None)
        if starter is not None:
            await starter()

    async def _load_event_row(self, event: Any) -> Any:
        manager = self._infra.message_manager
        if manager is None:
            return None
        try:
            payload = event.get_payload()
            return await manager.db.message.get_message(payload.message_id)
        except Exception:
            return None

# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Archive one public message and project this session's history."""

from __future__ import annotations

import json
from typing import Any

from openjiuwen.agent_teams.context import get_session_id
from openjiuwen.agent_teams.group_chat.meta import (
    BODY_LIMIT,
    CONTEXT_TAIL,
    GROUP_CHAT_TYPE,
    MAX_CLIENT_MESSAGE_ID,
    MAX_MENTIONS,
    group_metadata,
    stable_message_id,
)
from openjiuwen.agent_teams.i18n import t
from openjiuwen.agent_teams.schema.conversation import (
    ConversationAppendResult,
    ConversationMessage,
)
from openjiuwen.agent_teams.schema.status import MEMBER_DEPARTED_STATUSES

_USER = "user"


async def post_message(
    backend: Any,
    *,
    sender: str,
    sender_name: str,
    content: str,
    client_message_id: str,
    mentions: list[str] | None = None,
    attachments: list[dict] | None = None,
) -> ConversationAppendResult:
    """Insert one group broadcast and refresh this session's projection.

    A missing team row fails. This path does not call ``build_team``.
    """
    session_id = get_session_id()
    if not session_id:
        raise ValueError("group chat requires a bound session")
    body = _require_text(content, "content")
    identity = _require_identity(client_message_id)
    refs = _require_attachments(attachments)
    if not body and not refs:
        raise ValueError("group chat requires content or attachments")
    names = _dedupe(mentions)
    if not await backend.db.team.team_exists(backend.team_name):
        raise ValueError("team does not exist")
    author = await _require_sender(backend, sender)
    targets = await _require_mentions(backend, names)
    notified = [name for name in targets if name != sender]
    meta = {
        "type": GROUP_CHAT_TYPE,
        "client_message_id": identity,
        "session_id": session_id,
        "sender_name": sender_name,
        "mentions": names,
        "attachments": refs,
    }
    message_id = stable_message_id(backend.team_name, session_id, identity)
    created = await backend.db.message.create_message(
        message_id=message_id,
        team_name=backend.team_name,
        from_member_name=author,
        content=body,
        broadcast=True,
        meta=meta,
    )
    stored = await backend.db.message.get_message(message_id)
    if stored is None:
        raise RuntimeError("group chat message was not stored")
    duplicate = not created
    if duplicate and not _same_message(stored, author, body, meta):
        raise ValueError("client_message_id already archives different content")
    rows = await _session_messages(backend, session_id)
    log = backend.group_conversation()
    log.sync(rows)
    published = await backend.message_manager.publish_broadcast(message_id, author)
    if not published:
        raise RuntimeError("group chat broadcast was not published")
    record = next(row for row in rows if row.message_id == message_id)
    return ConversationAppendResult(
        message=record,
        duplicate=duplicate,
        notified_members=notified,
        context_path=str(log.history_path),
    )


def render_context(
    messages: list[ConversationMessage],
    trigger_id: str,
    after: int,
    path: str,
) -> str:
    """Render the excerpt, including the history file path."""
    trigger = next((item for item in messages if item.message_id == trigger_id), None)
    if trigger is None:
        raise ValueError("trigger message is not in this session")
    prior = [
        item for item in messages
        if after < item.timestamp <= trigger.timestamp and item.message_id != trigger.message_id
    ]
    window = [*prior[-(CONTEXT_TAIL - 1):], trigger]
    return t(
        "conversation.context",
        from_timestamp=after,
        to_timestamp=trigger.timestamp,
        trigger_message_id=trigger.message_id,
        path=path,
        excerpts="\n".join(_excerpt(item) for item in window),
    )


def conversation_message(row: Any, session_id: str) -> ConversationMessage | None:
    """Project one database row when it belongs to ``session_id``."""
    meta = group_metadata(row)
    if not meta or meta.get("session_id") != session_id:
        return None
    return ConversationMessage(
        message_id=row.message_id,
        team_name=row.team_name,
        session_id=session_id,
        client_message_id=str(meta.get("client_message_id") or ""),
        sender=row.from_member_name,
        sender_name=str(meta.get("sender_name") or row.from_member_name),
        content=row.content or "",
        timestamp=int(row.timestamp or 0),
        mentions=list(meta.get("mentions") or []),
        attachments=list(meta.get("attachments") or []),
    )


async def _session_messages(backend: Any, session_id: str) -> list[ConversationMessage]:
    rows = await backend.db.message.get_team_messages(backend.team_name, broadcast=True)
    projected = [
        message for row in rows
        if (message := conversation_message(row, session_id)) is not None
    ]
    projected.sort(key=lambda item: (item.timestamp, item.message_id))
    return projected


async def _require_sender(backend: Any, sender: str) -> str:
    if sender == _USER:
        return sender
    member = await backend.db.member.get_member(sender, backend.team_name)
    if member is None or member.status in MEMBER_DEPARTED_STATUSES:
        raise ValueError(f"unknown group chat sender: {sender}")
    return sender


async def _require_mentions(backend: Any, mentions: list[str]) -> list[str]:
    targets: list[str] = []
    for name in mentions:
        if name == _USER:
            continue
        member = await backend.db.member.get_member(name, backend.team_name)
        if member is None or member.status in MEMBER_DEPARTED_STATUSES:
            raise ValueError(f"unknown group chat mention: {name}")
        if member.role != "passive_human":
            targets.append(name)
    return targets


def _same_message(stored: Any, sender: str, content: str, meta: dict) -> bool:
    stored_meta = group_metadata(stored)
    return (
        stored.from_member_name == sender
        and (stored.content or "") == content
        and list(stored_meta.get("mentions") or []) == meta["mentions"]
        and list(stored_meta.get("attachments") or []) == meta["attachments"]
        and stored_meta.get("session_id") == meta["session_id"]
    )


def _excerpt(message: ConversationMessage) -> str:
    body = message.content
    truncated = len(body) > BODY_LIMIT
    if truncated:
        body = body[:BODY_LIMIT]
    return json.dumps(
        {
            "message_id": message.message_id,
            "sender": message.sender,
            "sender_name": message.sender_name,
            "timestamp": message.timestamp,
            "mentions": message.mentions,
            "content": body,
            "truncated": truncated,
            "attachment_count": len(message.attachments),
        },
        ensure_ascii=False,
    )


def _dedupe(mentions: list[str] | None) -> list[str]:
    if mentions is None:
        return []
    if not isinstance(mentions, list) or len(mentions) > MAX_MENTIONS:
        raise ValueError(f"mentions must be a list of at most {MAX_MENTIONS} names")
    ordered: list[str] = []
    for name in mentions:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("mentions must contain member names")
        if name not in ordered:
            ordered.append(name)
    return ordered


def _require_text(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    return value


def _require_identity(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_CLIENT_MESSAGE_ID:
        raise ValueError("client_message_id must be a non-empty string of at most 255 characters")
    return value


def _require_attachments(value: list[dict] | None) -> list[dict]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("attachments must be a list")
    cleaned: list[dict] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("attachments must contain objects")
        cleaned.append({key: item[key] for key in ("name", "path") if key in item})
    try:
        json.dumps(cleaned)
    except TypeError as exc:
        raise ValueError("attachments must contain JSON values") from exc
    return cleaned


async def deliver_group_message(backend: Any, message: Any) -> Any:
    """Archive a host group message. A missing team is a refusal, not a build."""
    from openjiuwen.agent_teams.interaction.payload import DeliverResult

    try:
        result = await post_message(
            backend,
            sender="user",
            sender_name="user",
            content=message.body,
            client_message_id=message.client_message_id,
            mentions=list(message.mentions),
            attachments=[dict(item) for item in message.attachments],
        )
    except ValueError:
        return DeliverResult.failure("invalid_group_chat")
    except (OSError, RuntimeError):
        return DeliverResult.failure("group_chat_delivery_failed")
    return DeliverResult.success(result.message.message_id, data=result.model_dump())

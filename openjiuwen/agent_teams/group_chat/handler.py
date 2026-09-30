# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Public broadcasts, file projection and mention-gated model input."""
import asyncio
import json
import uuid

from openjiuwen.agent_teams.schema.conversation import ConversationAppendResult, ConversationMessage

CONTEXT_TAIL = 5


def group_metadata(message) -> dict:
    raw = getattr(message, "meta", None)
    meta = json.loads(raw) if isinstance(raw, str) and raw else {}
    return meta if meta.get("type") == "group_chat" else {}


def conversation_message(row, session_id: str) -> ConversationMessage:
    meta = group_metadata(row)
    return ConversationMessage(
        message_id=row.message_id, team_name=row.team_name, session_id=session_id,
        client_message_id=meta["client_message_id"], sender=row.from_member_name,
        sender_name=meta["sender_name"], content=row.content, timestamp=row.timestamp,
        mentions=meta["mentions"], attachments=meta["attachments"],
    )


def _same_message(stored, sender: str, content: str, mentions, attachments) -> bool:
    original = group_metadata(stored)
    return (
        stored.from_member_name == sender
        and stored.content == content
        and original.get("mentions") == mentions
        and original.get("attachments") == attachments
    )


async def sync_history(conversation, message_manager) -> list[ConversationMessage]:
    # ponytail: full-history projection; use incremental reads only if large groups need them.
    rows = await message_manager.db.message.get_team_messages(conversation.team_name, broadcast=True)
    messages = [conversation_message(row, conversation.session_id) for row in rows if group_metadata(row)]
    await asyncio.to_thread(conversation.sync, messages)
    return messages


async def context_for(backend, member_name: str, trigger) -> str:
    """Render a recent excerpt; the DB, not the history file, selects the range."""
    from openjiuwen.agent_teams.i18n import STRINGS

    conversation = await backend.group_conversation()
    messages = await sync_history(conversation, backend.message_manager)
    after = await backend.db.message.get_broadcast_read_at(backend.team_name, member_name)
    candidates = [
        m for m in messages if after < m.timestamp <= trigger.timestamp and m.message_id != trigger.message_id
    ]
    candidates.sort(key=lambda m: (m.timestamp, m.message_id))
    tail = candidates[-(CONTEXT_TAIL - 1):]
    tail.append(conversation_message(trigger, conversation.session_id))
    excerpts = []
    for item in tail:
        excerpt = item.model_dump(exclude={"attachments"})
        excerpt.update(content=item.content[:2000], content_truncated=len(item.content) > 2000,
                       attachment_count=len(item.attachments))
        excerpts.append(json.dumps(excerpt, ensure_ascii=False))
    language = getattr(backend.group_chat_spec, "language", None) or "cn"
    return STRINGS[language]["conversation.context"].format(
        from_timestamp=after, to_timestamp=trigger.timestamp, trigger_message_id=trigger.message_id,
        path=str(conversation.history_path), excerpts="\n".join(excerpts),
    )


async def post_message(conversation, message_manager, sender: str, content: str, *, client_message_id: str,
                       mentions=(), attachments=()):
    """Persist one broadcast, project history, then publish the existing broadcast event."""
    from openjiuwen.agent_teams.context import reset_session_id, set_session_id
    from openjiuwen.agent_teams.schema.status import MEMBER_DEPARTED_STATUSES

    for label, value in (("sender", sender), ("client_message_id", client_message_id)):
        if not isinstance(value, str) or not value.strip() or len(value) > 255:
            raise ValueError(f"{label} must be a nonempty string of at most 255 characters")
    if not isinstance(content, str) or (not content.strip() and not attachments):
        raise ValueError("A conversation message needs text or attachments")
    if not isinstance(mentions, (list, tuple)) or len(mentions) > 100:
        raise ValueError("mentions must be a list of at most 100 member names")
    if any(not isinstance(name, str) or not name.strip() or len(name) > 255 for name in mentions):
        raise ValueError("mentions must contain nonempty member names")
    if not isinstance(attachments, (list, tuple)) or any(not isinstance(item, dict) for item in attachments):
        raise ValueError("attachments must be a list of JSON objects")
    attachments = json.loads(json.dumps(attachments, ensure_ascii=False, allow_nan=False))
    db = message_manager.db
    token = set_session_id(conversation.session_id)
    try:
        await db.initialize()
        if await db.team.get_team(conversation.team_name) is None:
            raise ValueError("Group team does not exist")

        async def member(name):
            value = await db.member.get_member(name, conversation.team_name)
            if value is None or value.status in MEMBER_DEPARTED_STATUSES:
                raise ValueError(f"Unknown or departed group member: {name}")
            return value

        author = None if sender == "user" else await member(sender)
        mentions = list(dict.fromkeys(mentions))
        targets = []
        for name in mentions:
            if name != "user" and (await member(name)).role != "passive_human":
                targets.append(name)
        meta = dict(type="group_chat", client_message_id=client_message_id,
                    sender_name=author.display_name if author else "user", mentions=mentions, attachments=attachments)
        identity = str(uuid.uuid5(uuid.NAMESPACE_URL, json.dumps(
            [conversation.team_name, conversation.session_id, client_message_id], ensure_ascii=False)))
        await db.create_cur_session_tables()
        stored = await db.message.get_message(identity)
        duplicate = stored is not None
        if stored is None:
            created = await db.message.create_message(
                identity, conversation.team_name, sender, content, broadcast=True, meta=meta, inline_content=True,
            )
            duplicate = not created
            stored = await db.message.get_message(identity)
        if stored is None:
            raise RuntimeError("Could not persist group broadcast")
        if not _same_message(stored, sender, content, mentions, attachments):
            raise ValueError("client_message_id was already used for different conversation content")
        await sync_history(conversation, message_manager)
        # Retry republishes the same row; the existing read_at prevents already-consumed input replay.
        await message_manager.publish_broadcast(identity, sender)
        return ConversationAppendResult(
            message=conversation_message(stored, conversation.session_id), duplicate=duplicate,
            context_path=str(conversation.history_path), notified_members=targets,
        )
    finally:
        reset_session_id(token)


async def deliver_group_message(backend, payload):
    """Adapt group posting to the existing interaction result contract."""
    from openjiuwen.agent_teams.interaction.payload import DeliverResult

    spec = backend.group_chat_spec
    try:
        await backend.db.initialize()
        if await backend.db.team.get_team(backend.team_name) is None:
            await backend.build_team(
                display_name=backend.team_name, desc="",
                leader_display_name=spec.leader.member_name, leader_desc="",
            )
        result = await backend.append_group_message(
            "user", payload.body, client_message_id=payload.client_message_id,
            mentions=payload.mentions, attachments=payload.attachments,
        )
        return DeliverResult(ok=True, message_id=result.message.message_id, data=result.model_dump(mode="json"))
    except ValueError:
        return DeliverResult.failure("invalid_group_chat")
    except (OSError, RuntimeError):
        return DeliverResult.failure("group_chat_delivery_failed")

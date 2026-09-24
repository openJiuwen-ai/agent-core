# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Archive public messages and notify explicitly mentioned members."""
import asyncio
import json
import uuid

from openjiuwen.agent_teams.schema.conversation import ConversationAppendResult, ConversationMessage


async def post_message(conversation, message_manager, sender: str, content: str, *, client_message_id: str,
               mentions=(), attachments=(), tail_count: int = 5, language: str = "cn"):
    """Archive public text and send mention excerpts through the ordinary mailbox."""
    from openjiuwen.agent_teams.context import reset_session_id, set_session_id
    from openjiuwen.agent_teams.i18n import STRINGS
    from openjiuwen.agent_teams.schema.status import MEMBER_DEPARTED_STATUSES
    from openjiuwen.agent_teams.tools.database.engine import get_current_time

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
    if language not in STRINGS or not isinstance(tail_count, int) or not 1 <= tail_count <= 20:
        raise ValueError("Invalid group context language or tail count")
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
        unique_mentions = list(dict.fromkeys(mentions))
        targets = []
        for name in unique_mentions:
            if name != "user" and (await member(name)).role != "passive_human":
                targets.append(name)
        message = ConversationMessage(
            message_id=str(uuid.uuid5(uuid.NAMESPACE_URL, json.dumps(
                [conversation.team_name, conversation.session_id, client_message_id], ensure_ascii=False))),
            team_name=conversation.team_name, session_id=conversation.session_id, client_message_id=client_message_id,
            sender=sender, sender_name=author.display_name if author else "user", content=content,
            mentions=unique_mentions, attachments=attachments, timestamp=get_current_time(),
        )
        message, duplicate = await asyncio.to_thread(conversation.append, message)
        context_path = str(await asyncio.to_thread(lambda: conversation.history_path))
        result = ConversationAppendResult(message=message, duplicate=duplicate, context_path=context_path)
        if duplicate:
            return result
        if targets:
            await db.create_cur_session_tables()
        for target in targets:
            after = await asyncio.to_thread(conversation.last_notified, target)
            tail = await asyncio.to_thread(
                conversation.list_messages, after_timestamp=after, through_timestamp=message.timestamp,
                limit=tail_count, latest=True, trigger_message_id=message.message_id,
            )
            excerpts = []
            for item in tail:
                excerpt = item.model_dump(exclude={"attachments"})
                excerpt["content"] = item.content[:2000]
                excerpt["content_truncated"] = len(item.content) > 2000
                excerpt["attachment_count"] = len(item.attachments)
                excerpts.append(json.dumps(excerpt, ensure_ascii=False))
            notice = STRINGS[language]["conversation.context"].format(
                from_timestamp=after, to_timestamp=message.timestamp,
                trigger_message_id=message.message_id, path=context_path, excerpts="\n".join(excerpts),
            )
            message_id = await message_manager.send_message(notice, target, from_member_name=sender)
            if message_id is None:
                raise RuntimeError(f"Could not queue group notice for {target}")
            await asyncio.to_thread(conversation.mark_notified, target, message.timestamp)
            result.notified_members.append(target)
        return result
    finally:
        reset_session_id(token)


async def deliver_group_message(backend, payload):
    """Adapt group posting to the existing interaction result contract."""
    from openjiuwen.agent_teams.interaction.payload import DeliverResult

    spec = backend.group_chat_spec
    if spec is None or not spec.enable_group_chat:
        return DeliverResult.failure("group_chat_disabled")
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

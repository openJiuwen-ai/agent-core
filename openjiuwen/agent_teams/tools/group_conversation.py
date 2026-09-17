# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Public chat history in one JSON file in the team's shared workspace."""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import uuid
from pathlib import Path

from openjiuwen.agent_teams.paths import (
    group_conversation_dir,
    group_conversation_registry_dir,
    team_home,
    team_workspace_dir,
)
from openjiuwen.agent_teams.schema.conversation import ConversationAppendResult, ConversationMessage
from openjiuwen.agent_teams.skill.file_lock import cross_process_file_lock
from openjiuwen.agent_teams.team_workspace.frontmatter import atomic_write


class GroupConversationLog:
    """Archive public messages with small per-member notification timestamps.

    Methods are synchronous; async callers use ``asyncio.to_thread``. A small
    registration file under the default team home locates custom workspaces
    when the runtime is offline. Existing sessions cannot switch workspace.
    """

    def __init__(
        self, team_name: str, session_id: str, *, workspace_path: str | Path | None = None,
    ) -> None:
        for label, value in (("team_name", team_name), ("session_id", session_id)):
            if not isinstance(value, str) or not value.strip() or len(value) > 255:
                raise ValueError(f"{label} must be a nonempty string of at most 255 characters")
        self.team_name = team_name
        self.session_id = session_id
        self._workspace_path = Path(workspace_path).expanduser().resolve() if workspace_path is not None else None
        self._registry_dir = group_conversation_registry_dir(team_name)
        self._registration = self._registry_dir / (hashlib.sha256(session_id.encode("utf-8")).hexdigest() + ".json")
        # Resolve now to reject invalid identities and conflicting roots early.
        _ = self.path

    def _workspace(self) -> Path:
        try:
            registration = json.loads(self._registration.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return self._workspace_path or team_workspace_dir(self.team_name).resolve()
        if (registration["team_name"], registration["session_id"]) != (self.team_name, self.session_id):
            raise ValueError("Conversation workspace registration has a different scope")
        root = Path(registration["workspace_path"]).resolve()
        if self._workspace_path is not None and self._workspace_path != root:
            raise ValueError("An existing conversation session cannot switch workspace")
        return root

    @property
    def path(self) -> Path:
        """Absolute history directory, also usable without a live runtime."""
        return group_conversation_dir(self.team_name, self.session_id, workspace_path=self._workspace())

    @property
    def history_path(self) -> Path:
        """Absolute path to the JSON array containing this session's messages."""
        target = self.path / "history.json"
        if target.resolve() != target:
            raise ValueError("Conversation history file must not follow symlinks or escape its directory")
        return target

    def append(self, message: ConversationMessage) -> tuple[ConversationMessage, bool]:
        """Write once; retries return the original message, including its clock."""
        if (message.team_name, message.session_id) != (self.team_name, self.session_id):
            raise ValueError("Conversation message belongs to a different scope")
        expected_id = str(uuid.uuid5(
            uuid.NAMESPACE_URL,
            json.dumps([self.team_name, self.session_id, message.client_message_id], ensure_ascii=False),
        ))
        if message.message_id != expected_id:
            raise ValueError("Conversation message_id must be derived from its client_message_id")
        if message.timestamp < 0:
            raise ValueError("Conversation timestamp must be nonnegative")
        # ponytail: rewrite the JSON array under the existing team lock; revisit if history size becomes a bottleneck.
        with cross_process_file_lock(self._registry_dir):
            root = self._workspace()
            messages = self._read_messages()
            stored = next((item for item in messages if item.message_id == expected_id), None)
            if stored is not None:
                ignored = {"timestamp", "sender_name"}
                if stored.model_dump(exclude=ignored) != message.model_dump(exclude=ignored):
                    raise ValueError("client_message_id was already used for different conversation content")
                return stored, True
            messages.append(message)
            text = json.dumps([item.model_dump() for item in messages], ensure_ascii=False, allow_nan=False, indent=2)
            if not self._registration.exists():
                registration = dict(
                    team_name=self.team_name, session_id=self.session_id, workspace_path=str(root),
                )
                atomic_write(self._registration, json.dumps(registration, ensure_ascii=False))
            atomic_write(self.history_path, text + "\n")
            return message, False

    def list_messages(
        self, *, after_timestamp: int = 0, through_timestamp: int | None = None,
        limit: int = 100, latest: bool = False, trigger_message_id: str | None = None,
    ) -> list[ConversationMessage]:
        """Select messages from the JSON array; include the trigger if the clock rolled back.

        The timestamp range is ``(after_timestamp, through_timestamp]``.
        ``latest`` chooses its last ``limit`` messages. An explicit trigger
        remains in that limit even when it falls outside the time range.
        """
        if not isinstance(after_timestamp, int) or after_timestamp < 0:
            raise ValueError("Invalid conversation history range")
        if through_timestamp is not None:
            if not isinstance(through_timestamp, int) or through_timestamp < 0:
                raise ValueError("Invalid conversation history range")
        if not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValueError("Invalid conversation history range")
        candidates = []
        trigger = None
        for item in self._read_messages():
            if item.message_id == trigger_message_id:
                trigger = item
            elif item.timestamp <= after_timestamp:
                continue
            elif through_timestamp is not None and item.timestamp > through_timestamp:
                continue
            candidates.append(item)
        candidates.sort(key=lambda item: (item.timestamp, item.message_id))
        selected = candidates[-limit:] if latest else candidates[:limit]
        if trigger is not None and trigger not in selected:
            selected[-1 if not latest else 0] = trigger
            selected.sort(key=lambda item: (item.timestamp, item.message_id))
        return selected

    def last_notified(self, member_name: str) -> int:
        """Timestamp of the last group notice saved to this member's mailbox."""
        target = self.path / ".notified.json"
        if target.resolve().parent != self.path:
            raise ValueError("Notification timestamps escape the history directory")
        try:
            return json.loads(target.read_text(encoding="utf-8")).get(member_name, 0)
        except FileNotFoundError:
            return 0

    def mark_notified(self, member_name: str, timestamp: int) -> None:
        with cross_process_file_lock(self._registry_dir):
            target = self.path / ".notified.json"
            if target.resolve().parent != self.path:
                raise ValueError("Notification timestamps escape the history directory")
            values = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {}
            values[member_name] = max(values.get(member_name, 0), timestamp)
            atomic_write(target, json.dumps(values, ensure_ascii=False))

    async def post(self, message_manager, sender: str, content: str, *, client_message_id: str,
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
        token = set_session_id(self.session_id)
        try:
            await db.initialize()
            if await db.team.get_team(self.team_name) is None:
                raise ValueError("Group team does not exist")

            async def member(name):
                value = await db.member.get_member(name, self.team_name)
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
                    [self.team_name, self.session_id, client_message_id], ensure_ascii=False))),
                team_name=self.team_name, session_id=self.session_id, client_message_id=client_message_id,
                sender=sender, sender_name=author.display_name if author else "user", content=content,
                mentions=unique_mentions, attachments=attachments, timestamp=get_current_time(),
            )
            message, duplicate = await asyncio.to_thread(self.append, message)
            context_path = str(await asyncio.to_thread(lambda: self.history_path))
            result = ConversationAppendResult(message=message, duplicate=duplicate, context_path=context_path)
            if duplicate:
                return result
            if targets:
                await db.create_cur_session_tables()
            for target in targets:
                after = await asyncio.to_thread(self.last_notified, target)
                tail = await asyncio.to_thread(
                    self.list_messages, after_timestamp=after, through_timestamp=message.timestamp,
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
                await asyncio.to_thread(self.mark_notified, target, message.timestamp)
                result.notified_members.append(target)
            return result
        finally:
            reset_session_id(token)

    def _read_messages(self) -> list[ConversationMessage]:
        try:
            records = json.loads(self.history_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            if any(self.path.glob("[0-9]*_*.json")):
                raise ValueError("Legacy per-message history must be converted to history.json before use") from None
            return []
        if not isinstance(records, list):
            raise ValueError("Conversation history must be a JSON array")
        messages = []
        for record in records:
            message = ConversationMessage.model_validate(record)
            if (message.team_name, message.session_id) != (self.team_name, self.session_id):
                raise ValueError("Conversation history contains a message from a different scope")
            messages.append(message)
        return messages

    def _delete_session(self) -> None:
        path = self.path
        if path.exists():
            shutil.rmtree(path)
        self._registration.unlink(missing_ok=True)

    def delete_session(self) -> None:
        """Remove this session's archive only, including in a custom workspace."""
        with cross_process_file_lock(self._registry_dir):
            self._delete_session()

    @classmethod
    def delete_registered(cls, team_name: str, session_id: str | None = None) -> None:
        """Delete only registered group history; leave ordinary team sessions alone."""
        if not (team_home(team_name) / "conversation-workspaces").exists():
            return
        registry_dir = group_conversation_registry_dir(team_name)
        with cross_process_file_lock(registry_dir):
            for path in registry_dir.glob("*.json"):
                registration = json.loads(path.read_text(encoding="utf-8"))
                if registration["team_name"] != team_name:
                    raise ValueError("Conversation workspace registration belongs to another team")
                if session_id is not None and registration["session_id"] != session_id:
                    continue
                log = cls(team_name, registration["session_id"])
                if log._registration != path:
                    raise ValueError("Conversation workspace registration has an invalid filename")
                log._delete_session()

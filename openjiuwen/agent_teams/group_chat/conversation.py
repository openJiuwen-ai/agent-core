# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Public chat history in one JSON Lines file in the team's shared workspace."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from openjiuwen.agent_teams.paths import (
    group_conversation_dir,
    group_conversation_registry_dir,
    team_home,
    team_workspace_dir,
)
from openjiuwen.agent_teams.schema.conversation import ConversationMessage
from openjiuwen.agent_teams.skill.file_lock import cross_process_file_lock
from openjiuwen.agent_teams.team_workspace.frontmatter import atomic_write


class GroupConversationLog:
    """File projection of public messages; all member progress lives in the DB.

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
        """Absolute path to the session history, one JSON message per line."""
        target = self.path / "history.jsonl"
        if target.resolve() != target:
            raise ValueError("Conversation history file must not follow symlinks or escape its directory")
        return target

    def sync(self, messages: list[ConversationMessage]) -> None:
        """Refresh the file from committed DB rows without dropping concurrent appends."""
        if any((m.team_name, m.session_id) != (self.team_name, self.session_id) for m in messages):
            raise ValueError("Conversation message belongs to a different scope")
        with cross_process_file_lock(self._registry_dir):
            records = {m.message_id: m for m in self._read_messages()}
            records.update((m.message_id, m) for m in messages)
            ordered = sorted(records.values(), key=lambda m: (m.timestamp, m.message_id))
            registration = dict(team_name=self.team_name, session_id=self.session_id,
                                workspace_path=str(self._workspace()))
            atomic_write(self._registration, json.dumps(registration, ensure_ascii=False))
            atomic_write(self.history_path, "".join(
                json.dumps(m.model_dump(), ensure_ascii=False, allow_nan=False) + "\n" for m in ordered
            ))

    def _read_messages(self) -> list[ConversationMessage]:
        try:
            records = [json.loads(line) for line in self.history_path.read_text(encoding="utf-8").split("\n")
                       if line.strip()]
        except FileNotFoundError:
            if any(self.path.glob("[0-9]*_*.json")):
                raise ValueError("Legacy per-message history must be converted to history.jsonl before use") from None
            return []
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

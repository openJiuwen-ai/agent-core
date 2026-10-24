# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Regenerate ``history.jsonl`` from the current session's group broadcasts."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from filelock import FileLock

from openjiuwen.agent_teams.paths import (
    group_conversation_dir,
    group_conversation_registry_dir,
    team_home,
)
from openjiuwen.agent_teams.schema.conversation import ConversationMessage

_MAX_NAME = 255


class GroupConversationLog:
    """Session-scoped projection of public messages. The database remains canonical."""

    def __init__(
        self,
        team_name: str,
        session_id: str,
        workspace: Path | str | None = None,
    ) -> None:
        self._validate(team_name, "team_name")
        self._validate(session_id, "session_id")
        self.team_name = team_name
        self.session_id = session_id
        requested = None if workspace is None else Path(workspace)
        self.workspace = self._workspace_path(requested)
        self.path = group_conversation_dir(team_name, session_id, self.workspace)
        self.history_path = self.path / "history.jsonl"

    def sync(self, messages: list[ConversationMessage]) -> None:
        """Merge ``messages`` into the projection and replace the file atomically."""
        for message in messages:
            if message.team_name != self.team_name or message.session_id != self.session_id:
                raise ValueError("conversation projection cannot mix teams or sessions")
        registry = group_conversation_registry_dir(self.team_name)
        self._reject_symlink(registry)
        registry.mkdir(parents=True, exist_ok=True)
        with FileLock(str(registry / ".conversation.lock"), timeout=10):
            self._reject_symlink(self.path)
            self._reject_symlink(self.history_path)
            existing = self._read_messages()
            merged = {item.message_id: item for item in existing}
            for message in messages:
                merged[message.message_id] = message
            ordered = sorted(merged.values(), key=lambda item: (item.timestamp, item.message_id))
            self._write_registration(registry)
            self._atomic_write(self.history_path, self._render(ordered))

    def read_messages(self) -> list[ConversationMessage]:
        """Return the projection currently on disk."""
        self._reject_symlink(self.history_path)
        return self._read_messages()

    def _read_messages(self) -> list[ConversationMessage]:
        if not self.history_path.exists():
            return []
        rows: list[ConversationMessage] = []
        for line in self.history_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(ConversationMessage.model_validate_json(line))
        return rows

    def _write_registration(self, registry: Path) -> None:
        registry.mkdir(parents=True, exist_ok=True)
        record = registry / f"{hashlib.sha256(self.session_id.encode('utf-8')).hexdigest()}.json"
        payload = {"session_id": self.session_id, "workspace": str(self.workspace)}
        if record.exists():
            stored = json.loads(record.read_text(encoding="utf-8"))
            if Path(stored["workspace"]).resolve() != self.workspace.resolve():
                raise ValueError("an existing conversation session cannot change workspace")
            return
        self._atomic_write(record, json.dumps(payload, ensure_ascii=False))

    def _workspace_path(self, requested: Path | None) -> Path:
        registry = group_conversation_registry_dir(self.team_name)
        record = registry / f"{hashlib.sha256(self.session_id.encode('utf-8')).hexdigest()}.json"
        if record.is_symlink():
            raise ValueError("conversation registration must not be a symlink")
        if record.exists():
            stored = Path(json.loads(record.read_text(encoding="utf-8"))["workspace"])
            if requested is not None and requested.resolve() != stored.resolve():
                raise ValueError("an existing conversation session cannot change workspace")
            return stored
        return requested if requested is not None else team_home(self.team_name) / "team-workspace"

    @staticmethod
    def _render(messages: list[ConversationMessage]) -> str:
        if not messages:
            return ""
        return "".join(
            message.model_dump_json(exclude_none=True) + "\n" for message in messages
        )

    @staticmethod
    def _atomic_write(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink() or path.parent.is_symlink():
            raise ValueError(f"refusing to write through a symlink: {path}")
        handle = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=".tmp-",
            delete=False,
        )
        temporary = Path(handle.name)
        try:
            with handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

    @staticmethod
    def _reject_symlink(path: Path) -> None:
        """Reject a path, or any ancestor, that is a symbolic link.

        Identity is ``is_symlink()``. Comparing ``resolve()`` with the original
        path is not used: Windows path normalization would flag ordinary
        directories.
        """
        current = path
        while True:
            if current.is_symlink():
                raise ValueError(f"conversation path must not be a symlink: {current}")
            parent = current.parent
            if parent == current:
                return
            current = parent

    @staticmethod
    def _validate(value: str, label: str) -> None:
        if not isinstance(value, str) or not value.strip() or len(value.encode("utf-8")) > _MAX_NAME:
            raise ValueError(f"{label} must be a non-empty string of at most {_MAX_NAME} bytes")

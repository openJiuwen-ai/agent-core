"""Structured session storage (JSON files).

Each CLI session is persisted as a JSON file under
``~/.openjiuwen/sessions/``.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class StoredMessage:
    """A single message in a conversation.

    Attributes:
        role: ``"user"`` | ``"assistant"`` | ``"system"``.
        content: Message text.
        timestamp: ISO-8601 formatted timestamp.
        token_count: Estimated token count (optional).
    """

    role: str
    content: str
    timestamp: str
    token_count: Optional[int] = None


@dataclass
class StoredSession:
    """Complete record of one conversation session.

    Attributes:
        session_id: Unique identifier (e.g. ``"cli-a1b2c3d4"``).
        model: Model name used in the session.
        created_at: ISO-8601 creation timestamp.
        messages: Ordered list of messages.
    """

    session_id: str
    model: str
    created_at: str
    messages: List[StoredMessage] = field(default_factory=list)


class SessionStore:
    """JSON file-based session storage.

    Args:
        store_dir: Directory for session JSON files.
            Defaults to ``~/.openjiuwen/sessions/``.
    """

    def __init__(
        self, store_dir: Optional[Path] = None
    ) -> None:
        self.store_dir = store_dir or (
            Path.home() / ".openjiuwen" / "sessions"
        )
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self._current: Optional[StoredSession] = None

    def new_session(
        self, session_id: str, model: str
    ) -> None:
        """Start tracking a new session."""
        self._current = StoredSession(
            session_id=session_id,
            model=model,
            created_at=datetime.now(tz=timezone.utc).isoformat(),
        )

    def add_message(self, role: str, content: str) -> None:
        """Append a message to the current session and persist."""
        if self._current is None:
            return
        self._current.messages.append(
            StoredMessage(
                role=role,
                content=content,
                timestamp=datetime.now(tz=timezone.utc).isoformat(),
            )
        )
        self._save()

    def list_sessions(self) -> List[Dict[str, Any]]:
        """Return summaries of all persisted sessions."""
        sessions: list[dict[str, Any]] = []
        for f in sorted(self.store_dir.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                sessions.append(
                    {
                        "id": data["session_id"],
                        "model": data["model"],
                        "created_at": data["created_at"],
                        "turns": len(data.get("messages", [])),
                    }
                )
            except (json.JSONDecodeError, KeyError):
                continue
        return sessions

    def _save(self) -> None:
        """Write current session to its JSON file."""
        if self._current is None:
            return
        path = (
            self.store_dir / f"{self._current.session_id}.json"
        )
        path.write_text(
            json.dumps(
                asdict(self._current),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

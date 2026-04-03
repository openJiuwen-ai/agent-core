"""Unit tests for openjiuwen.harness.cli.storage.session_store."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from openjiuwen.harness.cli.storage.session_store import SessionStore


class TestSessionStore:
    """Tests for JSON session storage."""

    def test_new_session(self, tmp_path: Path) -> None:
        """A new session is created."""
        store = SessionStore(store_dir=tmp_path)
        store.new_session("test-001", "gpt-4o")
        assert store._current is not None
        assert store._current.session_id == "test-001"

    def test_add_message_and_save(
        self, tmp_path: Path
    ) -> None:
        """Messages are added and persisted to JSON."""
        store = SessionStore(store_dir=tmp_path)
        store.new_session("test-002", "gpt-4o")
        store.add_message("user", "hello")
        store.add_message("assistant", "hi")

        f = tmp_path / "test-002.json"
        assert f.exists()
        data = json.loads(f.read_text())
        assert len(data["messages"]) == 2
        assert data["messages"][0]["role"] == "user"
        assert data["messages"][0]["content"] == "hello"
        assert data["messages"][1]["role"] == "assistant"
        assert data["messages"][1]["content"] == "hi"

    def test_list_sessions(self, tmp_path: Path) -> None:
        """All persisted sessions are listed."""
        store = SessionStore(store_dir=tmp_path)
        store.new_session("s1", "gpt-4o")
        store.add_message("user", "q1")
        store.new_session("s2", "qwen-max")
        store.add_message("user", "q2")

        sessions = store.list_sessions()
        assert len(sessions) == 2
        ids = {s["id"] for s in sessions}
        assert "s1" in ids
        assert "s2" in ids

    def test_add_message_without_session(
        self, tmp_path: Path
    ) -> None:
        """add_message without a session does not crash."""
        store = SessionStore(store_dir=tmp_path)
        store.add_message("user", "hello")  # no exception
        assert len(list(tmp_path.glob("*.json"))) == 0

    def test_message_has_timestamp(
        self, tmp_path: Path
    ) -> None:
        """Messages contain ISO-formatted timestamps."""
        store = SessionStore(store_dir=tmp_path)
        store.new_session("test-ts", "gpt-4o")
        store.add_message("user", "test")

        data = json.loads(
            (tmp_path / "test-ts.json").read_text()
        )
        ts = data["messages"][0]["timestamp"]
        # Must be parseable as ISO datetime
        datetime.fromisoformat(ts)

    def test_session_metadata(self, tmp_path: Path) -> None:
        """Session JSON contains id, model, created_at."""
        store = SessionStore(store_dir=tmp_path)
        store.new_session("meta-test", "gpt-4o")
        store.add_message("user", "x")

        data = json.loads(
            (tmp_path / "meta-test.json").read_text()
        )
        assert data["session_id"] == "meta-test"
        assert data["model"] == "gpt-4o"
        assert "created_at" in data
        datetime.fromisoformat(data["created_at"])

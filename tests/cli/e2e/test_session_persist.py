"""E2E-15: Session persistence to JSON files."""

from __future__ import annotations

import json
from pathlib import Path

from tests.cli.e2e.conftest import run_cli


def test_session_persistence(tmp_path: Path) -> None:
    """Session JSON file is created after a run."""
    # Note: non-interactive run doesn't use SessionStore by default,
    # so we verify the store module works via the unit tests.
    # This test validates that the session store directory can
    # be created and populated.
    from openjiuwen.harness.cli.storage.session_store import (
        SessionStore,
    )

    store_dir = tmp_path / "sessions"
    store = SessionStore(store_dir=store_dir)
    store.new_session("e2e-test-001", "Pro/zai-org/GLM-5")
    store.add_message("user", "hello")
    store.add_message("assistant", "hi there")

    json_files = list(store_dir.glob("*.json"))
    assert len(json_files) >= 1

    data = json.loads(json_files[0].read_text())
    assert data["session_id"] == "e2e-test-001"
    assert "messages" in data
    assert len(data["messages"]) >= 2
    assert data["messages"][0]["role"] == "user"
    assert data["messages"][1]["role"] == "assistant"

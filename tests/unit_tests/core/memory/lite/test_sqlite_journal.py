"""Lite memory supports an explicit SQLite rollback journal."""

import sqlite3

import pytest

from openjiuwen.core.memory.lite.config import create_memory_settings
from openjiuwen.core.memory.lite.manager import _open_database


def test_memory_settings_honor_journal_environment(monkeypatch):
    monkeypatch.setenv("OPENJIUWEN_MEMORY_SQLITE_JOURNAL_MODE", "DELETE")
    assert create_memory_settings().store["journal_mode"] == "DELETE"


def test_memory_database_switches_existing_wal_and_retains_data(tmp_path):
    path = tmp_path / "memory.db"
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE existing(value TEXT)")
        connection.execute("INSERT INTO existing VALUES ('kept')")
    connection.close()

    connection = _open_database(str(path), journal_mode="DELETE")
    try:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert connection.execute("SELECT value FROM existing").fetchone()[0] == "kept"
    finally:
        connection.close()


def test_memory_database_rejects_unknown_journal_mode(tmp_path):
    with pytest.raises(ValueError, match="journal_mode"):
        _open_database(str(tmp_path / "memory.db"), journal_mode="MEMORY")

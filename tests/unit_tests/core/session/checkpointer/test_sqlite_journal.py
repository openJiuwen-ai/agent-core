"""Persistent SQLite journal selection for shared filesystem deployments."""

import sqlite3

import pytest
from sqlalchemy import text

from openjiuwen.core.session.checkpointer.persistence import PersistenceCheckpointerProvider


@pytest.mark.asyncio
async def test_delete_journal_overrides_existing_wal_and_persists_writes(tmp_path):
    path = tmp_path / "checkpoint.db"
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE existing(value TEXT)")
        connection.execute("INSERT INTO existing VALUES ('kept')")
    connection.close()
    checkpointer = await PersistenceCheckpointerProvider().create({"db_path": str(path), "db_journal_mode": "DELETE"})
    engine = checkpointer._kv_store.engine
    try:
        async with engine.begin() as connection:
            assert (await connection.execute(text("PRAGMA journal_mode"))).scalar() == "delete"
            assert (await connection.execute(text("SELECT value FROM existing"))).scalar() == "kept"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_invalid_journal_mode_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="db_journal_mode"):
        await PersistenceCheckpointerProvider().create(
            {
                "db_path": str(tmp_path / "checkpoint.db"),
                "db_journal_mode": "MEMORY",
            }
        )

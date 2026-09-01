# coding: utf-8

import pytest
from sqlalchemy import inspect

from openjiuwen.agent_teams.organization.db import ensure_org_schema, json_dumps, json_loads
from openjiuwen.agent_teams.organization.schema import ORG_STATIC_TABLE_NAMES
from openjiuwen.agent_teams.tools.database import DatabaseConfig, DatabaseType, TeamDatabase


@pytest.mark.asyncio
async def test_ensure_org_schema_creates_static_tables():
    db = TeamDatabase(DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:"))
    await ensure_org_schema(db)
    assert db.engine is not None
    async with db.engine.connect() as conn:
        table_names = await conn.run_sync(lambda sync_conn: set(inspect(sync_conn).get_table_names()))
        message_columns = await conn.run_sync(
            lambda sync_conn: {
                column["name"]
                for column in inspect(sync_conn).get_columns("org_leader_message")
            }
        )
    assert set(ORG_STATIC_TABLE_NAMES).issubset(table_names)
    assert "read_at" not in message_columns
    await db.close()


def test_json_helpers_roundtrip_and_defaults():
    assert json_dumps(None) is None
    assert json_loads(None, []) == []
    assert json_loads("{bad", {"ok": False}) == {"ok": False}
    payload = {"a": 1, "b": "中文"}
    assert json_loads(json_dumps(payload), {}) == payload

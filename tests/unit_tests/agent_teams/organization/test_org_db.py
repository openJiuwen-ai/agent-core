# coding: utf-8

import pytest
from sqlalchemy import inspect

from openjiuwen.agent_teams.organization.db import OrgDbContext, json_dumps, json_loads
from openjiuwen.agent_teams.organization.manager import TeamOrganizationManager
from openjiuwen.agent_teams.organization.schema import ORG_STATIC_TABLE_NAMES
from openjiuwen.agent_teams.tools.database import DatabaseConfig, DatabaseType, TeamDatabase


@pytest.mark.asyncio
async def test_org_db_context_creates_static_tables():
    db = TeamDatabase(DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:"))
    await OrgDbContext(db).initialize()
    assert db.engine is not None
    async with db.engine.connect() as conn:
        table_names = await conn.run_sync(lambda sync_conn: set(inspect(sync_conn).get_table_names()))
        message_columns = await conn.run_sync(
            lambda sync_conn: {
                column["name"]
                for column in inspect(sync_conn).get_columns("org_leader_message")
            }
        )
        receipt_columns = await conn.run_sync(
            lambda sync_conn: {
                column["name"]
                for column in inspect(sync_conn).get_columns("org_leader_message_receipt")
            }
        )
    assert set(ORG_STATIC_TABLE_NAMES).issubset(table_names)
    assert "read_at" not in message_columns
    assert "read_at" not in receipt_columns
    await db.close()


def test_json_helpers_roundtrip_and_defaults():
    assert json_dumps(None) is None
    assert json_loads(None, []) == []
    assert json_loads("{bad", {"ok": False}) == {"ok": False}
    payload = {"a": 1, "b": "中文"}
    assert json_loads(json_dumps(payload), {}) == payload


@pytest.mark.asyncio
async def test_org_db_context_runs_static_ddl_once(monkeypatch):
    from openjiuwen.agent_teams.organization import db as org_db

    db = TeamDatabase(DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:"))
    calls: list[int] = []
    original = org_db.ensure_org_static_tables

    def tracked_ensure_org_static_tables(sync_conn) -> None:
        calls.append(1)
        original(sync_conn)

    monkeypatch.setattr(org_db, "ensure_org_static_tables", tracked_ensure_org_static_tables)
    context = OrgDbContext(db)
    await context.initialize()
    await context.initialize()
    assert len(calls) == 1
    await db.close()


@pytest.mark.asyncio
async def test_team_organization_manager_shares_db_context():
    db = TeamDatabase(DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:"))
    manager = TeamOrganizationManager(organization_id="org-1", db=db)
    assert manager.task_pool.db_context is manager.message_service.db_context
    await manager.task_pool.initialize()
    await manager.message_service.initialize()
    assert manager.task_pool.db_context.sessions is manager.message_service.db_context.sessions
    await db.close()

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
        org_task_columns = await conn.run_sync(
            lambda sync_conn: {col["name"] for col in inspect(sync_conn).get_columns("org_task")}
        )
    assert set(ORG_STATIC_TABLE_NAMES).issubset(table_names)
    assert "org_summary_execution" not in table_names
    assert "read_at" not in message_columns
    assert "read_at" not in receipt_columns
    assert {
        "aggregation_json",
        "failure_code",
        "failure_reason",
        "failed_at",
    }.issubset(org_task_columns)
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


@pytest.mark.asyncio
async def test_ensure_org_task_columns_normalizes_legacy_terminal_statuses():
    from openjiuwen.agent_teams.organization import db as org_db

    db = TeamDatabase(DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:"))
    await db.initialize()
    assert db.engine is not None

    def create_legacy_org_task(sync_conn) -> None:
        sync_conn.exec_driver_sql(
            """
            CREATE TABLE org_task (
                task_id TEXT PRIMARY KEY,
                organization_id TEXT,
                parent_task_id TEXT,
                root_task_id TEXT,
                creator_type TEXT,
                creator_id TEXT,
                creator_team_id TEXT,
                status TEXT,
                created_at INTEGER,
                updated_at INTEGER,
                title TEXT,
                description TEXT,
                task_type TEXT,
                required_capabilities_json TEXT,
                assignment_type TEXT,
                assigned_team_id TEXT,
                assigned_leader_id TEXT,
                assigned_by_team_id TEXT,
                assigned_at INTEGER,
                output_spec_json TEXT,
                output_context_json TEXT,
                output_abstract TEXT,
                metadata_json TEXT
            )
            """
        )
        sync_conn.exec_driver_sql(
            """
            INSERT INTO org_task (
                task_id, organization_id, parent_task_id, root_task_id,
                creator_type, creator_id, creator_team_id, status,
                created_at, updated_at, title, description, task_type,
                required_capabilities_json, assignment_type,
                assigned_team_id, assigned_leader_id, assigned_by_team_id,
                assigned_at, output_spec_json, output_context_json,
                output_abstract, metadata_json
            ) VALUES
            ('t-cancelled', 'org-1', NULL, 't-cancelled', 'client', 'c1', NULL,
             'CANCELLED', 1, 10, 'cancelled', 'd', NULL, '[]', 'unassigned',
             NULL, NULL, NULL, NULL, NULL, NULL, NULL, '{}'),
            ('t-expired', 'org-1', NULL, 't-expired', 'client', 'c1', NULL,
             'EXPIRED', 2, 20, 'expired', 'd', NULL, '[]', 'unassigned',
             NULL, NULL, NULL, NULL, NULL, NULL, NULL, '{}')
            """
        )

    async with db.engine.begin() as conn:
        await conn.run_sync(create_legacy_org_task)
        await conn.run_sync(org_db.ensure_org_static_tables)

    async with db.engine.connect() as conn:
        rows = await conn.exec_driver_sql(
            "SELECT task_id, status, failure_code, failed_at FROM org_task ORDER BY task_id"
        )
        data = {row[0]: (row[1], row[2], row[3]) for row in rows}

    assert data["t-cancelled"] == ("FAILED", "CANCELLED", 10)
    assert data["t-expired"] == ("FAILED", "EXPIRED", 20)
    await db.close()

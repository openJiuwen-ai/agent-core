# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared DB helpers for organization task pool and message service."""

from __future__ import annotations

import json
import weakref
from typing import Any

from sqlalchemy import inspect
from sqlmodel import SQLModel

from openjiuwen.agent_teams.organization.schema import (
    ORG_TASK_LEGACY_STATUS_FAILURE_CODES,
    OrgTaskStatus,
    org_static_tables,
)
from openjiuwen.agent_teams.tools.database import TeamDatabase
from openjiuwen.agent_teams.tools.database.engine import DbSessions, retry_on_transient
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import raise_error

_ORG_TASK_NEW_COLUMNS = (
    ("aggregation_json", "TEXT"),
    ("failure_code", "TEXT"),
    ("failure_reason", "TEXT"),
    ("failed_at", "INTEGER"),
    ("unclaimed_phase", "TEXT"),
    ("unclaimed_deadline_at", "BIGINT"),
    ("unclaimed_json", "TEXT"),
    ("recreated_from_task_id", "TEXT"),
    ("recreation_request_id", "TEXT"),
)


def ensure_org_static_tables(sync_conn) -> None:
    SQLModel.metadata.create_all(sync_conn, tables=org_static_tables())
    _ensure_org_task_columns(sync_conn)
    _ensure_org_summary_execution_columns(sync_conn)
    inspector = inspect(sync_conn)
    if "unclaimed_task_policy_json" not in {col["name"] for col in inspector.get_columns("org_info")}:
        sync_conn.exec_driver_sql("ALTER TABLE org_info ADD COLUMN unclaimed_task_policy_json TEXT")
    task_table = SQLModel.metadata.tables["org_task"]
    for index in task_table.indexes:
        if index.name in {"ix_org_task_unclaimed_due", "ix_org_task_recreation_request"}:
            index.create(sync_conn, checkfirst=True)


def _ensure_org_task_columns(sync_conn) -> None:
    """Backfill org_task columns and normalize legacy terminal statuses."""

    inspector = inspect(sync_conn)
    if "org_task" not in inspector.get_table_names():
        return
    columns = {col["name"] for col in inspector.get_columns("org_task")}
    for name, col_type in _ORG_TASK_NEW_COLUMNS:
        if name not in columns:
            sync_conn.exec_driver_sql(f"ALTER TABLE org_task ADD COLUMN {name} {col_type}")
    failed_status = OrgTaskStatus.FAILED.value
    for legacy_status, failure_code in ORG_TASK_LEGACY_STATUS_FAILURE_CODES.items():
        sync_conn.exec_driver_sql(
            f"UPDATE org_task SET status = '{failed_status}', "
            f"failure_code = '{failure_code.value}', "
            "failed_at = COALESCE(failed_at, updated_at) "
            f"WHERE status = '{legacy_status}' AND failure_code IS NULL"
        )


def _ensure_org_summary_execution_columns(sync_conn) -> None:
    """Add columns introduced after org_summary_execution was first created."""

    inspector = inspect(sync_conn)
    if "org_summary_execution" not in inspector.get_table_names():
        return
    columns = {col["name"] for col in inspector.get_columns("org_summary_execution")}
    if "updated_at" not in columns:
        sync_conn.exec_driver_sql("ALTER TABLE org_summary_execution ADD COLUMN updated_at BIGINT NOT NULL DEFAULT 0")


# ``DbSessions`` providers whose database already has the org static schema.
# Keyed by the provider rather than the ``TeamDatabase``: ``close()`` +
# ``initialize()`` builds a fresh provider over a fresh engine (a new
# ``:memory:`` database has no tables), so the mark must not survive it.
_ORG_SCHEMA_READY: weakref.WeakSet[DbSessions] = weakref.WeakSet()


async def _ensure_org_schema(sessions: DbSessions) -> None:
    """Create / migrate the org static tables once per ``DbSessions``.

    The DDL runs inside ``sessions.write()`` so it serialises with every DAO
    write of the same ``TeamDatabase``. A bare ``engine.begin()`` bypassed that
    lock: concurrent org lookups (``team.list`` polling, leader activation)
    each held a write-pool connection while parked on the SQLite file lock and
    exhausted the 2-connection pool (``QueuePool limit of size 2``).

    Must not be called from inside an open ``sessions.write()`` block: the
    write lock is non-reentrant, so the first call on a fresh database would
    deadlock. Org stores call ``initialize()`` before opening their writes.
    Transient errors are retried; once the budget is spent the last error is
    raised, since org reads and writes cannot work without these tables.
    """
    if sessions in _ORG_SCHEMA_READY:
        return

    async def _ddl_once() -> None:
        async with sessions.write() as session:
            # Re-check under the lock: a writer queued ahead may have done it.
            if sessions in _ORG_SCHEMA_READY:
                return
            conn = await session.connection()
            await conn.run_sync(ensure_org_static_tables)
            await session.commit()
        _ORG_SCHEMA_READY.add(sessions)

    await retry_on_transient(_ddl_once, label="ensure_org_static_tables")


class OrgDbContext:
    """Shared org DB bootstrap for task pool and message service."""

    def __init__(self, db: TeamDatabase) -> None:
        self.db = db

    async def initialize(self) -> DbSessions:
        """Ensure the org schema on the database's shared ``DbSessions``.

        Reuses ``TeamDatabase.sessions`` instead of building a second provider:
        a second ``DbSessions`` carries a second write lock, so org writes would
        race the DAO writes for the same small write pool. A closed database is
        never reopened: ``TeamDatabase.initialize()`` raises once it was closed.
        """
        await self.db.initialize()
        sessions = self.sessions
        await _ensure_org_schema(sessions)
        return sessions

    @property
    def sessions(self) -> DbSessions:
        """Return the database's current provider; never cached here.

        Reading it through ``TeamDatabase`` on every access means a holder can
        never keep writing on a provider (and write lock) the database dropped.
        """
        sessions = self.db.sessions
        if sessions is None:
            raise_error(
                StatusCode.AGENT_TEAM_STATE_INVALID,
                reason="TeamDatabase is not initialized or was closed",
            )
        return sessions


def json_dumps(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def json_loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


__all__ = [
    "OrgDbContext",
    "ensure_org_static_tables",
    "json_dumps",
    "json_loads",
]

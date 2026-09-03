# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared DB helpers for organization task pool and message service."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from sqlalchemy import inspect
from sqlmodel import SQLModel

from openjiuwen.agent_teams.organization.schema import (
    ORG_TASK_LEGACY_STATUS_FAILURE_CODES,
    OrgTaskStatus,
    org_static_tables,
)
from openjiuwen.agent_teams.tools.database import TeamDatabase
from openjiuwen.agent_teams.tools.database.engine import DbSessions

_ORG_TASK_NEW_COLUMNS = (
    ("aggregation_json", "TEXT"),
    ("failure_code", "TEXT"),
    ("failure_reason", "TEXT"),
    ("failed_at", "INTEGER"),
)


def ensure_org_static_tables(sync_conn) -> None:
    SQLModel.metadata.create_all(sync_conn, tables=org_static_tables())
    _ensure_org_task_columns(sync_conn)


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


class OrgDbContext:
    """Shared org DB bootstrap for task pool and message service."""

    def __init__(self, db: TeamDatabase) -> None:
        self.db = db
        self._sessions: DbSessions | None = None
        self._initialized = False
        self._init_lock = asyncio.Lock()

    async def initialize(self) -> DbSessions:
        if self._initialized and self._sessions is not None:
            return self._sessions
        async with self._init_lock:
            if self._initialized and self._sessions is not None:
                return self._sessions
            await self.db.initialize()
            if self.db.session_local is None or self.db.engine is None:
                raise RuntimeError("TeamDatabase is not initialized")
            self._sessions = DbSessions(self.db.session_local, self.db.read_session_local)
            async with self.db.engine.begin() as conn:
                await conn.run_sync(ensure_org_static_tables)
            self._initialized = True
            return self._sessions

    @property
    def sessions(self) -> DbSessions:
        if self._sessions is None:
            raise RuntimeError("OrgDbContext is not initialized")
        return self._sessions


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

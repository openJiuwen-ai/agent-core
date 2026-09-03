# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared DB helpers for organization task pool and message service."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from sqlmodel import SQLModel

from openjiuwen.agent_teams.organization.schema import org_static_tables
from openjiuwen.agent_teams.tools.database import TeamDatabase
from openjiuwen.agent_teams.tools.database.engine import DbSessions


def ensure_org_static_tables(sync_conn) -> None:
    SQLModel.metadata.create_all(sync_conn, tables=org_static_tables())


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

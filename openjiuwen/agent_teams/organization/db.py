# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared DB helpers for organization task pool and message service."""

from __future__ import annotations

import json
from typing import Any

from sqlmodel import SQLModel

from openjiuwen.agent_teams.organization.schema import org_static_tables
from openjiuwen.agent_teams.tools.database import TeamDatabase


def ensure_org_static_tables(sync_conn) -> None:
    SQLModel.metadata.create_all(sync_conn, tables=org_static_tables())


async def ensure_org_schema(db: TeamDatabase) -> None:
    """Initialize the team DB engine and create organization static tables."""

    await db.initialize()
    if db.engine is None:
        return
    async with db.engine.begin() as conn:
        await conn.run_sync(ensure_org_static_tables)


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
    "ensure_org_schema",
    "ensure_org_static_tables",
    "json_dumps",
    "json_loads",
]

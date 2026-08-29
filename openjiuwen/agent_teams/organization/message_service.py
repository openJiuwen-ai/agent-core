# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""DB-backed organization leader inbox (message service)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, select

from openjiuwen.agent_teams.organization.db import (
    ensure_org_schema,
    ensure_org_static_tables,
    json_dumps,
    json_loads,
)
from openjiuwen.agent_teams.organization.schema import OrgLeaderMessageRecord
from openjiuwen.agent_teams.organization.transport_api import create_message_id
from openjiuwen.agent_teams.tools.database import TeamDatabase
from openjiuwen.agent_teams.tools.database.engine import DbSessions, get_current_time


@dataclass
class OrgMessageOpResult:
    ok: bool
    reason: str = ""
    data: dict[str, Any] | None = None


class OrgMessageService:
    """Persist and query leader-to-leader organization messages.

    Notification delivery stays with TransportAPI; this service owns durable
    message rows only.
    """

    def __init__(
        self,
        *,
        db: TeamDatabase,
        organization_id: str,
        session_id: str | None = None,
    ) -> None:
        self.db = db
        self.organization_id = organization_id
        self.session_id = session_id
        self._sessions: DbSessions | None = None

    async def initialize(self) -> None:
        await ensure_org_schema(self.db)
        if self.db.session_local is None or self.db.engine is None:
            raise RuntimeError("TeamDatabase is not initialized")
        if self._sessions is None:
            self._sessions = DbSessions(self.db.session_local)
        async with self.db.engine.begin() as conn:
            await conn.run_sync(ensure_org_static_tables)

    def _read(self):
        if self._sessions is None:
            raise RuntimeError("OrgMessageService is not initialized")
        return self._sessions.read()

    def _write(self):
        if self._sessions is None:
            raise RuntimeError("OrgMessageService is not initialized")
        return self._sessions.write()

    async def send_leader_message(
        self,
        *,
        from_team_id: str,
        from_leader_id: str,
        content: str,
        to_team_id: str | None = None,
        to_leader_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> OrgMessageOpResult:
        await self.initialize()
        message_id = create_message_id()
        now = get_current_time()
        async with self._write() as session:
            row = OrgLeaderMessageRecord(
                message_id=message_id,
                organization_id=self.organization_id,
                from_team_id=from_team_id,
                from_leader_id=from_leader_id,
                to_team_id=to_team_id,
                to_leader_id=to_leader_id,
                content=content,
                created_at=now,
                metadata_json=json_dumps(metadata or {}),
            )
            session.add(row)
            await session.commit()
        # Notification is owned by TransportAPI (org_send_leader_message → deliver).
        return OrgMessageOpResult(ok=True, data=self._message_dict(row))

    async def list_leader_messages(
        self,
        *,
        team_id: str,
        include_broadcast: bool = True,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        await self.initialize()
        stmt = select(OrgLeaderMessageRecord).where(
            OrgLeaderMessageRecord.organization_id == self.organization_id
        )
        if include_broadcast:
            stmt = stmt.where(
                (OrgLeaderMessageRecord.to_team_id == team_id)
                | (OrgLeaderMessageRecord.to_team_id.is_(None))
            )
        else:
            stmt = stmt.where(OrgLeaderMessageRecord.to_team_id == team_id)
        stmt = stmt.order_by(OrgLeaderMessageRecord.created_at.desc()).limit(limit)
        async with self._read() as session:
            rows = (await session.execute(stmt)).scalars().all()
            return [self._message_dict(row) for row in rows]

    async def purge_organization(self) -> int:
        """Delete every leader message owned by this organization."""

        await self.initialize()
        async with self._write() as session:
            result = await session.execute(
                delete(OrgLeaderMessageRecord).where(
                    OrgLeaderMessageRecord.organization_id == self.organization_id
                )
            )
            await session.commit()
            return max(result.rowcount or 0, 0)

    @staticmethod
    def _message_dict(row: OrgLeaderMessageRecord) -> dict[str, Any]:
        return {
            "message_id": row.message_id,
            "organization_id": row.organization_id,
            "from_team_id": row.from_team_id,
            "from_leader_id": row.from_leader_id,
            "to_team_id": row.to_team_id,
            "to_leader_id": row.to_leader_id,
            "content": row.content,
            "created_at": row.created_at,
            "read_at": row.read_at,
            "metadata": json_loads(row.metadata_json, {}),
        }


__all__ = ["OrgMessageOpResult", "OrgMessageService"]

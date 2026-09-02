# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""DB-backed organization leader inbox (message service)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, select

from openjiuwen.agent_teams.organization.db import (
    OrgDbContext,
    json_dumps,
    json_loads,
)
from openjiuwen.agent_teams.organization.schema import (
    OrgLeaderMessageReceiptRecord,
    OrgLeaderMessageRecord,
    OrgLeaderRecord,
)
from openjiuwen.agent_teams.organization.transport_api import TransportAPI, create_message_id
from openjiuwen.agent_teams.tools.database import TeamDatabase
from openjiuwen.agent_teams.tools.database.engine import get_current_time


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
        messager: Any | None = None,
        db_context: OrgDbContext | None = None,
    ) -> None:
        self.db = db
        self.organization_id = organization_id
        self.session_id = session_id
        self.messager = messager
        self.db_context = db_context or OrgDbContext(db)

    async def initialize(self) -> None:
        await self.db_context.initialize()

    def _read(self):
        return self.db_context.sessions.read()

    def _write(self):
        return self.db_context.sessions.write()

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
        recipient_leaders = await self._resolve_recipients(
            from_team_id=from_team_id,
            to_team_id=to_team_id,
            to_leader_id=to_leader_id,
        )
        if not recipient_leaders:
            return OrgMessageOpResult(ok=False, reason="no delivery targets for leader message")
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
            for recipient_team_id, recipient_leader_id in recipient_leaders:
                session.add(
                    OrgLeaderMessageReceiptRecord(
                        message_id=message_id,
                        recipient_team_id=recipient_team_id,
                        organization_id=self.organization_id,
                        recipient_leader_id=recipient_leader_id,
                        created_at=now,
                    )
                )
            await session.commit()
        data = self._message_dict(row)
        delivered: list[str] = []
        if self.messager is not None and self.session_id:
            transport = TransportAPI(
                organization_id=self.organization_id,
                session_id=self.session_id,
                from_team_id=from_team_id,
                messager=self.messager,
            )
            for recipient_team_id, _ in recipient_leaders:
                result = await transport.deliver(
                    content,
                    recipient_team_id,
                    message_id=message_id,
                )
                if not result.success:
                    return OrgMessageOpResult(ok=False, reason=result.reason or "message delivery failed", data=data)
                delivered.append(recipient_team_id)
        data["delivered_to"] = delivered
        return OrgMessageOpResult(ok=True, data=data)

    async def list_leader_messages(
        self,
        *,
        team_id: str,
        include_broadcast: bool = True,
        unread_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        await self.initialize()
        stmt = (
            select(OrgLeaderMessageRecord, OrgLeaderMessageReceiptRecord)
            .join(
                OrgLeaderMessageReceiptRecord,
                OrgLeaderMessageReceiptRecord.message_id == OrgLeaderMessageRecord.message_id,
            )
            .where(
                OrgLeaderMessageRecord.organization_id == self.organization_id,
                OrgLeaderMessageReceiptRecord.organization_id == self.organization_id,
                OrgLeaderMessageReceiptRecord.recipient_team_id == team_id,
            )
        )
        if not include_broadcast:
            stmt = stmt.where(OrgLeaderMessageRecord.to_team_id == team_id)
        if unread_only:
            stmt = stmt.where(OrgLeaderMessageReceiptRecord.handled_at.is_(None))
        stmt = (
            stmt.order_by(
                OrgLeaderMessageRecord.created_at.desc(),
                OrgLeaderMessageRecord.message_id.desc(),
            )
            .offset(offset)
            .limit(limit)
        )
        async with self._read() as session:
            rows = (await session.execute(stmt)).all()
            return [self._message_dict(message, receipt) for message, receipt in rows]

    async def get_leader_message(self, *, message_id: str, team_id: str) -> dict[str, Any] | None:
        await self.initialize()
        stmt = (
            select(OrgLeaderMessageRecord, OrgLeaderMessageReceiptRecord)
            .join(
                OrgLeaderMessageReceiptRecord,
                OrgLeaderMessageReceiptRecord.message_id == OrgLeaderMessageRecord.message_id,
            )
            .where(
                OrgLeaderMessageRecord.message_id == message_id,
                OrgLeaderMessageRecord.organization_id == self.organization_id,
                OrgLeaderMessageReceiptRecord.recipient_team_id == team_id,
            )
        )
        async with self._read() as session:
            row = (await session.execute(stmt)).one_or_none()
            return self._message_dict(*row) if row else None

    async def ack_leader_message(
        self,
        *,
        message_id: str,
        team_id: str,
        leader_id: str,
        handling_result: str | None = None,
    ) -> OrgMessageOpResult:
        await self.initialize()
        key = (message_id, team_id)
        async with self._write() as session:
            receipt = await session.get(OrgLeaderMessageReceiptRecord, key)
            if receipt is None or receipt.organization_id != self.organization_id:
                return OrgMessageOpResult(ok=False, reason="leader message not found")
            already_handled = receipt.handled_at is not None
            if not already_handled:
                now = get_current_time()
                receipt.recipient_leader_id = leader_id
                receipt.handled_at = now
                receipt.handling_result_json = json_dumps(handling_result)
                await session.commit()
            data = self._receipt_dict(receipt)
            data["already_handled"] = already_handled
            return OrgMessageOpResult(ok=True, data=data)

    async def purge_organization(self) -> int:
        """Delete every leader message owned by this organization."""

        await self.initialize()
        async with self._write() as session:
            await session.execute(
                delete(OrgLeaderMessageReceiptRecord).where(
                    OrgLeaderMessageReceiptRecord.organization_id == self.organization_id
                )
            )
            result = await session.execute(
                delete(OrgLeaderMessageRecord).where(
                    OrgLeaderMessageRecord.organization_id == self.organization_id
                )
            )
            await session.commit()
            return max(result.rowcount or 0, 0)

    async def _resolve_recipients(
        self,
        *,
        from_team_id: str,
        to_team_id: str | None,
        to_leader_id: str | None,
    ) -> list[tuple[str, str | None]]:
        stmt = select(OrgLeaderRecord).where(
            OrgLeaderRecord.organization_id == self.organization_id
        )
        if to_team_id:
            stmt = stmt.where(OrgLeaderRecord.team_id == to_team_id)
        else:
            stmt = stmt.where(OrgLeaderRecord.team_id != from_team_id)
        async with self._read() as session:
            leaders = (await session.execute(stmt)).scalars().all()
        if to_leader_id:
            leaders = [leader for leader in leaders if leader.leader_id == to_leader_id]
        recipients = {leader.team_id: leader.leader_id for leader in leaders}
        return sorted(recipients.items())

    @classmethod
    def _message_dict(
        cls,
        row: OrgLeaderMessageRecord,
        receipt: OrgLeaderMessageReceiptRecord | None = None,
    ) -> dict[str, Any]:
        data = {
            "message_id": row.message_id,
            "organization_id": row.organization_id,
            "from_team_id": row.from_team_id,
            "from_leader_id": row.from_leader_id,
            "to_team_id": row.to_team_id,
            "to_leader_id": row.to_leader_id,
            "content": row.content,
            "created_at": row.created_at,
            "metadata": json_loads(row.metadata_json, {}),
        }
        if receipt is not None:
            data.update(cls._receipt_dict(receipt))
        return data

    @staticmethod
    def _receipt_dict(receipt: OrgLeaderMessageReceiptRecord) -> dict[str, Any]:
        return {
            "recipient_team_id": receipt.recipient_team_id,
            "recipient_leader_id": receipt.recipient_leader_id,
            "handled_at": receipt.handled_at,
            "handling_result": json_loads(receipt.handling_result_json, None),
        }


__all__ = ["OrgMessageOpResult", "OrgMessageService"]

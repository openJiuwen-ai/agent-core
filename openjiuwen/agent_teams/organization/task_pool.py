# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""DB-backed organization task pool and leader-message manager."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlmodel import SQLModel

from openjiuwen.agent_teams.context import get_session_id
from openjiuwen.agent_teams.messager import Messager
from openjiuwen.agent_teams.organization.events import (
    BaseOrgEvent,
    OrgEventMessage,
    OrgLeaderMessageEvent,
    OrgTaskClaimedEvent,
    OrgTaskCompletedEvent,
    OrgTaskCreatedEvent,
    OrgTaskDelegatedEvent,
    OrgTopic,
)
from openjiuwen.agent_teams.organization.schema import (
    OrgAssignment,
    OrgAssignmentType,
    OrgInfoRecord,
    OrgLeaderHandle,
    OrgLeaderMessageRecord,
    OrgLeaderRecord,
    OrganizationSpec,
    OrgTask,
    OrgTaskCreator,
    OrgTaskOutputContext,
    OrgTaskOutputSpec,
    OrgTaskRecord,
    OrgTaskStatus,
)
from openjiuwen.agent_teams.tools.database import TeamDatabase
from openjiuwen.agent_teams.tools.database.engine import DbSessions, get_current_time


@dataclass
class OrgTaskOpResult:
    ok: bool
    task: OrgTask | None = None
    reason: str = ""
    data: dict[str, Any] | None = None


def _json_dumps(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def _json_loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


class OrgTaskManager:
    """Process-local manager for org tasks persisted in the team DB."""

    def __init__(
        self,
        *,
        db: TeamDatabase,
        organization_id: str,
        messager: Messager | None = None,
        session_id: str | None = None,
    ) -> None:
        self.db = db
        self.organization_id = organization_id
        self.messager = messager
        self.session_id = session_id
        self._sessions: DbSessions | None = None

    async def initialize(self) -> None:
        await self.db.initialize()
        if self.db.session_local is None or self.db.engine is None:
            raise RuntimeError("TeamDatabase is not initialized")
        if self._sessions is None:
            self._sessions = DbSessions(self.db.session_local)
        async with self.db.engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)

    async def ensure_organization(
        self,
        *,
        display_name: str | None = None,
        description: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> OrganizationSpec:
        await self.initialize()
        now = get_current_time()
        async with self._write() as session:
            row = await session.get(OrgInfoRecord, self.organization_id)
            if row is None:
                row = OrgInfoRecord(
                    organization_id=self.organization_id,
                    display_name=display_name,
                    description=description,
                    metadata_json=_json_dumps(metadata or {}),
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
            else:
                row.display_name = display_name if display_name is not None else row.display_name
                row.description = description if description is not None else row.description
                if metadata is not None:
                    row.metadata_json = _json_dumps(metadata)
                row.updated_at = now
            await session.commit()
            return OrganizationSpec(
                organization_id=row.organization_id,
                display_name=row.display_name,
                description=row.description,
                metadata=_json_loads(row.metadata_json, {}),
            )

    async def register_leader(
        self,
        *,
        team_id: str,
        leader_id: str,
        leader_member_name: str | None = None,
        capabilities: list[str] | None = None,
    ) -> OrgLeaderHandle:
        await self.initialize()
        await self.ensure_organization()
        now = get_current_time()
        key = (self.organization_id, team_id, leader_id)
        async with self._write() as session:
            row = await session.get(OrgLeaderRecord, key)
            if row is None:
                row = OrgLeaderRecord(
                    organization_id=self.organization_id,
                    team_id=team_id,
                    leader_id=leader_id,
                    leader_member_name=leader_member_name,
                    capabilities_json=_json_dumps(capabilities or []),
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
            else:
                row.leader_member_name = leader_member_name if leader_member_name is not None else row.leader_member_name
                if capabilities is not None:
                    row.capabilities_json = _json_dumps(capabilities)
                row.updated_at = now
            await session.commit()
            return OrgLeaderHandle(
                organization_id=row.organization_id,
                team_id=row.team_id,
                leader_id=row.leader_id,
                leader_member_name=row.leader_member_name,
                capabilities=_json_loads(row.capabilities_json, []),
            )

    async def create_task(
        self,
        *,
        title: str,
        description: str,
        created_by: OrgTaskCreator,
        task_id: str | None = None,
        parent_task_id: str | None = None,
        root_task_id: str | None = None,
        task_type: str | None = None,
        required_capabilities: list[str] | None = None,
        output_spec: OrgTaskOutputSpec | dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        delegated_to_team_id: str | None = None,
        delegated_to_leader_id: str | None = None,
    ) -> OrgTaskOpResult:
        await self.initialize()
        task_id = task_id or f"org-task-{uuid.uuid4().hex[:12]}"
        root_task_id = root_task_id or parent_task_id or task_id
        now = get_current_time()
        assignment_type = OrgAssignmentType.DELEGATED if delegated_to_team_id else OrgAssignmentType.UNASSIGNED
        status = OrgTaskStatus.DELEGATED if delegated_to_team_id else OrgTaskStatus.OPEN
        spec_model = self._coerce_output_spec(output_spec)
        async with self._write() as session:
            if await session.get(OrgTaskRecord, task_id) is not None:
                return OrgTaskOpResult(ok=False, reason=f"org task already exists: {task_id}")
            row = OrgTaskRecord(
                task_id=task_id,
                organization_id=self.organization_id,
                parent_task_id=parent_task_id,
                root_task_id=root_task_id,
                creator_type=created_by.creator_type,
                creator_id=created_by.creator_id,
                creator_team_id=created_by.team_id,
                status=status.value,
                created_at=now,
                updated_at=now,
                title=title,
                description=description,
                task_type=task_type,
                required_capabilities_json=_json_dumps(required_capabilities or []),
                assignment_type=assignment_type.value,
                assigned_team_id=delegated_to_team_id,
                assigned_leader_id=delegated_to_leader_id,
                assigned_by_team_id=created_by.team_id if delegated_to_team_id else None,
                assigned_at=now if delegated_to_team_id else None,
                output_spec_json=_json_dumps(spec_model.model_dump() if spec_model else None),
                metadata_json=_json_dumps(metadata or {}),
            )
            session.add(row)
            await session.commit()
        task = self._to_task(row)
        await self._publish_task_created(task)
        if delegated_to_team_id:
            await self._publish_task_delegated(task, created_by.team_id or "", delegated_to_team_id, delegated_to_leader_id)
        return OrgTaskOpResult(ok=True, task=task)

    async def get_task(self, task_id: str) -> OrgTask | None:
        await self.initialize()
        async with self._read() as session:
            row = await session.get(OrgTaskRecord, task_id)
            if row is None or row.organization_id != self.organization_id:
                return None
            return self._to_task(row)

    async def list_tasks(
        self,
        *,
        status: str | OrgTaskStatus | None = None,
        assigned_team_id: str | None = None,
        limit: int = 50,
    ) -> list[OrgTask]:
        await self.initialize()
        stmt = select(OrgTaskRecord).where(OrgTaskRecord.organization_id == self.organization_id)
        if status:
            stmt = stmt.where(OrgTaskRecord.status == str(status))
        if assigned_team_id:
            stmt = stmt.where(OrgTaskRecord.assigned_team_id == assigned_team_id)
        stmt = stmt.order_by(OrgTaskRecord.updated_at.desc()).limit(limit)
        async with self._read() as session:
            rows = (await session.execute(stmt)).scalars().all()
            return [self._to_task(row) for row in rows]

    async def list_open_tasks(self, *, limit: int = 50) -> list[OrgTask]:
        return await self.list_tasks(status=OrgTaskStatus.OPEN, limit=limit)

    async def list_tasks_for_team(self, team_id: str, *, include_open: bool = True, limit: int = 50) -> list[OrgTask]:
        await self.initialize()
        stmt = select(OrgTaskRecord).where(OrgTaskRecord.organization_id == self.organization_id)
        if include_open:
            stmt = stmt.where((OrgTaskRecord.assigned_team_id == team_id) | (OrgTaskRecord.status == OrgTaskStatus.OPEN.value))
        else:
            stmt = stmt.where(OrgTaskRecord.assigned_team_id == team_id)
        stmt = stmt.order_by(OrgTaskRecord.updated_at.desc()).limit(limit)
        async with self._read() as session:
            rows = (await session.execute(stmt)).scalars().all()
            return [self._to_task(row) for row in rows]

    async def claim_task(self, *, task_id: str, team_id: str, leader_id: str) -> OrgTaskOpResult:
        await self.initialize()
        now = get_current_time()
        async with self._write() as session:
            row = await session.get(OrgTaskRecord, task_id)
            if row is None or row.organization_id != self.organization_id:
                return OrgTaskOpResult(ok=False, reason=f"org task not found: {task_id}")
            if row.status != OrgTaskStatus.OPEN.value or row.assignment_type != OrgAssignmentType.UNASSIGNED.value:
                return OrgTaskOpResult(ok=False, reason=f"task is not open/unassigned: {task_id}")
            row.status = OrgTaskStatus.CLAIMED.value
            row.assignment_type = OrgAssignmentType.CLAIMED.value
            row.assigned_team_id = team_id
            row.assigned_leader_id = leader_id
            row.assigned_by_team_id = None
            row.assigned_at = now
            row.updated_at = now
            await session.commit()
        task = self._to_task(row)
        await self._publish_event(
            OrgTaskClaimedEvent(
                organization_id=self.organization_id,
                team_id=team_id,
                leader_id=leader_id,
                task_id=task_id,
                claimed_by_team_id=team_id,
                claimed_by_leader_id=leader_id,
            )
        )
        return OrgTaskOpResult(ok=True, task=task)

    async def delegate_task(
        self,
        *,
        task_id: str,
        from_team_id: str,
        to_team_id: str,
        to_leader_id: str | None = None,
    ) -> OrgTaskOpResult:
        await self.initialize()
        now = get_current_time()
        async with self._write() as session:
            row = await session.get(OrgTaskRecord, task_id)
            if row is None or row.organization_id != self.organization_id:
                return OrgTaskOpResult(ok=False, reason=f"org task not found: {task_id}")
            if row.status in {OrgTaskStatus.COMPLETED.value, OrgTaskStatus.CANCELLED.value, OrgTaskStatus.EXPIRED.value}:
                return OrgTaskOpResult(ok=False, reason=f"task is terminal: {task_id}")
            if row.assigned_team_id and row.assigned_team_id != from_team_id:
                return OrgTaskOpResult(ok=False, reason=f"task is assigned to another team: {row.assigned_team_id}")
            row.status = OrgTaskStatus.DELEGATED.value
            row.assignment_type = OrgAssignmentType.DELEGATED.value
            row.assigned_team_id = to_team_id
            row.assigned_leader_id = to_leader_id
            row.assigned_by_team_id = from_team_id
            row.assigned_at = now
            row.updated_at = now
            await session.commit()
        task = self._to_task(row)
        await self._publish_task_delegated(task, from_team_id, to_team_id, to_leader_id)
        return OrgTaskOpResult(ok=True, task=task)

    async def start_task(self, *, task_id: str, team_id: str) -> OrgTaskOpResult:
        return await self._set_assigned_task_status(task_id, team_id, OrgTaskStatus.IN_PROGRESS)

    async def complete_task(
        self,
        *,
        task_id: str,
        team_id: str,
        output_context: OrgTaskOutputContext | dict[str, Any] | None = None,
        output_abstract: str | None = None,
    ) -> OrgTaskOpResult:
        await self.initialize()
        now = get_current_time()
        context_model = self._coerce_output_context(output_context)
        async with self._write() as session:
            row = await session.get(OrgTaskRecord, task_id)
            if row is None or row.organization_id != self.organization_id:
                return OrgTaskOpResult(ok=False, reason=f"org task not found: {task_id}")
            if row.assigned_team_id != team_id:
                return OrgTaskOpResult(ok=False, reason=f"task is not assigned to team: {team_id}")
            if row.status in {OrgTaskStatus.COMPLETED.value, OrgTaskStatus.CANCELLED.value, OrgTaskStatus.EXPIRED.value}:
                return OrgTaskOpResult(ok=False, reason=f"task is terminal: {task_id}")
            row.status = OrgTaskStatus.COMPLETED.value
            if context_model is not None:
                row.output_context_json = _json_dumps(context_model.model_dump())
            row.output_abstract = output_abstract if output_abstract is not None else row.output_abstract
            row.updated_at = now
            await session.commit()
        task = self._to_task(row)
        await self._publish_event(
            OrgTaskCompletedEvent(
                organization_id=self.organization_id,
                team_id=team_id,
                leader_id=row.assigned_leader_id,
                task_id=task_id,
            )
        )
        return OrgTaskOpResult(ok=True, task=task)

    async def send_leader_message(
        self,
        *,
        from_team_id: str,
        from_leader_id: str,
        content: str,
        to_team_id: str | None = None,
        to_leader_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> OrgTaskOpResult:
        await self.initialize()
        message_id = f"org-msg-{uuid.uuid4().hex[:12]}"
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
                metadata_json=_json_dumps(metadata or {}),
            )
            session.add(row)
            await session.commit()
        await self._publish_event(
            OrgLeaderMessageEvent(
                organization_id=self.organization_id,
                team_id=from_team_id,
                leader_id=from_leader_id,
                message_id=message_id,
                from_team_id=from_team_id,
                to_team_id=to_team_id,
            ),
            team_inbox_id=to_team_id,
        )
        return OrgTaskOpResult(ok=True, data=self._message_dict(row))

    async def list_leader_messages(
        self,
        *,
        team_id: str,
        include_broadcast: bool = True,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        await self.initialize()
        stmt = select(OrgLeaderMessageRecord).where(OrgLeaderMessageRecord.organization_id == self.organization_id)
        if include_broadcast:
            stmt = stmt.where(
                (OrgLeaderMessageRecord.to_team_id == team_id) | (OrgLeaderMessageRecord.to_team_id.is_(None))
            )
        else:
            stmt = stmt.where(OrgLeaderMessageRecord.to_team_id == team_id)
        stmt = stmt.order_by(OrgLeaderMessageRecord.created_at.desc()).limit(limit)
        async with self._read() as session:
            rows = (await session.execute(stmt)).scalars().all()
            return [self._message_dict(row) for row in rows]

    async def _set_assigned_task_status(
        self,
        task_id: str,
        team_id: str,
        status: OrgTaskStatus,
    ) -> OrgTaskOpResult:
        await self.initialize()
        now = get_current_time()
        async with self._write() as session:
            row = await session.get(OrgTaskRecord, task_id)
            if row is None or row.organization_id != self.organization_id:
                return OrgTaskOpResult(ok=False, reason=f"org task not found: {task_id}")
            if row.assigned_team_id != team_id:
                return OrgTaskOpResult(ok=False, reason=f"task is not assigned to team: {team_id}")
            row.status = status.value
            row.updated_at = now
            await session.commit()
        return OrgTaskOpResult(ok=True, task=self._to_task(row))

    async def _publish_task_created(self, task: OrgTask) -> None:
        await self._publish_event(
            OrgTaskCreatedEvent(
                organization_id=self.organization_id,
                team_id=task.created_by.team_id,
                leader_id=task.created_by.creator_id if task.created_by.creator_type == "team_leader" else None,
                task_id=task.task_id,
                parent_task_id=task.parent_task_id,
                root_task_id=task.root_task_id,
            )
        )

    async def _publish_task_delegated(
        self,
        task: OrgTask,
        from_team_id: str,
        to_team_id: str,
        to_leader_id: str | None,
    ) -> None:
        await self._publish_event(
            OrgTaskDelegatedEvent(
                organization_id=self.organization_id,
                team_id=from_team_id,
                task_id=task.task_id,
                delegated_by_team_id=from_team_id,
                delegated_to_team_id=to_team_id,
                delegated_to_leader_id=to_leader_id,
            ),
            team_inbox_id=to_team_id,
        )

    async def _publish_event(self, event: BaseOrgEvent, *, team_inbox_id: str | None = None) -> None:
        if self.messager is None:
            return
        session_id = self.session_id or get_session_id()
        if not session_id:
            return
        message = OrgEventMessage.from_event(event)
        await self.messager.publish(OrgTopic.ORG.build(session_id, self.organization_id), message)
        if isinstance(event, (OrgTaskCreatedEvent, OrgTaskClaimedEvent, OrgTaskDelegatedEvent, OrgTaskCompletedEvent)):
            await self.messager.publish(OrgTopic.TASK.build(session_id, self.organization_id), message)
        if isinstance(event, OrgLeaderMessageEvent):
            await self.messager.publish(OrgTopic.LEADER.build(session_id, self.organization_id), message)
        if team_inbox_id:
            await self.messager.publish(OrgTopic.TEAM_INBOX.build(session_id, self.organization_id, team_inbox_id), message)

    def _to_task(self, row: OrgTaskRecord) -> OrgTask:
        output_spec = _json_loads(row.output_spec_json, None)
        output_context = _json_loads(row.output_context_json, None)
        return OrgTask(
            task_id=row.task_id,
            parent_task_id=row.parent_task_id,
            root_task_id=row.root_task_id,
            created_by=OrgTaskCreator(
                creator_type=row.creator_type,
                creator_id=row.creator_id,
                organization_id=row.organization_id,
                team_id=row.creator_team_id,
            ),
            status=OrgTaskStatus(row.status),
            created_at=row.created_at,
            updated_at=row.updated_at,
            title=row.title,
            description=row.description,
            task_type=row.task_type,
            required_capabilities=_json_loads(row.required_capabilities_json, []),
            assignment=OrgAssignment(
                assignment_type=OrgAssignmentType(row.assignment_type),
                team_id=row.assigned_team_id,
                leader_id=row.assigned_leader_id,
                assigned_by_team_id=row.assigned_by_team_id,
                assigned_at=row.assigned_at,
            ),
            output_spec=OrgTaskOutputSpec.model_validate(output_spec) if output_spec else None,
            output_context=OrgTaskOutputContext.model_validate(output_context) if output_context else None,
            output_abstract=row.output_abstract,
            metadata=_json_loads(row.metadata_json, {}),
        )

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
            "metadata": _json_loads(row.metadata_json, {}),
        }

    @staticmethod
    def _coerce_output_spec(value: OrgTaskOutputSpec | dict[str, Any] | None) -> OrgTaskOutputSpec | None:
        if value is None:
            return None
        if isinstance(value, OrgTaskOutputSpec):
            return value
        return OrgTaskOutputSpec.model_validate(value)

    @staticmethod
    def _coerce_output_context(value: OrgTaskOutputContext | dict[str, Any] | None) -> OrgTaskOutputContext | None:
        if value is None:
            return None
        if isinstance(value, OrgTaskOutputContext):
            return value
        return OrgTaskOutputContext.model_validate(value)

    def _read(self):
        if self._sessions is None:
            raise RuntimeError("OrgTaskManager is not initialized")
        return self._sessions.read()

    def _write(self):
        if self._sessions is None:
            raise RuntimeError("OrgTaskManager is not initialized")
        return self._sessions.write()


__all__ = ["OrgTaskManager", "OrgTaskOpResult"]

# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""DB-backed organization task pool."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, or_, select, update

from openjiuwen.agent_teams.context import get_session_id
from openjiuwen.agent_teams.messager import Messager
from openjiuwen.agent_teams.organization.db import (
    ensure_org_schema,
    ensure_org_static_tables,
    json_dumps,
    json_loads,
)
from openjiuwen.agent_teams.organization.events import (
    BaseOrgEvent,
    OrgEventMessage,
    OrgLeaderMessageEvent,
    OrgSummarySourcesUpdatedEvent,
    OrgSummaryTaskCreatedEvent,
    OrgTaskClaimedEvent,
    OrgTaskCompletedEvent,
    OrgTaskCreatedEvent,
    OrgTaskDelegatedEvent,
    OrgTaskReviewedEvent,
    OrgTaskReviewRequestedEvent,
    OrgTopic,
)
from openjiuwen.agent_teams.organization.schema import (
    OrgAssignment,
    OrgAssignmentType,
    OrgInfoRecord,
    OrgLeaderHandle,
    OrgLeaderRecord,
    OrganizationSpec,
    OrgTask,
    OrgTaskCreator,
    OrgTaskEventRecord,
    OrgTaskOutputContext,
    OrgTaskOutputSpec,
    OrgTaskRecord,
    OrgTaskReview,
    OrgTaskReviewRecord,
    OrgTaskReviewStatus,
    OrgTaskSource,
    OrgTaskSourceRecord,
    OrgTaskStatus,
)
from openjiuwen.agent_teams.tools.database import TeamDatabase
from openjiuwen.agent_teams.tools.database.engine import DbSessions, get_current_time


logger = logging.getLogger(__name__)


@dataclass
class OrgTaskOpResult:
    ok: bool
    task: OrgTask | None = None
    reason: str = ""
    data: dict[str, Any] | None = None


# Local aliases keep call sites stable while sharing helpers with message_service.
_json_dumps = json_dumps
_json_loads = json_loads
_ensure_org_schema = ensure_org_schema


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
            await conn.run_sync(ensure_org_static_tables)

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
                owner_team_id=_json_loads(row.metadata_json, {}).get("owner_team_id"),
                owner_leader_id=_json_loads(row.metadata_json, {}).get("owner_leader_id"),
                metadata=_json_loads(row.metadata_json, {}),
            )

    async def get_organization(self) -> OrganizationSpec | None:
        """Return the persisted organization and its registered leaders."""

        await self.initialize()
        async with self._read() as session:
            row = await session.get(OrgInfoRecord, self.organization_id)
            if row is None:
                return None
            stmt = select(OrgLeaderRecord).where(OrgLeaderRecord.organization_id == self.organization_id)
            leaders = (await session.execute(stmt)).scalars().all()
            metadata = _json_loads(row.metadata_json, {})
            return OrganizationSpec(
                organization_id=row.organization_id,
                display_name=row.display_name,
                description=row.description,
                owner_team_id=metadata.get("owner_team_id"),
                owner_leader_id=metadata.get("owner_leader_id"),
                leaders=[
                    OrgLeaderHandle(
                        organization_id=leader.organization_id,
                        team_id=leader.team_id,
                        leader_id=leader.leader_id,
                        leader_member_name=leader.leader_member_name,
                        capabilities=_json_loads(leader.capabilities_json, []),
                    )
                    for leader in leaders
                ],
                metadata=metadata,
            )

    @classmethod
    async def find_organization_ids_for_team(cls, db: "TeamDatabase", team_id: str) -> list[str]:
        """Return persisted organizations that contain ``team_id``.

        The process-local organization runtime is intentionally ephemeral.  A
        cold-recovered team therefore needs a DB lookup to recover its
        organization binding before its leader tool set is assembled.
        """

        await _ensure_org_schema(db)
        if db.session_local is None:
            raise RuntimeError("TeamDatabase is not initialized")
        sessions = DbSessions(db.session_local)
        async with sessions.read() as session:
            stmt = select(OrgLeaderRecord.organization_id).where(OrgLeaderRecord.team_id == team_id)
            return list((await session.execute(stmt)).scalars().all())

    async def dissolve_organization(self) -> dict[str, int]:
        """Delete every persisted row owned by this organization."""

        await self.initialize()
        async with self._write() as session:
            task_ids = list(
                (await session.execute(
                    select(OrgTaskRecord.task_id).where(
                        OrgTaskRecord.organization_id == self.organization_id
                    )
                )).scalars().all()
            )

            counts: dict[str, int] = {}

            async def _delete(statement: Any, name: str) -> None:
                result = await session.execute(statement)
                counts[name] = max(result.rowcount or 0, 0)

            if task_ids:
                await _delete(
                    delete(OrgTaskSourceRecord).where(
                        or_(
                            OrgTaskSourceRecord.summary_task_id.in_(task_ids),
                            OrgTaskSourceRecord.source_task_id.in_(task_ids),
                        )
                    ),
                    "task_sources",
                )
                await _delete(
                    delete(OrgTaskReviewRecord).where(OrgTaskReviewRecord.task_id.in_(task_ids)),
                    "task_reviews",
                )
            else:
                counts["task_sources"] = 0
                counts["task_reviews"] = 0

            await _delete(
                delete(OrgTaskEventRecord).where(
                    OrgTaskEventRecord.organization_id == self.organization_id
                ),
                "task_events",
            )
            await _delete(
                delete(OrgTaskRecord).where(OrgTaskRecord.organization_id == self.organization_id),
                "tasks",
            )
            await _delete(
                delete(OrgLeaderRecord).where(OrgLeaderRecord.organization_id == self.organization_id),
                "leaders",
            )
            await _delete(
                delete(OrgInfoRecord).where(OrgInfoRecord.organization_id == self.organization_id),
                "organization",
            )
            await session.commit()
            return counts

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
                if leader_member_name is not None:
                    row.leader_member_name = leader_member_name
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
        capabilities = required_capabilities or []
        if not capabilities or any(
            not isinstance(capability, str) or not capability.strip()
            for capability in capabilities
        ):
            return OrgTaskOpResult(
                ok=False,
                reason="required_capabilities must contain at least one non-empty capability",
            )
        capabilities = list(dict.fromkeys(capability.strip() for capability in capabilities))
        task_id = task_id or f"org-task-{uuid.uuid4().hex[:12]}"
        now = get_current_time()
        assignment_type = OrgAssignmentType.DELEGATED if delegated_to_team_id else OrgAssignmentType.UNASSIGNED
        status = OrgTaskStatus.DELEGATED if delegated_to_team_id else OrgTaskStatus.OPEN
        spec_model = self._coerce_output_spec(output_spec)
        async with self._write() as session:
            if parent_task_id:
                parent = await session.get(OrgTaskRecord, parent_task_id)
                if parent is None or parent.organization_id != self.organization_id:
                    return OrgTaskOpResult(ok=False, reason=f"parent task not found: {parent_task_id}")
                if root_task_id is None:
                    root_task_id = parent.root_task_id
            else:
                root_task_id = root_task_id or task_id
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
                required_capabilities_json=_json_dumps(capabilities),
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
            await self._publish_task_delegated(
                task,
                created_by.team_id or "",
                delegated_to_team_id,
                delegated_to_leader_id,
            )
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
            stmt = stmt.where(
                (OrgTaskRecord.assigned_team_id == team_id)
                | (OrgTaskRecord.status == OrgTaskStatus.OPEN.value)
            )
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
            result = await session.execute(
                update(OrgTaskRecord)
                .where(
                    OrgTaskRecord.task_id == task_id,
                    OrgTaskRecord.organization_id == self.organization_id,
                    OrgTaskRecord.status == OrgTaskStatus.OPEN.value,
                    OrgTaskRecord.assignment_type == OrgAssignmentType.UNASSIGNED.value,
                )
                .values(
                    status=OrgTaskStatus.CLAIMED.value,
                    assignment_type=OrgAssignmentType.CLAIMED.value,
                    assigned_team_id=team_id,
                    assigned_leader_id=leader_id,
                    assigned_by_team_id=None,
                    assigned_at=now,
                    updated_at=now,
                )
            )
            await session.commit()
            if result.rowcount != 1:
                return OrgTaskOpResult(ok=False, reason=f"task is not open/unassigned: {task_id}")
            row = await session.get(OrgTaskRecord, task_id)
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
            result = await session.execute(
                update(OrgTaskRecord)
                .where(
                    OrgTaskRecord.task_id == task_id,
                    OrgTaskRecord.organization_id == self.organization_id,
                    OrgTaskRecord.status.not_in({
                        OrgTaskStatus.COMPLETED.value,
                        OrgTaskStatus.CANCELLED.value,
                        OrgTaskStatus.EXPIRED.value,
                    }),
                    or_(
                        OrgTaskRecord.assigned_team_id.is_(None),
                        OrgTaskRecord.assigned_team_id == from_team_id,
                    ),
                )
                .values(
                    status=OrgTaskStatus.DELEGATED.value,
                    assignment_type=OrgAssignmentType.DELEGATED.value,
                    assigned_team_id=to_team_id,
                    assigned_leader_id=to_leader_id,
                    assigned_by_team_id=from_team_id,
                    assigned_at=now,
                    updated_at=now,
                )
            )
            await session.commit()
            if result.rowcount != 1:
                row = await session.get(OrgTaskRecord, task_id)
                if row is None or row.organization_id != self.organization_id:
                    return OrgTaskOpResult(ok=False, reason=f"org task not found: {task_id}")
                if row.status in {
                    OrgTaskStatus.COMPLETED.value,
                    OrgTaskStatus.CANCELLED.value,
                    OrgTaskStatus.EXPIRED.value,
                }:
                    return OrgTaskOpResult(ok=False, reason=f"task is terminal: {task_id}")
                if row.assigned_team_id and row.assigned_team_id != from_team_id:
                    return OrgTaskOpResult(
                        ok=False,
                        reason=f"task is assigned to another team: {row.assigned_team_id}",
                    )
                return OrgTaskOpResult(ok=False, reason=f"task delegate failed: {task_id}")
            row = await session.get(OrgTaskRecord, task_id)
        task = self._to_task(row)
        await self._publish_task_delegated(task, from_team_id, to_team_id, to_leader_id)
        return OrgTaskOpResult(ok=True, task=task)

    async def start_task(self, *, task_id: str, team_id: str) -> OrgTaskOpResult:
        await self.initialize()
        now = get_current_time()
        async with self._write() as session:
            row = await session.get(OrgTaskRecord, task_id)
            if row is None or row.organization_id != self.organization_id:
                return OrgTaskOpResult(ok=False, reason=f"org task not found: {task_id}")
            if row.assigned_team_id != team_id:
                return OrgTaskOpResult(ok=False, reason=f"task is not assigned to team: {team_id}")
            if row.status in {
                OrgTaskStatus.COMPLETED.value,
                OrgTaskStatus.CANCELLED.value,
                OrgTaskStatus.EXPIRED.value,
            }:
                return OrgTaskOpResult(ok=False, reason=f"task is terminal: {task_id}")
            if row.status == OrgTaskStatus.IN_PROGRESS.value:
                return OrgTaskOpResult(ok=True, task=self._to_task(row))
            if row.status not in {
                OrgTaskStatus.CLAIMED.value,
                OrgTaskStatus.DELEGATED.value,
            }:
                return OrgTaskOpResult(ok=False, reason=f"task cannot be started from status {row.status}: {task_id}")
            row.status = OrgTaskStatus.IN_PROGRESS.value
            row.updated_at = now
            await session.commit()
        return OrgTaskOpResult(ok=True, task=self._to_task(row))

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
            if row.status in {
                OrgTaskStatus.COMPLETED.value,
                OrgTaskStatus.CANCELLED.value,
                OrgTaskStatus.EXPIRED.value,
            }:
                return OrgTaskOpResult(ok=False, reason=f"task is terminal: {task_id}")
            child_stmt = select(OrgTaskRecord).where(
                OrgTaskRecord.organization_id == self.organization_id,
                OrgTaskRecord.parent_task_id == task_id,
                OrgTaskRecord.creator_team_id == team_id,
            )
            child_rows = (await session.execute(child_stmt)).scalars().all()
            for child in child_rows:
                if child.status != OrgTaskStatus.COMPLETED.value:
                    return OrgTaskOpResult(ok=False, reason=f"child task is not completed: {child.task_id}")
                child_review = await self._get_latest_review_row(session, child.task_id)
                if child_review is None or child_review.review_status != OrgTaskReviewStatus.ACCEPTED.value:
                    return OrgTaskOpResult(ok=False, reason=f"child task review is not accepted: {child.task_id}")
            row.status = OrgTaskStatus.COMPLETED.value
            if context_model is not None:
                row.output_context_json = _json_dumps(context_model.model_dump())
            row.output_abstract = output_abstract if output_abstract is not None else row.output_abstract
            row.updated_at = now
            review_event: OrgTaskReviewRequestedEvent | None = None
            if row.parent_task_id and row.creator_team_id:
                review = await self._get_latest_review_row(session, row.task_id)
                if review is None:
                    review = OrgTaskReviewRecord(
                        review_id=f"org-review-{uuid.uuid4().hex[:12]}",
                        task_id=row.task_id,
                        reviewer_team_id=row.creator_team_id,
                        review_status=OrgTaskReviewStatus.PENDING.value,
                        required_changes_json=_json_dumps([]),
                        created_at=now,
                        updated_at=now,
                    )
                    session.add(review)
                else:
                    review.reviewer_team_id = row.creator_team_id
                    review.review_status = OrgTaskReviewStatus.PENDING.value
                    review.updated_at = now
                review_event = OrgTaskReviewRequestedEvent(
                    organization_id=self.organization_id,
                    team_id=row.creator_team_id,
                    task_id=row.task_id,
                    parent_task_id=row.parent_task_id,
                    reviewer_team_id=row.creator_team_id,
                )
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
        if review_event is not None:
            await self._publish_event(review_event)
        return OrgTaskOpResult(ok=True, task=task)

    async def list_child_tasks(self, *, parent_task_id: str, creator_team_id: str | None = None) -> list[OrgTask]:
        await self.initialize()
        stmt = select(OrgTaskRecord).where(
            OrgTaskRecord.organization_id == self.organization_id,
            OrgTaskRecord.parent_task_id == parent_task_id,
        )
        if creator_team_id:
            stmt = stmt.where(OrgTaskRecord.creator_team_id == creator_team_id)
        stmt = stmt.order_by(OrgTaskRecord.created_at.asc())
        async with self._read() as session:
            rows = (await session.execute(stmt)).scalars().all()
            return [self._to_task(row) for row in rows]

    async def list_pending_reviews(self, *, team_id: str, limit: int = 50) -> list[dict[str, Any]]:
        await self.initialize()
        stmt = (
            select(OrgTaskReviewRecord)
            .where(
                OrgTaskReviewRecord.reviewer_team_id == team_id,
                OrgTaskReviewRecord.review_status == OrgTaskReviewStatus.PENDING.value,
            )
            .order_by(OrgTaskReviewRecord.updated_at.desc())
            .limit(limit)
        )
        async with self._read() as session:
            rows = (await session.execute(stmt)).scalars().all()
            results = []
            for row in rows:
                task_row = await session.get(OrgTaskRecord, row.task_id)
                if task_row is None or task_row.organization_id != self.organization_id:
                    continue
                results.append(
                    {
                        "review": self._to_review(row).model_dump(),
                        "task": self._to_task(task_row).brief(),
                    }
                )
            return results

    async def get_task_review(self, task_id: str) -> OrgTaskReview | None:
        await self.initialize()
        async with self._read() as session:
            task_row = await session.get(OrgTaskRecord, task_id)
            if task_row is None or task_row.organization_id != self.organization_id:
                return None
            row = await self._get_latest_review_row(session, task_id)
            return self._to_review(row) if row is not None else None

    async def review_task(
        self,
        *,
        task_id: str,
        reviewer_team_id: str,
        review_status: OrgTaskReviewStatus | str,
        verdict: str | None = None,
        required_changes: list[str] | None = None,
    ) -> OrgTaskOpResult:
        await self.initialize()
        now = get_current_time()
        status = OrgTaskReviewStatus(str(review_status))
        async with self._write() as session:
            task_row = await session.get(OrgTaskRecord, task_id)
            if task_row is None or task_row.organization_id != self.organization_id:
                return OrgTaskOpResult(ok=False, reason=f"org task not found: {task_id}")
            if task_row.creator_team_id != reviewer_team_id:
                return OrgTaskOpResult(ok=False, reason="only the task creator team can review this task")
            if task_row.status != OrgTaskStatus.COMPLETED.value:
                return OrgTaskOpResult(ok=False, reason=f"task is not completed: {task_id}")
            row = await self._get_latest_review_row(session, task_id)
            if row is None:
                row = OrgTaskReviewRecord(
                    review_id=f"org-review-{uuid.uuid4().hex[:12]}",
                    task_id=task_id,
                    reviewer_team_id=reviewer_team_id,
                    review_status=status.value,
                    verdict=verdict,
                    required_changes_json=_json_dumps(required_changes or []),
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
            else:
                row.reviewer_team_id = reviewer_team_id
                row.review_status = status.value
                row.verdict = verdict
                row.required_changes_json = _json_dumps(required_changes or [])
                row.updated_at = now
            await session.commit()
        review = self._to_review(row)
        await self._publish_event(
            OrgTaskReviewedEvent(
                organization_id=self.organization_id,
                team_id=reviewer_team_id,
                task_id=task_id,
                review_id=review.review_id,
                review_status=review.review_status.value,
            )
        )
        return OrgTaskOpResult(ok=True, task=self._to_task(task_row), data={"review": review.model_dump()})

    async def can_complete_parent_task(self, *, parent_task_id: str, team_id: str) -> bool:
        await self.initialize()
        async with self._read() as session:
            stmt = select(OrgTaskRecord).where(
                OrgTaskRecord.organization_id == self.organization_id,
                OrgTaskRecord.parent_task_id == parent_task_id,
                OrgTaskRecord.creator_team_id == team_id,
            )
            child_rows = (await session.execute(stmt)).scalars().all()
            for child in child_rows:
                if child.status != OrgTaskStatus.COMPLETED.value:
                    return False
                review = await self._get_latest_review_row(session, child.task_id)
                if review is None or review.review_status != OrgTaskReviewStatus.ACCEPTED.value:
                    return False
            return True

    async def create_summary_task(
        self,
        *,
        title: str,
        description: str,
        created_by: OrgTaskCreator,
        task_id: str | None = None,
        source_task_ids: list[str] | None = None,
        output_spec: OrgTaskOutputSpec | dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> OrgTaskOpResult:
        result = await self.create_task(
            task_id=task_id,
            title=title,
            description=description,
            task_type="organization.summary",
            required_capabilities=["summary"],
            output_spec=output_spec,
            metadata=metadata,
            created_by=created_by,
        )
        if not result.ok or result.task is None:
            return result
        if source_task_ids:
            attach = await self.attach_summary_sources(
                summary_task_id=result.task.task_id,
                source_task_ids=source_task_ids,
            )
            if not attach.ok:
                return attach
        await self._publish_event(
            OrgSummaryTaskCreatedEvent(
                organization_id=self.organization_id,
                team_id=created_by.team_id,
                leader_id=created_by.creator_id if created_by.creator_type == "team_leader" else None,
                summary_task_id=result.task.task_id,
            )
        )
        return result

    async def attach_summary_sources(
        self,
        *,
        summary_task_id: str,
        source_task_ids: list[str],
        source_role: str | None = None,
        required: bool = True,
    ) -> OrgTaskOpResult:
        await self.initialize()
        now = get_current_time()
        async with self._write() as session:
            summary = await session.get(OrgTaskRecord, summary_task_id)
            if summary is None or summary.organization_id != self.organization_id:
                return OrgTaskOpResult(ok=False, reason=f"summary task not found: {summary_task_id}")
            if summary.task_type != "organization.summary":
                return OrgTaskOpResult(ok=False, reason=f"task is not a summary task: {summary_task_id}")
            for source_task_id in source_task_ids:
                source = await session.get(OrgTaskRecord, source_task_id)
                if source is None or source.organization_id != self.organization_id:
                    return OrgTaskOpResult(ok=False, reason=f"source task not found: {source_task_id}")
                if source.status != OrgTaskStatus.COMPLETED.value:
                    return OrgTaskOpResult(ok=False, reason=f"source task is not completed: {source_task_id}")
                review = await self._get_latest_review_row(session, source_task_id)
                if review is not None and review.review_status != OrgTaskReviewStatus.ACCEPTED.value:
                    return OrgTaskOpResult(ok=False, reason=f"source task review is not accepted: {source_task_id}")
                existing = await session.get(OrgTaskSourceRecord, (summary_task_id, source_task_id))
                if existing is None:
                    session.add(
                        OrgTaskSourceRecord(
                            summary_task_id=summary_task_id,
                            source_task_id=source_task_id,
                            source_role=source_role,
                            required=required,
                            created_at=now,
                        )
                    )
                else:
                    existing.source_role = source_role
                    existing.required = required
            await session.commit()
        await self._publish_event(
            OrgSummarySourcesUpdatedEvent(
                organization_id=self.organization_id,
                summary_task_id=summary_task_id,
            )
        )
        return OrgTaskOpResult(
            ok=True,
            task=await self.get_task(summary_task_id),
            data={"source_task_ids": source_task_ids},
        )

    async def list_summary_sources(self, *, summary_task_id: str) -> list[OrgTaskSource]:
        await self.initialize()
        async with self._read() as session:
            summary = await session.get(OrgTaskRecord, summary_task_id)
            if summary is None or summary.organization_id != self.organization_id:
                return []
            stmt = select(OrgTaskSourceRecord).where(OrgTaskSourceRecord.summary_task_id == summary_task_id)
            rows = (await session.execute(stmt)).scalars().all()
            return [self._to_source(row) for row in rows]

    async def get_summary_inputs(self, *, summary_task_id: str) -> dict[str, Any] | None:
        await self.initialize()
        async with self._read() as session:
            summary = await session.get(OrgTaskRecord, summary_task_id)
            if summary is None or summary.organization_id != self.organization_id:
                return None
            stmt = select(OrgTaskSourceRecord).where(OrgTaskSourceRecord.summary_task_id == summary_task_id)
            source_rows = (await session.execute(stmt)).scalars().all()
            sources = []
            for source_row in source_rows:
                task_row = await session.get(OrgTaskRecord, source_row.source_task_id)
                if task_row is None or task_row.organization_id != self.organization_id:
                    continue
                review_row = await self._get_latest_review_row(session, task_row.task_id)
                sources.append(
                    {
                        "source": self._to_source(source_row).model_dump(),
                        "task": self._to_task(task_row).model_dump(),
                        "review": self._to_review(review_row).model_dump() if review_row is not None else None,
                    }
                )
            return {"summary_task": self._to_task(summary).model_dump(), "source_tasks": sources}

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
        message = OrgEventMessage.from_event(event)
        # Keep a compact durable activity trail for the web UI and for
        # post-run inspection.  Transport delivery remains best effort.
        try:
            async with self._write() as session:
                session.add(
                    OrgTaskEventRecord(
                        event_id=f"org-event-{uuid.uuid4().hex[:12]}",
                        organization_id=self.organization_id,
                        event_type=message.event_type,
                        task_id=message.payload.get("task_id"),
                        team_id=message.payload.get("team_id"),
                        leader_id=message.payload.get("leader_id"),
                        payload_json=_json_dumps(message.payload),
                        created_at=get_current_time(),
                    )
                )
                await session.commit()
        except Exception:  # noqa: BLE001
            logger.warning("Failed to persist organization activity event", exc_info=True)

        if self.messager is None:
            return
        session_id = self.session_id or get_session_id()
        if not session_id:
            return
        try:
            await self.messager.publish(OrgTopic.ORG.build(session_id, self.organization_id), message)
            if isinstance(
                event,
                (
                    OrgTaskCreatedEvent,
                    OrgTaskClaimedEvent,
                    OrgTaskDelegatedEvent,
                    OrgTaskCompletedEvent,
                    OrgTaskReviewRequestedEvent,
                    OrgTaskReviewedEvent,
                    OrgSummaryTaskCreatedEvent,
                    OrgSummarySourcesUpdatedEvent,
                ),
            ):
                await self.messager.publish(OrgTopic.TASK.build(session_id, self.organization_id), message)
            if isinstance(event, OrgLeaderMessageEvent):
                await self.messager.publish(OrgTopic.LEADER.build(session_id, self.organization_id), message)
            # Leader inbox delivery is owned by TransportAPI.deliver; skip duplicate TEAM_INBOX publish.
            if team_inbox_id and not isinstance(event, OrgLeaderMessageEvent):
                await self.messager.publish(
                    OrgTopic.TEAM_INBOX.build(session_id, self.organization_id, team_inbox_id),
                    message,
                )
        except Exception:  # noqa: BLE001
            logger.warning(
                "Failed to publish organization event event_type=%s task_id=%s organization_id=%s",
                message.event_type,
                message.payload.get("task_id"),
                self.organization_id,
                exc_info=True,
            )

    async def publish_event(self, event: BaseOrgEvent, *, team_inbox_id: str | None = None) -> None:
        """Publish an organization lifecycle event through the configured transport."""

        await self._publish_event(event, team_inbox_id=team_inbox_id)

    @staticmethod
    def _to_task(row: OrgTaskRecord) -> OrgTask:
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
    def _to_review(row: OrgTaskReviewRecord) -> OrgTaskReview:
        return OrgTaskReview(
            review_id=row.review_id,
            task_id=row.task_id,
            reviewer_team_id=row.reviewer_team_id,
            review_status=OrgTaskReviewStatus(row.review_status),
            verdict=row.verdict,
            required_changes=_json_loads(row.required_changes_json, []),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _to_source(row: OrgTaskSourceRecord) -> OrgTaskSource:
        return OrgTaskSource(
            summary_task_id=row.summary_task_id,
            source_task_id=row.source_task_id,
            source_role=row.source_role,
            required=row.required,
            created_at=row.created_at,
        )

    @staticmethod
    async def _get_latest_review_row(session: Any, task_id: str) -> OrgTaskReviewRecord | None:
        stmt = (
            select(OrgTaskReviewRecord)
            .where(OrgTaskReviewRecord.task_id == task_id)
            .order_by(OrgTaskReviewRecord.updated_at.desc())
            .limit(1)
        )
        return (await session.execute(stmt)).scalars().first()

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

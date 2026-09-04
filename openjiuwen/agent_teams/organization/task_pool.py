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
    OrgDbContext,
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
    OrgTaskFailedEvent,
    OrgTaskReviewedEvent,
    OrgTaskReviewRequestedEvent,
    OrgTopic,
)
from openjiuwen.agent_teams.organization.schema import (
    ORG_TASK_LEGACY_STATUS_FAILURE_CODES,
    ORG_TASK_REPAIRS_TASK_ID_KEY,
    ORG_TASK_RETRY_COUNT_KEY,
    ORG_TASK_RETRY_LIMIT_KEY,
    ORG_TASK_TERMINAL_STATUS_VALUES,
    OrgAssignment,
    OrgAssignmentType,
    OrgInfoRecord,
    OrgLeaderHandle,
    OrgLeaderRecord,
    OrganizationSpec,
    OrgTask,
    OrgTaskAggregationConfig,
    OrgTaskAggregationMode,
    OrgTaskCreator,
    OrgTaskEventRecord,
    OrgTaskFailureCode,
    OrgTaskOutputContext,
    OrgTaskOutputSpec,
    OrgTaskRecord,
    OrgTaskReview,
    OrgTaskReviewRecord,
    OrgTaskReviewStatus,
    OrgTaskSource,
    OrgTaskSourceRecord,
    OrgTaskStatus,
    default_root_aggregation,
)
from openjiuwen.agent_teams.tools.database import TeamDatabase
from openjiuwen.agent_teams.tools.database.engine import get_current_time


logger = logging.getLogger(__name__)

_FAILABLE_TASK_STATUSES = frozenset({
    OrgTaskStatus.CLAIMED.value,
    OrgTaskStatus.DELEGATED.value,
    OrgTaskStatus.IN_PROGRESS.value,
})

# Rejected / needs-revision children may be superseded by an accepted repair sibling.
_SUPERSEDEABLE_REVIEW_STATUSES = frozenset({
    OrgTaskReviewStatus.REJECTED.value,
    OrgTaskReviewStatus.NEEDS_REVISION.value,
})


@dataclass
class OrgTaskOpResult:
    ok: bool
    task: OrgTask | None = None
    reason: str = ""
    data: dict[str, Any] | None = None


# Local aliases keep call sites stable while sharing helpers with message_service.
_json_dumps = json_dumps
_json_loads = json_loads


class OrgTaskManager:
    """Process-local manager for org tasks persisted in the team DB."""

    def __init__(
        self,
        *,
        db: TeamDatabase,
        organization_id: str,
        messager: Messager | None = None,
        session_id: str | None = None,
        db_context: OrgDbContext | None = None,
    ) -> None:
        self.db = db
        self.organization_id = organization_id
        self.messager = messager
        self.session_id = session_id
        self.db_context = db_context or OrgDbContext(db)

    async def initialize(self) -> None:
        await self.db_context.initialize()

    def _read(self):
        return self.db_context.sessions.read()

    def _write(self):
        return self.db_context.sessions.write()

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

        context = OrgDbContext(db)
        sessions = await context.initialize()
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
        repairs_task_id: str | None = None,
        delegated_to_team_id: str | None = None,
        delegated_to_leader_id: str | None = None,
        aggregation_mode: OrgTaskAggregationMode | str | None = None,
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
        task_metadata = dict(metadata or {})
        # Single entry: only the repairs_task_id param establishes the link.
        task_metadata.pop(ORG_TASK_REPAIRS_TASK_ID_KEY, None)
        repairs_target: str | None = None
        if repairs_task_id is not None:
            if not isinstance(repairs_task_id, str) or not repairs_task_id.strip():
                return OrgTaskOpResult(ok=False, reason="repairs_task_id must be a non-empty string")
            repairs_target = repairs_task_id.strip()
            if not parent_task_id:
                return OrgTaskOpResult(
                    ok=False,
                    reason="repairs_task_id requires parent_task_id (repair must be a sibling child)",
                )
            if repairs_target == task_id:
                return OrgTaskOpResult(ok=False, reason="repairs_task_id cannot reference the new task itself")
            task_metadata[ORG_TASK_REPAIRS_TASK_ID_KEY] = repairs_target
        if parent_task_id and aggregation_mode is not None:
            return OrgTaskOpResult(ok=False, reason="aggregation_mode is only allowed on root tasks")
        if aggregation_mode is not None:
            try:
                mode = OrgTaskAggregationMode(aggregation_mode)
            except ValueError:
                return OrgTaskOpResult(ok=False, reason=f"invalid aggregation_mode: {aggregation_mode!r}")
            if mode is OrgTaskAggregationMode.SUMMARY_TEAM:
                return OrgTaskOpResult(
                    ok=False,
                    reason="SUMMARY_TEAM aggregation is not supported yet",
                )
            if mode is not OrgTaskAggregationMode.HIERARCHICAL:
                return OrgTaskOpResult(ok=False, reason=f"invalid aggregation_mode: {aggregation_mode!r}")
        assignment_type = OrgAssignmentType.DELEGATED if delegated_to_team_id else OrgAssignmentType.UNASSIGNED
        status = OrgTaskStatus.DELEGATED if delegated_to_team_id else OrgTaskStatus.OPEN
        spec_model = self._coerce_output_spec(output_spec)
        async with self._write() as session:
            if parent_task_id:
                parent = await session.get(OrgTaskRecord, parent_task_id)
                if parent is None or parent.organization_id != self.organization_id:
                    return OrgTaskOpResult(ok=False, reason=f"parent task not found: {parent_task_id}")
                expected_root = parent.root_task_id
                if root_task_id is not None and root_task_id != expected_root:
                    return OrgTaskOpResult(
                        ok=False,
                        reason=(
                            f"root_task_id must match parent.root_task_id ({expected_root!r}); "
                            f"got {root_task_id!r}"
                        ),
                    )
                root_task_id = expected_root
            else:
                if root_task_id is not None and root_task_id != task_id:
                    return OrgTaskOpResult(
                        ok=False,
                        reason=f"root task root_task_id must equal task_id ({task_id!r}); got {root_task_id!r}",
                    )
                root_task_id = task_id
            if repairs_target is not None:
                repaired = await session.get(OrgTaskRecord, repairs_target)
                if repaired is None or repaired.organization_id != self.organization_id:
                    return OrgTaskOpResult(
                        ok=False,
                        reason=f"repairs_task_id target not found: {repairs_target}",
                    )
                if repaired.parent_task_id != parent_task_id:
                    return OrgTaskOpResult(
                        ok=False,
                        reason=(
                            "repairs_task_id target must share the same parent_task_id "
                            f"({parent_task_id!r}); got {repaired.parent_task_id!r}"
                        ),
                    )
                retry_gate = await self._apply_repair_retry_budget(
                    session,
                    repaired=repaired,
                    parent_task_id=parent_task_id,
                    repairs_target=repairs_target,
                    now=now,
                )
                if retry_gate is not None:
                    return retry_gate
            if await session.get(OrgTaskRecord, task_id) is not None:
                return OrgTaskOpResult(ok=False, reason=f"org task already exists: {task_id}")
            aggregation_json = None
            if parent_task_id is None:
                aggregation_json = _json_dumps(default_root_aggregation(task_id).model_dump())
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
                aggregation_json=aggregation_json,
                output_spec_json=_json_dumps(spec_model.model_dump() if spec_model else None),
                metadata_json=_json_dumps(task_metadata),
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
                    OrgTaskRecord.status.not_in(ORG_TASK_TERMINAL_STATUS_VALUES),
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
                if row.status in ORG_TASK_TERMINAL_STATUS_VALUES:
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
            if row.status in ORG_TASK_TERMINAL_STATUS_VALUES:
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
            if row.status in ORG_TASK_TERMINAL_STATUS_VALUES:
                return OrgTaskOpResult(ok=False, reason=f"task is terminal: {task_id}")
            child_stmt = select(OrgTaskRecord).where(
                OrgTaskRecord.organization_id == self.organization_id,
                OrgTaskRecord.parent_task_id == task_id,
                OrgTaskRecord.creator_team_id == team_id,
            )
            child_rows = (await session.execute(child_stmt)).scalars().all()
            blocked_reason = await self._parent_complete_blocked_reason(session, child_rows)
            if blocked_reason is not None:
                return OrgTaskOpResult(ok=False, reason=blocked_reason)
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

    async def fail_task(
        self,
        *,
        task_id: str,
        team_id: str,
        failure_code: OrgTaskFailureCode | str,
        failure_reason: str,
        output_context: OrgTaskOutputContext | dict[str, Any] | None = None,
    ) -> OrgTaskOpResult:
        await self.initialize()
        reason = (failure_reason or "").strip()
        if not reason:
            return OrgTaskOpResult(ok=False, reason="failure_reason is required")
        try:
            code = OrgTaskFailureCode(failure_code)
        except ValueError:
            return OrgTaskOpResult(ok=False, reason=f"invalid failure_code: {failure_code!r}")

        now = get_current_time()
        context_model = self._coerce_output_context(output_context)
        async with self._write() as session:
            row = await session.get(OrgTaskRecord, task_id)
            if row is None or row.organization_id != self.organization_id:
                return OrgTaskOpResult(ok=False, reason=f"org task not found: {task_id}")
            if row.assigned_team_id != team_id:
                return OrgTaskOpResult(ok=False, reason=f"task is not assigned to team: {team_id}")
            if row.status in ORG_TASK_TERMINAL_STATUS_VALUES:
                return OrgTaskOpResult(ok=False, reason=f"task is terminal: {task_id}")
            if row.status not in _FAILABLE_TASK_STATUSES:
                return OrgTaskOpResult(
                    ok=False,
                    reason=f"task cannot be failed from status {row.status}: {task_id}",
                )
            row.status = OrgTaskStatus.FAILED.value
            row.failure_code = code.value
            row.failure_reason = reason
            row.failed_at = now
            row.updated_at = now
            if context_model is not None:
                row.output_context_json = _json_dumps(context_model.model_dump())
            await session.commit()
        task = self._to_task(row)
        await self._publish_event(
            OrgTaskFailedEvent(
                organization_id=self.organization_id,
                team_id=team_id,
                leader_id=row.assigned_leader_id,
                task_id=task_id,
                failure_code=code.value,
                failure_reason=reason,
            )
        )
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

    async def list_child_task_views(
        self,
        *,
        parent_task_id: str,
        creator_team_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Direct children with brief + latest review summary (for org_view_child_tasks)."""
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
            views: list[dict[str, Any]] = []
            for row in rows:
                task = self._to_task(row)
                payload = task.brief()
                review_row = await self._get_latest_review_row(session, row.task_id)
                if review_row is None:
                    payload["review"] = None
                else:
                    review = self._to_review(review_row)
                    verdict = review.verdict
                    if isinstance(verdict, str) and len(verdict) > 200:
                        verdict = verdict[:200]
                    payload["review"] = {
                        "review_id": review.review_id,
                        "review_status": review.review_status.value,
                        "verdict": verdict,
                        "required_changes": list(review.required_changes or [])[:10],
                        "updated_at": review.updated_at,
                    }
                repairs_target = task.metadata.get(ORG_TASK_REPAIRS_TASK_ID_KEY)
                if isinstance(repairs_target, str) and repairs_target.strip():
                    payload["repairs_task_id"] = repairs_target.strip()
                views.append(payload)
            return views

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
            return await self._parent_complete_blocked_reason(session, child_rows) is None

    async def _parent_complete_blocked_reason(
        self,
        session: Any,
        child_rows: list[OrgTaskRecord],
    ) -> str | None:
        """Return a block reason, or None when every direct child is accepted or one-level superseded."""
        if not child_rows:
            return None

        reviews: dict[str, OrgTaskReviewRecord | None] = {}
        for child in child_rows:
            reviews[child.task_id] = await self._get_latest_review_row(session, child.task_id)

        repairs_of: dict[str, list[OrgTaskRecord]] = {}
        for child in child_rows:
            meta = _json_loads(child.metadata_json, {})
            target = meta.get(ORG_TASK_REPAIRS_TASK_ID_KEY)
            if isinstance(target, str) and target.strip():
                repairs_of.setdefault(target.strip(), []).append(child)

        def _is_accepted(child: OrgTaskRecord) -> bool:
            if child.status != OrgTaskStatus.COMPLETED.value:
                return False
            review = reviews.get(child.task_id)
            return review is not None and review.review_status == OrgTaskReviewStatus.ACCEPTED.value

        def _is_supersedable(child: OrgTaskRecord) -> bool:
            if child.status == OrgTaskStatus.FAILED.value:
                return True
            if child.status != OrgTaskStatus.COMPLETED.value:
                return False
            review = reviews.get(child.task_id)
            return review is not None and review.review_status in _SUPERSEDEABLE_REVIEW_STATUSES

        for child in child_rows:
            if _is_accepted(child):
                continue
            if _is_supersedable(child) and any(
                _is_accepted(repair) for repair in repairs_of.get(child.task_id, ())
            ):
                continue
            if _is_supersedable(child):
                return f"child task is not superseded by an accepted repair: {child.task_id}"
            if child.status != OrgTaskStatus.COMPLETED.value:
                return f"child task is not completed: {child.task_id}"
            return f"child task review is not accepted: {child.task_id}"
        return None

    async def _apply_repair_retry_budget(
        self,
        session: Any,
        *,
        repaired: OrgTaskRecord,
        parent_task_id: str,
        repairs_target: str,
        now: int,
    ) -> OrgTaskOpResult | None:
        """Enforce optional retry_limit on the repaired task; bump retry_count. None = ok."""
        repaired_meta = _json_loads(repaired.metadata_json, {})
        raw_limit = repaired_meta.get(ORG_TASK_RETRY_LIMIT_KEY)
        retry_limit: int | None = None
        if raw_limit is not None:
            try:
                retry_limit = int(raw_limit)
            except (TypeError, ValueError):
                return OrgTaskOpResult(
                    ok=False,
                    reason=f"invalid retry_limit on repaired task {repairs_target!r}: {raw_limit!r}",
                )
            if retry_limit < 0:
                return OrgTaskOpResult(
                    ok=False,
                    reason=f"invalid retry_limit on repaired task {repairs_target!r}: {raw_limit!r}",
                )

        sibling_rows = (
            await session.execute(
                select(OrgTaskRecord).where(
                    OrgTaskRecord.organization_id == self.organization_id,
                    OrgTaskRecord.parent_task_id == parent_task_id,
                )
            )
        ).scalars().all()
        existing = 0
        for sibling in sibling_rows:
            meta = _json_loads(sibling.metadata_json, {})
            target = meta.get(ORG_TASK_REPAIRS_TASK_ID_KEY)
            if isinstance(target, str) and target.strip() == repairs_target:
                existing += 1

        if retry_limit is not None and existing >= retry_limit:
            return OrgTaskOpResult(
                ok=False,
                reason=(
                    f"retry_limit reached for repaired task {repairs_target}: "
                    f"{existing}/{retry_limit}"
                ),
            )

        repaired_meta[ORG_TASK_RETRY_COUNT_KEY] = existing + 1
        repaired.metadata_json = _json_dumps(repaired_meta)
        repaired.updated_at = now
        return None

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
                    OrgTaskFailedEvent,
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
        aggregation_payload = _json_loads(row.aggregation_json, None)
        status, failure_code = OrgTaskManager._task_status_from_row(row)
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
            status=status,
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
            aggregation=OrgTaskAggregationConfig.model_validate(aggregation_payload)
            if aggregation_payload
            else None,
            output_spec=OrgTaskOutputSpec.model_validate(output_spec) if output_spec else None,
            output_context=OrgTaskOutputContext.model_validate(output_context) if output_context else None,
            output_abstract=row.output_abstract,
            failure_code=failure_code,
            failure_reason=row.failure_reason,
            failed_at=row.failed_at,
            metadata=_json_loads(row.metadata_json, {}),
        )

    @staticmethod
    def _task_status_from_row(row: OrgTaskRecord) -> tuple[OrgTaskStatus, OrgTaskFailureCode | None]:
        legacy_failure = ORG_TASK_LEGACY_STATUS_FAILURE_CODES.get(row.status)
        if legacy_failure is not None:
            return OrgTaskStatus.FAILED, legacy_failure

        failure_code: OrgTaskFailureCode | None = None
        if row.failure_code:
            try:
                failure_code = OrgTaskFailureCode(row.failure_code)
            except ValueError:
                logger.warning(
                    "Unknown org task failure_code=%r task_id=%s; treating as None",
                    row.failure_code,
                    row.task_id,
                )

        try:
            return OrgTaskStatus(row.status), failure_code
        except ValueError:
            logger.warning(
                "Unknown org task status=%r task_id=%s; degrading to FAILED",
                row.status,
                row.task_id,
            )
            return OrgTaskStatus.FAILED, failure_code

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


__all__ = ["OrgTaskManager", "OrgTaskOpResult"]

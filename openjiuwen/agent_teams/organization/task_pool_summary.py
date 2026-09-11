# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Summary-task and SummaryExecution helpers mixed into :class:`OrgTaskManager`."""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openjiuwen.agent_teams.organization.task_pool import OrgTaskOpResult

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from openjiuwen.agent_teams.organization.db import json_dumps as _json_dumps, json_loads as _json_loads
from openjiuwen.agent_teams.organization.events import (
    OrgSummarySourcesUpdatedEvent,
    OrgSummaryTaskCreatedEvent,
    OrgTaskFailedEvent,
)
from openjiuwen.agent_teams.organization.schema import (
    ORG_SUMMARY_CAPABILITY,
    ORG_SUMMARY_TASK_TYPE,
    ORG_TASK_TERMINAL_STATUS_VALUES,
    OrgAssignmentType,
    OrgSummaryExecution,
    OrgSummaryExecutionRecord,
    OrgSummaryExecutionStatus,
    OrgTaskAggregationConfig,
    OrgTaskAggregationMode,
    OrgTaskCreator,
    OrgTaskFailureCode,
    OrgTaskOutputSpec,
    OrgTaskRecord,
    OrgTaskReviewStatus,
    OrgTaskSource,
    OrgTaskSourceRecord,
    OrgTaskStatus,
)
from openjiuwen.agent_teams.tools.database.engine import get_current_time

logger = logging.getLogger(__name__)

TERMINAL_SUMMARY_EXECUTION_STATUSES = frozenset(
    {
        OrgSummaryExecutionStatus.COMPLETED.value,
        OrgSummaryExecutionStatus.FAILED.value,
        OrgSummaryExecutionStatus.RELEASED.value,
    }
)


def _op_result(**kwargs: Any):
    """Lazy import avoids an import cycle with :mod:`task_pool`."""
    from openjiuwen.agent_teams.organization.task_pool import OrgTaskOpResult

    return OrgTaskOpResult(**kwargs)


class OrgTaskSummaryMixin:
    """SUMMARY_TEAM / Summary Task / SummaryExecution APIs for the task pool."""

    async def bind_root_summary_team(
        self,
        *,
        root_task_id: str,
        summary_team_id: str,
    ) -> OrgTaskOpResult:
        """Write the provisioned Summary Team id back into a SUMMARY_TEAM root's aggregation.

        ``_complete_summary_provision`` delegates the Summary Task to the dynamic
        team but never lands the team id on the root task's
        ``OrgTaskAggregationConfig.summary_team_id``, so ``org_view_tasks`` on the
        root shows ``summary_team_id=None`` even though the Summary Task is
        already assigned.  Call this after delegation to keep the two views in
        sync (design doc §4.4.1).
        """
        await self.initialize()
        async with self._write() as session:
            row = await session.get(OrgTaskRecord, root_task_id)
            if row is None or row.organization_id != self.organization_id:
                return _op_result(ok=False, reason=f"org task not found: {root_task_id}")
            aggregation_payload = _json_loads(row.aggregation_json, None)
            if aggregation_payload is None:
                return _op_result(ok=False, reason=f"root task has no aggregation: {root_task_id}")
            aggregation = OrgTaskAggregationConfig.model_validate(aggregation_payload)
            if aggregation.mode is not OrgTaskAggregationMode.SUMMARY_TEAM:
                return _op_result(
                    ok=False,
                    reason=f"root task is not SUMMARY_TEAM: {root_task_id}",
                )
            if aggregation.summary_team_id == summary_team_id:
                return _op_result(ok=True, task=self._to_task(row))
            aggregation.summary_team_id = summary_team_id
            row.aggregation_json = _json_dumps(aggregation.model_dump())
            row.updated_at = get_current_time()
            await session.commit()
            return _op_result(ok=True, task=self._to_task(row))

    async def fail_summary_task(
        self,
        *,
        summary_task_id: str,
        failure_reason: str,
    ) -> OrgTaskOpResult:
        """Mark a Summary Task FAILED with SUMMARY_PROVISION_FAILED (§4.4.3).

        Unlike :meth:`fail_task`, this internal path skips the assignment guard:
        provisioning can fail *before* the Summary Task is delegated, so
        ``assigned_team_id`` may still be None here.  The failure code is written
        onto the task row so ``org_view_tasks`` surfaces ``failure_code`` rather
        than an inscrutable WAITING_SOURCES/DELEGATED state.
        """
        await self.initialize()
        reason = (failure_reason or "").strip() or "summary team provision failed"
        now = get_current_time()
        async with self._write() as session:
            row = await session.get(OrgTaskRecord, summary_task_id)
            if row is None or row.organization_id != self.organization_id:
                return _op_result(ok=False, reason=f"org task not found: {summary_task_id}")
            if row.task_type != ORG_SUMMARY_TASK_TYPE:
                return _op_result(
                    ok=False,
                    reason=f"task is not a summary task: {summary_task_id}",
                )
            if row.status in ORG_TASK_TERMINAL_STATUS_VALUES:
                return _op_result(ok=False, reason=f"task is terminal: {summary_task_id}")
            row.status = OrgTaskStatus.FAILED.value
            row.failure_code = OrgTaskFailureCode.SUMMARY_PROVISION_FAILED.value
            row.failure_reason = reason
            row.failed_at = now
            row.updated_at = now
            await session.commit()
        task = self._to_task(row)
        await self._publish_event(
            OrgTaskFailedEvent(
                organization_id=self.organization_id,
                team_id=row.creator_team_id or "",
                task_id=summary_task_id,
                failure_code=OrgTaskFailureCode.SUMMARY_PROVISION_FAILED.value,
                failure_reason=reason,
            )
        )
        return _op_result(ok=True, task=task)

    async def _final_output_incomplete_reason(
        self,
        session: Any,
        row: OrgTaskRecord,
    ) -> str | None:
        """Block completing a root whose aggregation final output is not yet done.

        ``HIERARCHICAL`` sets ``final_output_task_id`` to the root itself — no
        extra gate. ``SUMMARY_TEAM`` points it at the framework Summary Task,
        which is not a ``parent_task_id`` child, so the ordinary child-tree check
        never sees it (§4.4.4).
        """
        aggregation_payload = _json_loads(row.aggregation_json, None)
        if not isinstance(aggregation_payload, dict):
            return None
        final_id = aggregation_payload.get("final_output_task_id")
        if not isinstance(final_id, str) or not final_id.strip():
            return None
        if final_id == row.task_id:
            return None
        final_row = await session.get(OrgTaskRecord, final_id)
        if final_row is None or final_row.organization_id != self.organization_id:
            return f"final output task not found: {final_id}"
        if final_row.status != OrgTaskStatus.COMPLETED.value:
            return f"final output task is not completed: {final_id}"
        return None

    async def _find_any_summary_task(self, session: Any) -> OrgTaskRecord | None:
        """Return the organization's live Summary Task, if one exists.

        Used by the standalone summary-creation path: a Summary Task is created
        once and outlives the root that requested it, so "already present" means
        the organization's aggregation slot is taken.
        """
        rows = (
            await session.execute(
                select(OrgTaskRecord).where(
                    OrgTaskRecord.organization_id == self.organization_id,
                    OrgTaskRecord.task_type == ORG_SUMMARY_TASK_TYPE,
                    OrgTaskRecord.status.not_in(ORG_TASK_TERMINAL_STATUS_VALUES),
                )
            )
        ).scalars().all()
        return rows[0] if rows else None

    async def _find_active_summary_root(self, session: Any) -> OrgTaskRecord | None:
        """Return the organization's live SUMMARY_TEAM root, if one exists.

        The aggregation mode lives inside ``aggregation_json`` rather than in a
        column, so the rows are read and filtered here (same approach as
        ``_is_summary_team_for_parent_guard``).  Framework-owned Summary Tasks are
        excluded: they also have ``parent_task_id IS NULL``, but they are the
        aggregation target, not a root that owns a team -- and they outlive the
        root that created them.  Terminal roots are ignored too: a completed or
        failed aggregation has released its team and no longer blocks a new one.
        """
        rows = (
            await session.execute(
                select(OrgTaskRecord).where(
                    OrgTaskRecord.organization_id == self.organization_id,
                    OrgTaskRecord.parent_task_id.is_(None),
                    OrgTaskRecord.status.not_in(ORG_TASK_TERMINAL_STATUS_VALUES),
                )
            )
        ).scalars().all()
        for row in rows:
            if row.task_type == ORG_SUMMARY_TASK_TYPE:
                continue
            config = _json_loads(row.aggregation_json, {}) or {}
            if config.get("mode") == OrgTaskAggregationMode.SUMMARY_TEAM.value:
                return row
        return None

    async def _is_summary_team_for_parent_guard(
        self, session: Any, parent: OrgTaskRecord, team_id: str
    ) -> bool:
        """Allow a SUMMARY_TEAM root's dynamic Summary Team to create supplementary children.

        A Summary Task is created with ``parent_task_id=None``, so it lives outside
        the regular child-tree whose children only the root's assigned team may
        create.  The on-demand Summary Team must still be able to spawn the focused
        supplementary tasks it needs to complete the aggregation; guard that by
        requiring the creator to be the team currently assigned that root's summary
        task.
        """
        if parent.parent_task_id:
            return False
        config = _json_loads(parent.aggregation_json, {}) or {}
        mode = config.get("mode")
        if mode != OrgTaskAggregationMode.SUMMARY_TEAM.value:
            return False
        summary_task_id = config.get("summary_task_id")
        if not summary_task_id:
            return False
        summary = await session.get(OrgTaskRecord, summary_task_id)
        if summary is None or summary.organization_id != self.organization_id:
            return False
        return summary.assigned_team_id == team_id

    async def create_summary_task(
        self,
        *,
        title: str,
        description: str,
        created_by: OrgTaskCreator,
        task_id: str | None = None,
        root_task_id: str | None = None,
        source_task_ids: list[str] | None = None,
        output_spec: OrgTaskOutputSpec | dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> OrgTaskOpResult:
        task_id = task_id or f"org-task-{uuid.uuid4().hex[:12]}"
        # a Summary Task is framework-owned and independent of the child tree:
        # parent_task_id stays None, root_task_id points at the organizational
        # root whose SUMMARY_TEAM result this task produces.
        result = await self._build_summary_task(
            task_id=task_id,
            title=title,
            description=description,
            created_by=created_by,
            root_task_id=root_task_id or task_id,
            source_task_ids=source_task_ids,
            output_spec=output_spec,
            metadata=metadata,
        )
        if not result.ok or result.task is None:
            return result
        await self._publish_event(
            OrgSummaryTaskCreatedEvent(
                organization_id=self.organization_id,
                team_id=created_by.team_id,
                leader_id=created_by.creator_id if created_by.creator_type == "team_leader" else None,
                summary_task_id=result.task.task_id,
            )
        )
        return result

    async def _build_summary_task(
        self,
        *,
        task_id: str,
        title: str,
        description: str,
        created_by: OrgTaskCreator,
        root_task_id: str,
        source_task_ids: list[str] | None,
        output_spec: OrgTaskOutputSpec | dict[str, Any] | None,
        metadata: dict[str, Any] | None,
    ) -> OrgTaskOpResult:
        """Create the framework-owned Summary Task row (WAITING_SOURCES, SUMMARY_TEAM)."""
        await self.initialize()
        now = get_current_time()
        async with self._write() as session:
            # Second guard for the "one Summary Team per organization" rule: this
            # path is reached directly by org_create_summary_task, which would
            # otherwise let a leader bypass the check in create_task.  Writes hold
            # a process-wide lock, so the check and the insert below cannot be
            # interleaved by another writer.
            existing_summary = await self._find_any_summary_task(session)
            if existing_summary is not None:
                return _op_result(
                    ok=False,
                    reason=(
                        "organization already has a summary task "
                        f"({existing_summary.task_id}); complete or fail the current summary "
                        "aggregation before creating another"
                    ),
                )
            if await session.get(OrgTaskRecord, task_id) is not None:
                return _op_result(ok=False, reason=f"org task already exists: {task_id}")
            row = await self._insert_summary_task_row(
                session,
                task_id=task_id,
                title=title,
                description=description,
                created_by=created_by,
                root_task_id=root_task_id,
                output_spec=output_spec,
                metadata=metadata or {},
                now=now,
            )
            if source_task_ids:
                attached = await self._attach_summary_sources(
                    session,
                    summary_task_id=task_id,
                    source_task_ids=source_task_ids,
                    organization_id=self.organization_id,
                    now=now,
                )
                if attached is not None:
                    return attached
            await session.commit()
            task = self._to_task(row)
        return _op_result(ok=True, task=task)

    async def _insert_summary_task_row(
        self,
        session: Any,
        *,
        task_id: str,
        title: str,
        description: str,
        created_by: OrgTaskCreator,
        root_task_id: str,
        output_spec: OrgTaskOutputSpec | dict[str, Any] | None,
        metadata: dict[str, Any],
        now: int,
    ) -> OrgTaskRecord:
        """Insert the framework-owned Summary Task row inside an open write session.

        Shared by ``_build_summary_task`` (standalone path) and ``create_task``
        (auto-created for a SUMMARY_TEAM root), so both stay on one row shape.
        Source binding is handled by the caller so it can return a validation
        error without leaving a partially-created row.
        """
        spec_model = self._coerce_output_spec(output_spec)
        aggregation = OrgTaskAggregationConfig(
            mode=OrgTaskAggregationMode.SUMMARY_TEAM,
            summary_task_id=task_id,
            final_output_task_id=task_id,
        )
        row = OrgTaskRecord(
            task_id=task_id,
            organization_id=self.organization_id,
            parent_task_id=None,
            root_task_id=root_task_id,
            creator_type=created_by.creator_type,
            creator_id=created_by.creator_id,
            creator_team_id=created_by.team_id,
            status=OrgTaskStatus.WAITING_SOURCES.value,
            created_at=now,
            updated_at=now,
            title=title,
            description=description,
            task_type=ORG_SUMMARY_TASK_TYPE,
            required_capabilities_json=_json_dumps([ORG_SUMMARY_CAPABILITY]),
            assignment_type=OrgAssignmentType.UNASSIGNED.value,
            aggregation_json=_json_dumps(aggregation.model_dump()),
            output_spec_json=_json_dumps(spec_model.model_dump() if spec_model else None),
            metadata_json=_json_dumps(dict(metadata or {})),
        )
        session.add(row)
        return row

    @staticmethod

    async def _attach_summary_sources(
        session: Any,
        *,
        summary_task_id: str,
        source_task_ids: list[str],
        organization_id: str,
        now: int,
        source_role: str | None = None,
        required: bool = True,
    ) -> OrgTaskOpResult | None:
        """Bind source tasks inside an open write session (§4.4.3 early-bind).

        Sources may be bound before they finish; readiness / failure is decided
        later by :meth:`evaluate_summary_sources`. Returns None on success, or an
        OrgTaskOpResult error on the first missing source.
        """
        for source_task_id in source_task_ids:
            source = await session.get(OrgTaskRecord, source_task_id)
            if source is None or source.organization_id != organization_id:
                return _op_result(ok=False, reason=f"source task not found: {source_task_id}")
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
        return None

    async def attach_summary_sources(
        self,
        *,
        summary_task_id: str,
        source_task_ids: list[str],
        team_id: str,
        source_role: str | None = None,
        required: bool = True,
    ) -> OrgTaskOpResult:
        await self.initialize()
        now = get_current_time()
        async with self._write() as session:
            summary = await session.get(OrgTaskRecord, summary_task_id)
            if summary is None or summary.organization_id != self.organization_id:
                return _op_result(ok=False, reason=f"summary task not found: {summary_task_id}")
            if summary.task_type != ORG_SUMMARY_TASK_TYPE:
                return _op_result(ok=False, reason=f"task is not a summary task: {summary_task_id}")
            if summary.status in ORG_TASK_TERMINAL_STATUS_VALUES:
                # A finished summary already aggregated (or gave up on) its
                # sources.  Writing the binding anyway would leave the task
                # COMPLETED with sources it never read, and the wake-up would
                # be dropped because the execution is terminal too.
                return _op_result(
                    ok=False,
                    reason=f"summary task is terminal: {summary_task_id} (status={summary.status})",
                )
            # §6.2: only the Root Leader (root creator / assignee) may bind sources.
            root = await session.get(OrgTaskRecord, summary.root_task_id)
            if root is None or root.organization_id != self.organization_id:
                return _op_result(ok=False, reason=f"root task not found: {summary.root_task_id}")
            allowed = {t for t in (root.creator_team_id, root.assigned_team_id) if t}
            if not team_id or team_id not in allowed:
                return _op_result(
                    ok=False,
                    reason="only the root task's team can attach summary sources",
                )
            attached = await self._attach_summary_sources(
                session,
                summary_task_id=summary_task_id,
                source_task_ids=source_task_ids,
                organization_id=self.organization_id,
                now=now,
                source_role=source_role,
                required=required,
            )
            if attached is not None:
                return attached
            await session.commit()
        await self._publish_event(
            OrgSummarySourcesUpdatedEvent(
                organization_id=self.organization_id,
                summary_task_id=summary_task_id,
            )
        )
        return _op_result(
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

    async def evaluate_summary_sources(self, *, summary_task_id: str) -> dict[str, Any]:
        """Judge whether a Summary Task may start: every required source COMPLETED + ACCEPTED.

        Returns {"ready": bool, "source_failed": source_task_id | None, "reason": str}.
        ``source_failed`` is set only when a required source is missing or FAILED —
        an in-progress source is not ready and not a failure (§4.4.3).
        """
        await self.initialize()
        async with self._read() as session:
            summary = await session.get(OrgTaskRecord, summary_task_id)
            if summary is None or summary.organization_id != self.organization_id:
                return {"ready": False, "source_failed": None, "reason": f"summary task not found: {summary_task_id}"}
            if summary.task_type != ORG_SUMMARY_TASK_TYPE:
                return {
                    "ready": False,
                    "source_failed": None,
                    "reason": f"task is not a summary task: {summary_task_id}",
                }
            stmt = select(OrgTaskSourceRecord).where(OrgTaskSourceRecord.summary_task_id == summary_task_id)
            source_rows = (await session.execute(stmt)).scalars().all()
            for source_row in source_rows:
                source = await session.get(OrgTaskRecord, source_row.source_task_id)
                if source is None or source.organization_id != self.organization_id:
                    return {
                        "ready": False,
                        "source_failed": source_row.source_task_id,
                        "reason": f"source task not found: {source_row.source_task_id}",
                    }
                if not source_row.required:
                    continue
                if source.status == OrgTaskStatus.FAILED.value:
                    return {
                        "ready": False,
                        "source_failed": source_row.source_task_id,
                        "reason": f"source task failed: {source_row.source_task_id}",
                    }
                if source.status != OrgTaskStatus.COMPLETED.value:
                    return {
                        "ready": False,
                        "source_failed": None,
                        "reason": f"source task is not completed: {source_row.source_task_id}",
                    }
                review = await self._get_latest_review_row(session, source_row.source_task_id)
                if review is not None and review.review_status != OrgTaskReviewStatus.ACCEPTED.value:
                    return {
                        "ready": False,
                        "source_failed": None,
                        "reason": f"source task review is not accepted: {source_row.source_task_id}",
                    }
            if not source_rows:
                return {"ready": False, "source_failed": None, "reason": "no sources bound to summary task"}
            return {"ready": True, "source_failed": None, "reason": ""}

    async def _list_summary_task_ids_for_source(self, source_task_id: str) -> list[str]:
        """Return Summary Task ids that currently list ``source_task_id`` as a source."""
        async with self._read() as session:
            rows = (
                await session.execute(
                    select(OrgTaskSourceRecord.summary_task_id).where(
                        OrgTaskSourceRecord.source_task_id == source_task_id
                    )
                )
            ).scalars().all()
        return list(rows)

    async def _notify_bound_summary_sources(self, source_task_id: str) -> None:
        """Re-evaluate summaries that bound this task (complete / fail / review)."""
        for summary_task_id in await self._list_summary_task_ids_for_source(source_task_id):
            await self._publish_event(
                OrgSummarySourcesUpdatedEvent(
                    organization_id=self.organization_id,
                    summary_task_id=summary_task_id,
                )
            )

    async def create_summary_execution(
        self,
        *,
        root_task_id: str,
        summary_task_id: str,
        execution_id: str | None = None,
    ) -> OrgSummaryExecution:
        """Return the Summary Task's live SummaryExecution, creating one if needed.

        A Summary Task owns exactly one live execution, so this is idempotent: a
        duplicate ``OrgSummaryTaskCreatedEvent`` (delivery is at-least-once)
        reuses the existing PROVISIONING / WAITING_SOURCES / RUNNING row instead
        of spawning a second Summary Team for the same task.  A terminal row
        (COMPLETED / FAILED / RELEASED) does not block a fresh one -- a summary
        that finished, failed, or was released may legitimately run again.

        The pre-insert read below is only a fast path: two duplicate events can
        arrive concurrently on different coroutines, both find nothing, and both
        try to insert.  The partial unique index
        ``uq_org_summary_execution_live`` rejects the loser, which then reads
        back the winner's row -- that constraint, not this check, is what makes
        the one-live-execution invariant hold.
        """
        await self.initialize()
        existing = await self._find_live_summary_execution(summary_task_id)
        if existing is not None:
            logger.debug(
                "reusing live summary execution %s for task %s (status=%s)",
                existing.execution_id,
                summary_task_id,
                existing.status,
            )
            return existing

        execution_id = execution_id or f"summary-exec-{uuid.uuid4().hex[:12]}"
        now = get_current_time()
        record = OrgSummaryExecutionRecord(
            execution_id=execution_id,
            organization_id=self.organization_id,
            root_task_id=root_task_id,
            summary_task_id=summary_task_id,
            status=OrgSummaryExecutionStatus.PROVISIONING.value,
            created_at=now,
        )
        try:
            async with self._write() as session:
                session.add(record)
                await session.commit()
        except IntegrityError:
            # Lost the race against a concurrent duplicate event: adopt the row
            # that won instead of surfacing a spurious failure to the caller.
            winner = await self._find_live_summary_execution(summary_task_id)
            if winner is None:
                raise
            logger.debug(
                "concurrent create for task %s lost the race; adopting execution %s",
                summary_task_id,
                winner.execution_id,
            )
            return winner
        return self._to_summary_execution(record)

    async def _find_live_summary_execution(self, summary_task_id: str) -> OrgSummaryExecution | None:
        """Return the task's non-terminal execution, if one exists."""
        async with self._read() as session:
            row = (
                await session.execute(
                    select(OrgSummaryExecutionRecord).where(
                        OrgSummaryExecutionRecord.organization_id == self.organization_id,
                        OrgSummaryExecutionRecord.summary_task_id == summary_task_id,
                        OrgSummaryExecutionRecord.status.not_in(
                            TERMINAL_SUMMARY_EXECUTION_STATUSES
                        ),
                    )
                )
            ).scalars().first()
        return self._to_summary_execution(row) if row is not None else None

    async def list_summary_executions(
        self,
        *,
        root_task_id: str | None = None,
        summary_task_id: str | None = None,
    ) -> list[OrgSummaryExecution]:
        """List Summary Team instances, optionally filtered by root or summary task."""
        await self.initialize()
        stmt = select(OrgSummaryExecutionRecord).where(
            OrgSummaryExecutionRecord.organization_id == self.organization_id
        )
        if root_task_id is not None:
            stmt = stmt.where(OrgSummaryExecutionRecord.root_task_id == root_task_id)
        if summary_task_id is not None:
            stmt = stmt.where(OrgSummaryExecutionRecord.summary_task_id == summary_task_id)
        async with self._read() as session:
            rows = (await session.execute(stmt)).scalars().all()
            return [self._to_summary_execution(row) for row in rows]

    async def update_summary_execution(
        self,
        *,
        execution_id: str,
        status: OrgSummaryExecutionStatus,
        summary_team_id: str | None = None,
        released_at: int | None = None,
    ) -> OrgSummaryExecution | None:
        """Transition a SummaryExecution status (and optionally bind/release its team)."""
        await self.initialize()
        async with self._write() as session:
            row = await session.get(OrgSummaryExecutionRecord, execution_id)
            if row is None or row.organization_id != self.organization_id:
                return None
            row.status = status.value
            if summary_team_id is not None:
                row.summary_team_id = summary_team_id
            if released_at is not None:
                row.released_at = released_at
            await session.commit()
            return self._to_summary_execution(row)

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
    def _to_summary_execution(row: OrgSummaryExecutionRecord) -> OrgSummaryExecution:
        return OrgSummaryExecution(
            execution_id=row.execution_id,
            organization_id=row.organization_id,
            root_task_id=row.root_task_id,
            summary_task_id=row.summary_task_id,
            summary_team_id=row.summary_team_id,
            status=OrgSummaryExecutionStatus(row.status),
            created_at=row.created_at,
            released_at=row.released_at,
        )


__all__ = ["OrgTaskSummaryMixin", "TERMINAL_SUMMARY_EXECUTION_STATUSES"]

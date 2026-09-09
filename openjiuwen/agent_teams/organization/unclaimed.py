# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Deadline scanning and durable leader notification replay for unclaimed tasks."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from openjiuwen.agent_teams.organization.schema import OrgTaskStatus, OrgUnclaimedPhase
from openjiuwen.agent_teams.tools.database.engine import get_current_time

if TYPE_CHECKING:
    from openjiuwen.agent_teams.organization.manager import TeamOrganizationManager

logger = logging.getLogger(__name__)


class OrgUnclaimedTaskService:
    """One scanner per bound organization; inbox receipts are the retry source."""

    def __init__(
        self,
        manager: TeamOrganizationManager,
        notify: Callable[[dict[str, Any]], Awaitable[None]],
        interval_seconds: int,
    ) -> None:
        self.manager = manager
        self.notify = notify
        self.interval_seconds = interval_seconds
        self._worker: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()

    def start(self) -> None:
        if self._worker is None or self._worker.done():
            self._stopping.clear()
            self._worker = asyncio.create_task(self._run(), name=f"org-unclaimed:{self.manager.organization_id}")

    async def stop(self) -> None:
        if self._worker is not None:
            # Finish the current transaction. Cancelling an in-flight SQLite
            # query invalidates its connection (and destroys an in-memory DB).
            self._stopping.set()
            await self._worker
            self._worker = None

    async def scan_once(self, *, now: int | None = None) -> None:
        await self.manager.task_pool.advance_unclaimed_tasks(now=now)
        after_id = ""
        while True:
            messages = await self.manager.message_service.list_pending_system_notifications(after_id=after_id)
            if not messages:
                return
            for message in messages:
                if await self.is_actionable(message, now=now):
                    await self.notify(message)
            after_id = messages[-1]["message_id"]

    async def is_actionable(self, message: dict[str, Any], *, now: int | None = None) -> bool:
        """Retire stale requests both before enqueueing and immediately before a turn."""
        metadata = message["metadata"]
        kind = metadata.get("unclaimed_kind")
        if kind not in {"revision", "revised", "expired"}:
            return False
        task = await self.manager.task_pool.get_task(metadata["task_id"])
        state = task.unclaimed if task else None
        now = get_current_time() if now is None else now
        if kind == "expired":
            actionable = task is not None and task.status is OrgTaskStatus.FAILED
            if actionable and task.parent_task_id:
                parent = await self.manager.task_pool.get_task(task.parent_task_id)
                actionable = parent is not None and parent.status not in {OrgTaskStatus.FAILED, OrgTaskStatus.COMPLETED}
                if actionable:
                    actionable = not await self.manager.task_pool.has_accepted_or_active_repair(
                        parent_task_id=task.parent_task_id,
                        repairs_target=metadata["repairs_task_id"],
                    )
        else:
            expected = (
                OrgUnclaimedPhase.REVISION_PENDING if kind == "revision" else OrgUnclaimedPhase.POST_REVISION_WAIT
            )
            actionable = (
                state is not None
                and state.phase is expected
                and state.deadline_at is not None
                and state.deadline_at > now
                and task.status is OrgTaskStatus.OPEN
            )
        if not actionable:
            await self.manager.message_service.ack_leader_message(
                message_id=message["message_id"],
                team_id=message["to_team_id"],
                leader_id=message["to_leader_id"] or "",
                handling_result="request no longer actionable",
            )
        return actionable

    async def _run(self) -> None:
        while not self._stopping.is_set():
            try:
                await self.scan_once()
            except Exception:
                # Failed writes roll back; pending receipts survive transport/runner
                # failures. Retry on the next tick without inventing another queue.
                logger.exception("Unclaimed task scan failed organization_id=%s", self.manager.organization_id)
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=self.interval_seconds)
            except TimeoutError:
                pass


__all__ = ["OrgUnclaimedTaskService"]

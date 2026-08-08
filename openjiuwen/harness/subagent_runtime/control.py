# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Orchestration facade for subagent spawn, wait, and close."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.common.logging import logger
from openjiuwen.core.session.checkpointer import CheckpointerFactory
from openjiuwen.harness.kv_cache.kv_cache_hooks import is_sticky_subagent_type
from openjiuwen.harness.subagent_runtime.config import (
    WAIT_TIMEOUT_MS_DEFAULT,
    WAIT_TIMEOUT_MS_MAX,
    WAIT_TIMEOUT_MS_MIN,
    SubagentRuntimeConfig,
)
from openjiuwen.harness.subagent_runtime.errors import build_subagent_runtime_error, raise_subagent_not_found
from openjiuwen.harness.subagent_runtime.ids import build_subagent_id, new_task_id
from openjiuwen.harness.subagent_runtime.models import (
    ClosedSubagentRecord,
    SpawnResult,
    SubagentMetadata,
    SubagentStatus,
    SubagentStatusKind,
    UserInputOp,
    WaitResult,
    resolve_presentation,
)
from openjiuwen.harness.subagent_runtime.registry import SpawnReservation, SubagentRegistry
from openjiuwen.harness.subagent_runtime.session_manager import SubagentSessionManager
from openjiuwen.harness.subagent_runtime.status import StatusReceiver
from openjiuwen.harness.subagent_runtime.status_events import (
    build_subagent_updated_payload,
    emit_subagent_updated,
    is_externally_closed,
)

_TASK_DESCRIPTION_MAX_LEN = 2000


class SubagentControl:
    """Parent-session orchestration entry for subagent runtime."""

    def __init__(
        self,
        parent_agent: Any,
        parent_session_id: str,
        config: SubagentRuntimeConfig | None = None,
        parent_session: Any | None = None,
    ) -> None:
        self._parent_agent = parent_agent
        self._parent_session_id = parent_session_id
        self._parent_session = parent_session
        self._config = config or SubagentRuntimeConfig()
        self._registry = SubagentRegistry(self._config)
        self._semaphore = asyncio.Semaphore(self._config.max_concurrent_running)
        self._closed_records: dict[str, ClosedSubagentRecord] = {}
        self._manager = SubagentSessionManager(
            parent_agent,
            self._config,
            self._semaphore,
            status_change_handler=self._handle_instance_status_changed,
        )

    async def spawn(
        self,
        subagent_type: str,
        query: str,
        *,
        subagent_id: str | None = None,
        display_name: str | None = None,
        role: str | None = None,
        browser_capabilities: list[str] | None = None,
    ) -> SpawnResult:
        sticky = is_sticky_subagent_type(subagent_type)
        sid = subagent_id or build_subagent_id(
            self._parent_session_id,
            subagent_type,
            sticky=sticky,
        )
        existing = self._manager.find(sid)
        if existing is not None and not existing.is_closed():
            raise build_subagent_runtime_error(
                f"subagent already live: {sid}; use subagent_wait to collect its result",
            )

        task_id = new_task_id()
        resolved_name, resolved_role = self._resolve_spawn_presentation(
            subagent_type,
            display_name,
            role,
        )
        task_description = self._truncate_task_description(query)
        reservation = await self._acquire_slot()

        try:
            instance = await self._manager.create(
                subagent_type=subagent_type,
                subagent_id=sid,
                parent_session_id=self._parent_session_id,
                display_name=resolved_name,
                role=resolved_role,
                browser_capabilities=browser_capabilities,
            )
            reservation.commit(
                self._build_metadata(
                    sid,
                    subagent_type,
                    task_id,
                    resolved_name,
                    resolved_role,
                    task_description,
                ),
            )
        except Exception:
            reservation.rollback()
            raise

        try:
            await instance.enqueue(UserInputOp(query=query, task_id=task_id))
        except Exception:
            await self._manager.remove(sid, reason="spawn_failed")
            self._registry.release(sid)
            raise

        self._registry.touch(sid)
        return SpawnResult(
            subagent_id=sid,
            task_id=task_id,
            status=instance.agent_status(),
        )

    async def wait(
        self,
        subagent_ids: list[str],
        timeout_ms: int = WAIT_TIMEOUT_MS_DEFAULT,
    ) -> WaitResult:
        timeout_s = min(max(timeout_ms, WAIT_TIMEOUT_MS_MIN), WAIT_TIMEOUT_MS_MAX) / 1000
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s

        targets = list(dict.fromkeys(subagent_ids))
        statuses: dict[str, SubagentStatus] = {}
        waiters: dict[asyncio.Task[SubagentStatus], str] = {}

        for sid in targets:
            instance = self._manager.find(sid)
            if instance is None:
                statuses[sid] = SubagentStatus.not_found()
                continue
            self._registry.touch(sid)
            receiver = instance.subscribe_status()
            current = receiver.current()
            if current.is_final() and not instance.has_pending_work():
                statuses[sid] = current
                continue
            waiters[asyncio.create_task(receiver.wait_for_final())] = sid

        pending = set(waiters)
        try:
            while pending:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    break
                done, pending = await asyncio.wait(
                    pending,
                    timeout=remaining,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not done:
                    break
                for task in done:
                    sid = waiters[task]
                    statuses[sid] = self._resolve_final(sid, task)
        finally:
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

        for sid in targets:
            statuses.setdefault(sid, self.get_status(sid))

        timed_out = any(not status.is_final() for status in statuses.values())
        results = {
            sid: instance.last_output
            for sid in targets
            if statuses[sid].kind is SubagentStatusKind.COMPLETED
            and (instance := self._manager.find(sid)) is not None
            and instance.last_output
        }
        return WaitResult(statuses=statuses, results=results, timed_out=timed_out)

    def get_status(self, subagent_id: str) -> SubagentStatus:
        instance = self._manager.find(subagent_id)
        if instance is None:
            return SubagentStatus.not_found()
        return instance.agent_status()

    def subscribe_status(self, subagent_id: str) -> StatusReceiver:
        return self._manager.get(subagent_id).subscribe_status()

    def list_live(self) -> list[SubagentMetadata]:
        return self._registry.list_live()

    def capacity(self) -> dict[str, int]:
        """Return current slot usage for the parent session."""
        return {
            "used": self._registry.count,
            "max": self._config.max_subagents,
        }

    def describe_one(self, subagent_id: str) -> dict[str, Any] | None:
        """Return one subagent's external status payload, or None if never registered."""
        metadata = self._registry.find_metadata(subagent_id)
        instance = self._manager.find(subagent_id)
        if metadata is None and instance is None:
            record = self._closed_records.get(subagent_id)
            if record is None:
                return None
            return self._closed_record_to_payload(record)

        if metadata is None:
            status = instance.agent_status() if instance is not None else SubagentStatus.not_found()
            return build_subagent_updated_payload(
                subagent_id=subagent_id,
                subagent_type=instance.subagent_type if instance else "",
                display_name=instance.display_name if instance else subagent_id,
                role=instance.role if instance else "",
                parent_session_id=self._parent_session_id,
                task_description="",
                created_at_ms=0.0,
                updated_at_ms=0.0,
                closed_at_ms=None,
                status=status,
                revision=instance.revision() if instance is not None else 0,
            )

        status = instance.agent_status() if instance is not None else SubagentStatus.not_found()
        revision = instance.revision() if instance is not None else 0
        return self._metadata_to_payload(metadata, status=status, revision=revision)

    def describe_live(self) -> list[dict[str, Any]]:
        """Return external status payloads for every live subagent."""
        rows: list[dict[str, Any]] = []
        for metadata in self._registry.list_live():
            instance = self._manager.find(metadata.subagent_id)
            if instance is None:
                continue
            rows.append(
                self._metadata_to_payload(
                    metadata,
                    status=instance.agent_status(),
                    revision=instance.revision(),
                )
            )
        return rows

    async def emit_status_update(
        self,
        subagent_id: str,
        *,
        session: Any | None = None,
    ) -> None:
        """Push one subagent status update to the parent session stream."""
        target_session = session or self._parent_session
        if target_session is None:
            return
        projection = self.describe_one(subagent_id)
        if projection is None:
            return
        await emit_subagent_updated(target_session, projection=projection)

    async def send_input(
        self,
        subagent_id: str,
        query: str,
        *,
        interrupt: bool = False,
    ) -> str:
        """Enqueue follow-up input and return a new task_id without blocking."""
        instance = self._manager.find(subagent_id)
        if instance is None or instance.is_closed():
            raise build_subagent_runtime_error(
                f"subagent closed or not found: {subagent_id}; call subagent_resume first",
            )
        if interrupt:
            await instance.interrupt()
        task_id = new_task_id()
        await instance.enqueue(UserInputOp(query=query, task_id=task_id))
        self._registry.touch(subagent_id)
        metadata = self._registry.find_metadata(subagent_id)
        if metadata is not None:
            metadata.current_task_id = task_id
            metadata.task_description = self._truncate_task_description(query)
            metadata.updated_at_ms = time.time() * 1000
        return task_id

    async def resume(self, subagent_id: str) -> SubagentStatus:
        """Restore a closed or evicted subagent from checkpointer without enqueueing work."""
        existing = self._manager.find(subagent_id)
        if existing is not None and not existing.is_closed():
            return existing.agent_status()

        record = self._closed_records.get(subagent_id)
        if record is None:
            raise_subagent_not_found(subagent_id)

        checkpointer = CheckpointerFactory.get_checkpointer()
        if not await checkpointer.session_exists(subagent_id):
            raise_subagent_not_found(subagent_id)

        reservation = await self._acquire_slot()
        try:
            await self._manager.restore(
                subagent_id=subagent_id,
                subagent_type=record.subagent_type,
                parent_session_id=self._parent_session_id,
                display_name=record.display_name,
                role=record.role,
            )
            reservation.commit(
                self._build_metadata_from_record(record, task_id=None),
            )
        except Exception:
            reservation.rollback()
            raise

        self._closed_records.pop(subagent_id, None)
        return SubagentStatus.pending_init()

    async def close(self, subagent_id: str, reason: str = "manual") -> SubagentStatus:
        instance = self._manager.get(subagent_id)
        previous = instance.agent_status()
        if previous.kind is SubagentStatusKind.RUNNING:
            raise build_subagent_runtime_error(
                f"cannot close running subagent: {subagent_id}",
            )
        await self._evict_from_memory(subagent_id, reason=reason)
        return previous

    async def cancel_all(self, reason: str = "parent_ended") -> list[str]:
        """Force-close every live subagent regardless of RUNNING state."""
        closed: list[str] = []
        for sid in list(self._manager.list_ids()):
            try:
                await self._evict_from_memory(sid, reason=reason)
            except Exception:
                logger.warning("[SubagentControl] cancel_all failed: sid=%s", sid)
                self._registry.release(sid)
            closed.append(sid)
        return closed

    async def _evict_from_memory(self, subagent_id: str, *, reason: str) -> None:
        metadata = self._registry.find_metadata(subagent_id)
        if metadata is not None and metadata.closed_at_ms is None:
            metadata.closed_at_ms = time.time() * 1000
        await self._manager.remove(subagent_id, reason=reason)
        if metadata is not None:
            self._store_closed_record(metadata, close_reason=reason)
        self._registry.release(subagent_id)

    async def _acquire_slot(self) -> SpawnReservation:
        try:
            return self._registry.reserve_slot()
        except BaseError:
            if not self._config.enable_lru_eviction:
                raise
            for sid in self._registry.lru_candidates():
                instance = self._manager.find(sid)
                if instance is not None and instance.is_evictable():
                    await self.close(sid, reason="evicted")
                    return self._registry.reserve_slot()
            raise

    def _build_metadata(
        self,
        sid: str,
        subagent_type: str,
        task_id: str,
        display_name: str,
        role: str,
        task_description: str,
    ) -> SubagentMetadata:
        now_mono = time.monotonic()
        now_ms = time.time() * 1000
        return SubagentMetadata(
            subagent_id=sid,
            subagent_type=subagent_type,
            display_name=display_name,
            role=role,
            parent_session_id=self._parent_session_id,
            created_at=now_mono,
            last_used_at=now_mono,
            current_task_id=task_id,
            task_description=task_description,
            created_at_ms=now_ms,
            updated_at_ms=now_ms,
        )

    def _build_metadata_from_record(
        self,
        record: ClosedSubagentRecord,
        *,
        task_id: str | None,
    ) -> SubagentMetadata:
        now_mono = time.monotonic()
        now_ms = time.time() * 1000
        return SubagentMetadata(
            subagent_id=record.subagent_id,
            subagent_type=record.subagent_type,
            display_name=record.display_name,
            role=record.role,
            parent_session_id=self._parent_session_id,
            created_at=now_mono,
            last_used_at=now_mono,
            current_task_id=task_id,
            task_description=record.task_description,
            created_at_ms=record.created_at_ms,
            updated_at_ms=now_ms,
        )

    def _store_closed_record(
        self,
        metadata: SubagentMetadata,
        *,
        close_reason: str,
    ) -> None:
        closed_at_ms = metadata.closed_at_ms or time.time() * 1000
        self._closed_records[metadata.subagent_id] = ClosedSubagentRecord(
            subagent_id=metadata.subagent_id,
            subagent_type=metadata.subagent_type,
            display_name=metadata.display_name,
            role=metadata.role,
            task_description=metadata.task_description,
            closed_reason=close_reason,
            closed_at_ms=closed_at_ms,
            created_at_ms=metadata.created_at_ms,
        )
        self._trim_closed_records()

    def _trim_closed_records(self) -> None:
        limit = max(self._config.max_subagents * 2, 1)
        if len(self._closed_records) <= limit:
            return
        excess = len(self._closed_records) - limit
        oldest = sorted(
            self._closed_records.values(),
            key=lambda record: record.closed_at_ms,
        )[:excess]
        for record in oldest:
            self._closed_records.pop(record.subagent_id, None)

    def _closed_record_to_payload(self, record: ClosedSubagentRecord) -> dict[str, Any]:
        return build_subagent_updated_payload(
            subagent_id=record.subagent_id,
            subagent_type=record.subagent_type,
            display_name=record.display_name,
            role=record.role,
            parent_session_id=self._parent_session_id,
            task_description=record.task_description,
            created_at_ms=record.created_at_ms,
            updated_at_ms=record.closed_at_ms,
            closed_at_ms=record.closed_at_ms,
            status=SubagentStatus.closed(record.closed_reason),
            revision=0,
        )

    def _metadata_to_payload(
        self,
        metadata: SubagentMetadata,
        *,
        status: SubagentStatus,
        revision: int,
    ) -> dict[str, Any]:
        return build_subagent_updated_payload(
            subagent_id=metadata.subagent_id,
            subagent_type=metadata.subagent_type,
            display_name=metadata.display_name,
            role=metadata.role,
            parent_session_id=metadata.parent_session_id,
            task_description=metadata.task_description,
            created_at_ms=metadata.created_at_ms,
            updated_at_ms=metadata.updated_at_ms,
            closed_at_ms=metadata.closed_at_ms,
            status=status,
            revision=revision,
        )

    def _resolve_spawn_presentation(
        self,
        subagent_type: str,
        display_name: str | None,
        role: str | None,
    ) -> tuple[str, str]:
        spec = self._lookup_subagent_config(subagent_type)
        agent_card = getattr(spec, "agent_card", None) if spec is not None else None
        config_display = getattr(spec, "display_name", None) if spec is not None else None
        config_role = getattr(spec, "role", None) if spec is not None else None
        return resolve_presentation(
            subagent_type=subagent_type,
            display_name=display_name or config_display,
            role=role or config_role,
            agent_card=agent_card,
        )

    def _lookup_subagent_config(self, subagent_type: str) -> Any | None:
        deep_config = getattr(self._parent_agent, "deep_config", None)
        subagents = getattr(deep_config, "subagents", None) or []
        for spec in subagents:
            card = getattr(spec, "agent_card", None)
            if card is None:
                continue
            if getattr(card, "id", None) == subagent_type or getattr(card, "name", None) == subagent_type:
                return spec
        return None

    @staticmethod
    def _truncate_task_description(query: str) -> str:
        text = str(query or "").strip()
        if len(text) <= _TASK_DESCRIPTION_MAX_LEN:
            return text
        return text[:_TASK_DESCRIPTION_MAX_LEN]

    def _touch_metadata_timestamps(
        self,
        metadata: SubagentMetadata,
        *,
        status: SubagentStatus,
    ) -> None:
        metadata.updated_at_ms = time.time() * 1000
        if is_externally_closed(status) and metadata.closed_at_ms is None:
            metadata.closed_at_ms = metadata.updated_at_ms

    async def _handle_instance_status_changed(
        self,
        subagent_id: str,
        status: SubagentStatus,
    ) -> None:
        if not is_externally_closed(status):
            return
        metadata = self._registry.find_metadata(subagent_id)
        if metadata is not None:
            self._touch_metadata_timestamps(metadata, status=status)
        await self.emit_status_update(subagent_id)

    def _resolve_final(
        self,
        sid: str,
        task: asyncio.Task[SubagentStatus],
    ) -> SubagentStatus:
        if task.cancelled() or task.exception() is not None:
            return self.get_status(sid)
        status = task.result()
        if status.is_final():
            return status
        return self.get_status(sid)

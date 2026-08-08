# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Orchestration facade for subagent spawn, wait, and close."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.common.logging import logger
from openjiuwen.harness.kv_cache.kv_cache_hooks import is_sticky_subagent_type
from openjiuwen.harness.subagent_runtime.config import (
    WAIT_TIMEOUT_MS_DEFAULT,
    WAIT_TIMEOUT_MS_MAX,
    WAIT_TIMEOUT_MS_MIN,
    SubagentRuntimeConfig,
)
from openjiuwen.harness.subagent_runtime.errors import build_subagent_runtime_error
from openjiuwen.harness.subagent_runtime.ids import build_subagent_id, new_task_id
from openjiuwen.harness.subagent_runtime.models import (
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


class SubagentControl:
    """Parent-session orchestration entry for subagent runtime."""

    def __init__(
        self,
        parent_agent: Any,
        parent_session_id: str,
        config: SubagentRuntimeConfig | None = None,
    ) -> None:
        self._parent_session_id = parent_session_id
        self._config = config or SubagentRuntimeConfig()
        self._registry = SubagentRegistry(self._config)
        self._semaphore = asyncio.Semaphore(self._config.max_concurrent_running)
        self._manager = SubagentSessionManager(
            parent_agent,
            self._config,
            self._semaphore,
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
        resolved_name, resolved_role = resolve_presentation(
            subagent_type=subagent_type,
            display_name=display_name,
            role=role,
        )
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
            if current.is_final():
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

    def describe_live(self) -> list[dict[str, Any]]:
        """Return a serializable summary of every live subagent."""
        rows: list[dict[str, Any]] = []
        for metadata in self._registry.list_live():
            instance = self._manager.find(metadata.subagent_id)
            if instance is None:
                continue
            status = instance.agent_status()
            rows.append(
                {
                    "subagent_id": metadata.subagent_id,
                    "subagent_type": metadata.subagent_type,
                    "display_name": metadata.display_name,
                    "role": metadata.role,
                    "status": status.kind.value,
                    "revision": instance.revision(),
                    "result": instance.last_output
                    if status.kind is SubagentStatusKind.COMPLETED
                    else None,
                }
            )
        return rows

    async def close(self, subagent_id: str, reason: str = "manual") -> SubagentStatus:
        instance = self._manager.get(subagent_id)
        previous = instance.agent_status()
        if previous.kind is SubagentStatusKind.RUNNING:
            raise build_subagent_runtime_error(
                f"cannot close running subagent: {subagent_id}",
            )
        await self._manager.remove(subagent_id, reason=reason)
        self._registry.release(subagent_id)
        return previous

    async def cancel_all(self, reason: str = "parent_ended") -> list[str]:
        """Force-close every live subagent regardless of RUNNING state."""
        closed: list[str] = []
        for sid in self._manager.list_ids():
            try:
                await self._manager.remove(sid, reason=reason)
            except Exception:
                logger.warning("[SubagentControl] cancel_all failed: sid=%s", sid)
            finally:
                self._registry.release(sid)
                closed.append(sid)
        return closed

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
    ) -> SubagentMetadata:
        now = time.monotonic()
        return SubagentMetadata(
            subagent_id=sid,
            subagent_type=subagent_type,
            display_name=display_name,
            role=role,
            parent_session_id=self._parent_session_id,
            created_at=now,
            last_used_at=now,
            current_task_id=task_id,
        )

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

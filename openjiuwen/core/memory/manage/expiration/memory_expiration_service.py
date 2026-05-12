# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Callable, Awaitable

from openjiuwen.core.common.logging import memory_logger
from openjiuwen.core.common.logging.events import LogEventType
from openjiuwen.core.foundation.store.base_kv_store import BaseKVStore
from openjiuwen.core.memory.common.distributed_lock import DistributedLock
from openjiuwen.core.memory.config.config import MemoryEngineConfig
from openjiuwen.core.memory.manage.index.write_manager import WriteManager
from openjiuwen.core.memory.manage.mem_model.memory_unit import MemoryType
from openjiuwen.core.memory.manage.search.search_manager import SearchManager
from openjiuwen.core.memory.manage.mem_model.scope_user_mapping_manager import ScopeUserMappingManager
from openjiuwen.core.memory.manage.mem_model.semantic_store import SemanticStore

_EXPIRABLE_MEM_TYPES = [
    MemoryType.USER_PROFILE.value,
    MemoryType.SEMANTIC_MEMORY.value,
    MemoryType.EPISODIC_MEMORY.value,
    MemoryType.SUMMARY.value,
]


class MemoryExpirationService:
    _CHECK_INTERVAL_SECONDS: int = 12 * 60 * 60

    def __init__(
            self,
            kv_store: BaseKVStore,
            config: MemoryEngineConfig,
            scope_user_mapping_manager: ScopeUserMappingManager,
            write_manager: WriteManager,
            search_manager: SearchManager,
            semantic_store_factory: Callable[[str], Awaitable[SemanticStore]],
    ):
        self._kv_store = kv_store
        self._config = config
        self._scope_user_mapping_manager = scope_user_mapping_manager
        self._write_manager = write_manager
        self._search_manager = search_manager
        self._semantic_store_factory = semantic_store_factory
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_periodically())
        memory_logger.info(
            "Memory expiration service started.",
            event_type=LogEventType.MEMORY_INIT,
        )

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        memory_logger.info(
            "Memory expiration service stopped.",
            event_type=LogEventType.MEMORY_INIT,
        )

    async def _run_periodically(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(self._CHECK_INTERVAL_SECONDS)
                cutoff = datetime.now(timezone.utc).astimezone() - timedelta(
                    seconds=self._config.memory_expiration_seconds
                )
                await self.cleanup_all_users(cutoff_time=cutoff)
            except asyncio.CancelledError:
                break
            except Exception as e:
                memory_logger.error(
                    "Memory expiration periodic cleanup failed.",
                    event_type=LogEventType.MEMORY_DELETE,
                    exception=str(e),
                )

    async def cleanup_all_users(self, cutoff_time: datetime | None = None) -> None:
        if cutoff_time is None:
            cutoff_time = datetime.now(timezone.utc).astimezone() - timedelta(
                seconds=self._config.memory_expiration_seconds
            )
        mappings = await self._scope_user_mapping_manager.get_all_mappings()
        if not mappings:
            return
        total_deleted = 0
        for mapping in mappings:
            user_id = mapping.get("user_id", "")
            scope_id = mapping.get("scope_id", "")
            if not user_id or not scope_id:
                continue
            try:
                deleted = await self._cleanup_user(user_id, scope_id, cutoff_time)
                total_deleted += deleted
            except Exception as e:
                memory_logger.error(
                    "Failed to cleanup expired memories for user.",
                    event_type=LogEventType.MEMORY_DELETE,
                    user_id=user_id,
                    scope_id=scope_id,
                    exception=str(e),
                )
        if total_deleted > 0:
            memory_logger.info(
                "Memory expiration cleanup completed.",
                event_type=LogEventType.MEMORY_DELETE,
                total_deleted=total_deleted,
            )

    async def _cleanup_user(self, user_id: str, scope_id: str, cutoff_time: datetime) -> int:
        lock = DistributedLock(self._kv_store, f"user/{user_id}")

        # Phase 1: Acquire lock, fetch all memories, release lock
        async with lock:
            snapshot = await self._fetch_all_memories(user_id, scope_id)

        # Calculate expired candidates outside lock to minimize lock holding time
        candidate_ids = self._find_expired_ids(snapshot, cutoff_time)
        if not candidate_ids:
            return 0

        # Phase 2: Re-acquire lock, check each candidate individually before deleting
        async with lock:
            semantic_store = await self._semantic_store_factory(scope_id)
            deleted_count = 0
            for mem_id in candidate_ids:
                mem_data = await self._search_manager.get_mem_by_id(user_id, scope_id, mem_id)
                if mem_data is not None and self._is_expired(mem_data.get("timestamp", ""), cutoff_time):
                    await self._write_manager.delete_mem_by_id(
                        user_id=user_id,
                        scope_id=scope_id,
                        mem_id=mem_id,
                        semantic_store=semantic_store,
                    )
                    deleted_count += 1

            if deleted_count > 0:
                memory_logger.debug(
                    "Cleaned up expired memories for user.",
                    event_type=LogEventType.MEMORY_DELETE,
                    user_id=user_id,
                    scope_id=scope_id,
                    deleted_count=deleted_count,
                )
            return deleted_count

    async def _fetch_all_memories(self, user_id: str, scope_id: str) -> dict[str, list[dict]]:
        result: dict[str, list[dict]] = {}
        for mem_type in _EXPIRABLE_MEM_TYPES:
            memories = await self._search_manager.get_all(user_id, scope_id, mem_type)
            if memories:
                result[mem_type] = memories
        return result

    @staticmethod
    def _find_expired_ids(memories_by_type: dict[str, list[dict]], cutoff_time: datetime) -> list[str]:
        expired: list[str] = []
        for memories in memories_by_type.values():
            for mem in memories:
                timestamp_str = mem.get("timestamp", "")
                mem_id = mem.get("id", "")
                if mem_id and MemoryExpirationService._is_expired(timestamp_str, cutoff_time):
                    expired.append(mem_id)
        return expired

    @staticmethod
    def _is_expired(timestamp_str: str, cutoff_time: datetime) -> bool:
        if not timestamp_str:
            return False
        try:
            mem_time = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
            mem_time = mem_time.astimezone()
            return mem_time < cutoff_time
        except (ValueError, TypeError):
            return False

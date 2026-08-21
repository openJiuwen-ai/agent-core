# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""In-process subagent instance quota and metadata registry."""

from __future__ import annotations

import time
from dataclasses import dataclass

from openjiuwen.harness.subagent_runtime.config import SubagentRuntimeConfig
from openjiuwen.harness.subagent_runtime.errors import raise_subagent_capacity_invalid
from openjiuwen.harness.subagent_runtime.models import SubagentMetadata


@dataclass
class SpawnReservation:
    """Placeholder for one pending subagent instance creation."""

    _registry: SubagentRegistry
    _active: bool = True

    def commit(self, metadata: SubagentMetadata) -> None:
        if not self._active:
            return
        self._registry.register(metadata)
        self._registry.release_pending()
        self._active = False

    def rollback(self) -> None:
        if not self._active:
            return
        self._registry.release_pending()
        self._active = False


class SubagentRegistry:
    """Synchronous quota ledger and metadata index for live subagent instances."""

    def __init__(self, config: SubagentRuntimeConfig) -> None:
        self._config = config
        self._table: dict[str, SubagentMetadata] = {}
        self._pending = 0

    @property
    def count(self) -> int:
        return len(self._table) + self._pending

    def reserve_slot(self) -> SpawnReservation:
        limit = self._config.max_subagents
        if self.count >= limit:
            raise_subagent_capacity_invalid(used=self.count, limit=limit)
        self._pending += 1
        return SpawnReservation(self)

    def register(self, metadata: SubagentMetadata) -> None:
        self._table[metadata.subagent_id] = metadata

    def release(self, subagent_id: str) -> None:
        self._table.pop(subagent_id, None)

    def touch(self, subagent_id: str) -> None:
        metadata = self._table.get(subagent_id)
        if metadata is None:
            return
        metadata.last_used_at = time.monotonic()

    def find_metadata(self, subagent_id: str) -> SubagentMetadata | None:
        return self._table.get(subagent_id)

    def list_live(self) -> list[SubagentMetadata]:
        return sorted(self._table.values(), key=lambda item: item.created_at)

    def lru_candidates(self) -> list[str]:
        ordered = sorted(self._table.values(), key=lambda item: item.last_used_at)
        return [item.subagent_id for item in ordered]

    def release_pending(self) -> None:
        if self._pending > 0:
            self._pending -= 1

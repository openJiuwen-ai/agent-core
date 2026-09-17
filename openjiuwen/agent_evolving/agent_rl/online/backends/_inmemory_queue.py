# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Reusable in-memory status queue for online RL/SFT stores."""

from __future__ import annotations

import asyncio
import copy
from collections.abc import Sequence
from typing import Any


class InMemoryStatusQueue:
    """Small in-memory queue with per-user status buckets."""

    def __init__(self, *, id_field: str) -> None:
        self._id_field = id_field
        self._items: dict[str, dict[str, Any]] = {}
        self._status_index: dict[str, dict[str, list[str]]] = {}
        self._lock = asyncio.Lock()

    async def save(self, payload: dict[str, Any], *, user_id: str = "online", initial_status: str = "pending") -> None:
        item_id = str(payload.get(self._id_field) or payload.get("id") or "").strip()
        if not item_id:
            raise ValueError(f"{self._id_field} is required")

        normalized = copy.deepcopy(payload)
        normalized[self._id_field] = item_id
        normalized["user_id"] = str(normalized.get("user_id") or user_id or "online")
        normalized["_store_status"] = initial_status

        async with self._lock:
            old = self._items.get(item_id)
            if old is not None:
                self._remove(item_id, str(old.get("user_id") or "online"), str(old.get("_store_status") or "pending"))
            self._items[item_id] = normalized
            self._add(item_id, normalized["user_id"], initial_status)

    async def save_once(
        self,
        payloads: Sequence[dict[str, Any]],
        *,
        user_id: str = "online",
        initial_status: str = "pending",
    ) -> set[str]:
        prepared: list[tuple[str, dict[str, Any]]] = []
        for payload in payloads:
            item_id = str(payload.get(self._id_field) or payload.get("id") or "").strip()
            if not item_id:
                raise ValueError(f"{self._id_field} is required")
            normalized = copy.deepcopy(payload)
            normalized[self._id_field] = item_id
            normalized["user_id"] = str(normalized.get("user_id") or user_id or "online")
            normalized["_store_status"] = initial_status
            prepared.append((item_id, normalized))

        async with self._lock:
            saved_ids: set[str] = set()
            for item_id, normalized in prepared:
                if item_id in self._items:
                    continue
                self._items[item_id] = normalized
                self._add(item_id, normalized["user_id"], initial_status)
                saved_ids.add(item_id)
            return saved_ids

    async def pending_count(self, user_id: str, *, status: str = "pending") -> int:
        async with self._lock:
            return len(self._status_index.get(user_id, {}).get(status, []))

    async def users_above_threshold(self, threshold: int, *, status: str = "pending") -> list[str]:
        normalized_threshold = max(1, int(threshold))
        async with self._lock:
            return [
                user_id
                for user_id, statuses in self._status_index.items()
                if len(statuses.get(status, [])) >= normalized_threshold
            ]

    async def fetch_and_mark(
        self,
        user_id: str,
        limit: int,
        *,
        from_status: str = "pending",
        to_status: str,
    ) -> list[dict[str, Any]]:
        async with self._lock:
            ids = list(self._status_index.get(user_id, {}).get(from_status, []))[: max(1, int(limit))]
            out: list[dict[str, Any]] = []
            for item_id in ids:
                item = self._items.get(item_id)
                if item is None:
                    continue
                self._remove(item_id, user_id, from_status)
                self._add(item_id, user_id, to_status)
                item["_store_status"] = to_status
                out.append(copy.deepcopy(item))
            return out

    async def update_status(self, item_ids: list[str], *, from_status: str, to_status: str) -> None:
        async with self._lock:
            for item_id in item_ids:
                item = self._items.get(item_id)
                if item is None:
                    continue
                user_id = str(item.get("user_id") or "online")
                self._remove(item_id, user_id, from_status)
                self._add(item_id, user_id, to_status)
                item["_store_status"] = to_status

    async def stats(self, *, statuses: tuple[str, ...]) -> dict[str, int]:
        async with self._lock:
            return {
                status: sum(len(user_statuses.get(status, [])) for user_statuses in self._status_index.values())
                for status in statuses
            }

    async def get(self, item_id: str) -> dict[str, Any] | None:
        async with self._lock:
            item = self._items.get(item_id)
            return copy.deepcopy(item) if item is not None else None

    async def list(
        self,
        *,
        user_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        async with self._lock:
            out: list[dict[str, Any]] = []
            user_ids = [user_id] if user_id else sorted(self._status_index)
            for uid in user_ids:
                statuses = [status] if status else sorted(self._status_index.get(uid, {}))
                for item_status in statuses:
                    for item_id in self._status_index.get(uid, {}).get(item_status, []):
                        item = self._items.get(item_id)
                        if item is None:
                            continue
                        out.append(copy.deepcopy(item))
                        if len(out) >= max(1, int(limit)):
                            return out
            return out

    async def patch(
        self,
        item_id: str,
        updates: dict[str, Any],
        *,
        allowed_keys: tuple[str, ...],
    ) -> dict[str, Any] | None:
        async with self._lock:
            item = self._items.get(item_id)
            if item is None:
                return None
            old_status = str(item.get("_store_status") or "pending")
            new_status = str(updates.get("status") or old_status)
            if new_status != old_status:
                user_id = str(item.get("user_id") or "online")
                self._remove(item_id, user_id, old_status)
                self._add(item_id, user_id, new_status)
                item["_store_status"] = new_status
            for key in allowed_keys:
                if key in updates:
                    item[key] = copy.deepcopy(updates[key])
            return copy.deepcopy(item)

    async def delete(self, item_id: str, *, force: bool = False) -> bool:
        del force
        async with self._lock:
            item = self._items.pop(item_id, None)
            if item is None:
                return False
            self._remove(
                item_id,
                str(item.get("user_id") or "online"),
                str(item.get("_store_status") or "pending"),
            )
            return True

    def _add(self, item_id: str, user_id: str, status: str) -> None:
        bucket = self._status_index.setdefault(user_id, {}).setdefault(status, [])
        if item_id not in bucket:
            bucket.append(item_id)

    def _remove(self, item_id: str, user_id: str, status: str) -> None:
        bucket = self._status_index.get(user_id, {}).get(status)
        if not bucket:
            return
        try:
            bucket.remove(item_id)
        except ValueError:
            return

# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared async store contract for SFT raw trajectories and SFT samples."""

from __future__ import annotations

import asyncio
import copy
from typing import Any

from openjiuwen.agent_evolving.agent_rl.online.abstract.store import SFTSampleStore


class InMemorySFTStore:
    """In-memory SFT store used by unit tests and embedded dry-run flows."""

    def __init__(self) -> None:
        self._raw = _InMemoryQueue(id_field="raw_id")
        self._samples = _InMemoryQueue(id_field="sample_id")

    async def save_raw(self, raw: dict[str, Any], *, user_id: str = "online") -> None:
        await self._raw.save(raw, user_id=user_id)

    async def save_sample(self, sample: dict[str, Any], *, user_id: str = "online") -> None:
        await self._samples.save(sample, user_id=user_id)

    async def get_pending_raw_count(self, user_id: str) -> int:
        return await self._raw.pending_count(user_id)

    async def get_pending_sample_count(self, user_id: str) -> int:
        return await self._samples.pending_count(user_id)

    async def get_raw_users_above_threshold(self, threshold: int) -> list[str]:
        return await self._raw.users_above_threshold(threshold)

    async def get_sample_users_above_threshold(self, threshold: int) -> list[str]:
        return await self._samples.users_above_threshold(threshold)

    async def fetch_raw_and_mark_processing(self, user_id: str, limit: int) -> list[dict[str, Any]]:
        return await self._raw.fetch_and_mark(user_id, limit, "processing")

    async def fetch_samples_and_mark_training(self, user_id: str, limit: int) -> list[dict[str, Any]]:
        return await self._samples.fetch_and_mark(user_id, limit, "training")

    async def mark_raw_processed(self, raw_ids: list[str]) -> None:
        await self._raw.update_status(raw_ids, from_status="processing", to_status="processed")

    async def mark_raw_failed(self, raw_ids: list[str]) -> None:
        await self._raw.update_status(raw_ids, from_status="processing", to_status="failed")

    async def mark_samples_trained(self, sample_ids: list[str]) -> None:
        await self._samples.update_status(sample_ids, from_status="training", to_status="trained")

    async def mark_samples_failed(self, sample_ids: list[str]) -> None:
        await self._samples.update_status(sample_ids, from_status="training", to_status="failed")

    async def stats(self) -> dict[str, int]:
        raw_stats = await self._raw.stats()
        sample_stats = await self._samples.stats()
        return {
            "pending_raw": raw_stats["pending"],
            "processing_raw": raw_stats["processing"],
            "processed_raw": raw_stats["processed"],
            "failed_raw": raw_stats["failed"],
            "pending_samples": sample_stats["pending"],
            "training_samples": sample_stats["training"],
            "trained_samples": sample_stats["trained"],
            "failed_samples": sample_stats["failed"],
        }


class _InMemoryQueue:
    def __init__(self, *, id_field: str) -> None:
        self._id_field = id_field
        self._items: dict[str, dict[str, Any]] = {}
        self._status_index: dict[str, dict[str, list[str]]] = {}
        self._lock = asyncio.Lock()

    async def save(self, payload: dict[str, Any], *, user_id: str = "online") -> None:
        item_id = str(payload.get(self._id_field) or payload.get("id") or "").strip()
        if not item_id:
            raise ValueError(f"{self._id_field} is required")
        normalized = copy.deepcopy(payload)
        normalized[self._id_field] = item_id
        normalized["user_id"] = str(normalized.get("user_id") or user_id or "online")
        normalized["_store_status"] = "pending"
        async with self._lock:
            old = self._items.get(item_id)
            if old is not None:
                self._remove(item_id, str(old.get("user_id") or "online"), str(old.get("_store_status") or "pending"))
            self._items[item_id] = normalized
            self._add(item_id, normalized["user_id"], "pending")

    async def pending_count(self, user_id: str) -> int:
        async with self._lock:
            return len(self._status_index.get(user_id, {}).get("pending", []))

    async def users_above_threshold(self, threshold: int) -> list[str]:
        async with self._lock:
            return [
                user_id
                for user_id, statuses in self._status_index.items()
                if len(statuses.get("pending", [])) >= max(1, int(threshold))
            ]

    async def fetch_and_mark(self, user_id: str, limit: int, status: str) -> list[dict[str, Any]]:
        async with self._lock:
            ids = list(self._status_index.get(user_id, {}).get("pending", []))[: max(1, int(limit))]
            out: list[dict[str, Any]] = []
            for item_id in ids:
                item = self._items.get(item_id)
                if item is None:
                    continue
                self._remove(item_id, user_id, "pending")
                self._add(item_id, user_id, status)
                item["_store_status"] = status
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

    async def stats(self) -> dict[str, int]:
        async with self._lock:
            statuses = ("pending", "processing", "processed", "training", "trained", "failed")
            return {
                status: sum(len(user_statuses.get(status, [])) for user_statuses in self._status_index.values())
                for status in statuses
            }

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

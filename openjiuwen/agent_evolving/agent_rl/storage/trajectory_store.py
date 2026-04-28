# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared async trajectory-sample store contract and in-memory backend."""

from __future__ import annotations

import asyncio
import copy
from typing import Any, Protocol


class TrajectorySampleStore(Protocol):
    """Stateful queue for scored RL samples waiting for training."""

    async def save_sample(self, sample: dict[str, Any], *, user_id: str = "online") -> None:
        """Save a sample as pending for ``user_id``."""

    async def get_pending_count(self, user_id: str) -> int:
        """Return pending sample count for ``user_id``."""

    async def get_users_above_threshold(self, threshold: int) -> list[str]:
        """Return users whose pending sample count reaches ``threshold``."""

    async def fetch_and_mark_training(self, user_id: str, limit: int) -> list[dict[str, Any]]:
        """Atomically move pending samples to training and return them."""

    async def mark_trained(self, sample_ids: list[str]) -> None:
        """Mark training samples as trained."""

    async def mark_failed(self, sample_ids: list[str]) -> None:
        """Mark training samples as failed."""

    async def reset_to_pending(self, sample_ids: list[str]) -> None:
        """Move training samples back to pending."""

    async def stats(self) -> dict[str, int]:
        """Return store counters."""


class InMemoryTrajectoryStore:
    """Lightweight in-memory trajectory store for scored training samples."""

    def __init__(self) -> None:
        self._samples: dict[str, dict[str, Any]] = {}
        self._status_index: dict[str, dict[str, list[str]]] = {}
        self._lock = asyncio.Lock()

    async def save_sample(self, sample: dict[str, Any], *, user_id: str = "online") -> None:
        sample_id = str(sample.get("sample_id") or "").strip()
        if not sample_id:
            raise ValueError("sample_id is required")
        normalized = copy.deepcopy(sample)
        normalized["user_id"] = str(normalized.get("user_id") or user_id or "online")
        normalized["_store_status"] = "pending"

        async with self._lock:
            old = self._samples.get(sample_id)
            if old is not None:
                self._remove_from_status_index(sample_id, old["user_id"], old["_store_status"])
            self._samples[sample_id] = normalized
            self._add_to_status_index(sample_id, normalized["user_id"], "pending")

    async def get_pending_count(self, user_id: str) -> int:
        async with self._lock:
            return len(self._status_index.get(user_id, {}).get("pending", []))

    async def get_users_above_threshold(self, threshold: int) -> list[str]:
        async with self._lock:
            return [
                user_id
                for user_id, statuses in self._status_index.items()
                if len(statuses.get("pending", [])) >= threshold
            ]

    async def fetch_and_mark_training(self, user_id: str, limit: int) -> list[dict[str, Any]]:
        async with self._lock:
            pending = list(self._status_index.get(user_id, {}).get("pending", []))[: max(1, int(limit))]
            out: list[dict[str, Any]] = []
            for sample_id in pending:
                sample = self._samples.get(sample_id)
                if sample is None:
                    continue
                self._remove_from_status_index(sample_id, user_id, "pending")
                self._add_to_status_index(sample_id, user_id, "training")
                sample["_store_status"] = "training"
                out.append(copy.deepcopy(sample))
            return out

    async def mark_trained(self, sample_ids: list[str]) -> None:
        await self._update_status(sample_ids, from_status="training", to_status="trained")

    async def mark_failed(self, sample_ids: list[str]) -> None:
        await self._update_status(sample_ids, from_status="training", to_status="failed")

    async def reset_to_pending(self, sample_ids: list[str]) -> None:
        await self._update_status(sample_ids, from_status="training", to_status="pending")

    async def stats(self) -> dict[str, int]:
        async with self._lock:
            pending = sum(len(statuses.get("pending", [])) for statuses in self._status_index.values())
            training = sum(len(statuses.get("training", [])) for statuses in self._status_index.values())
            trained = sum(len(statuses.get("trained", [])) for statuses in self._status_index.values())
            failed = sum(len(statuses.get("failed", [])) for statuses in self._status_index.values())
            return {
                "total_samples": pending + training + trained + failed,
                "pending_samples": pending,
                "training_samples": training,
                "trained_samples": trained,
                "failed_samples": failed,
            }

    async def _update_status(self, sample_ids: list[str], *, from_status: str, to_status: str) -> None:
        async with self._lock:
            for sample_id in sample_ids:
                sample = self._samples.get(sample_id)
                if sample is None:
                    continue
                user_id = str(sample.get("user_id") or "online")
                self._remove_from_status_index(sample_id, user_id, from_status)
                self._add_to_status_index(sample_id, user_id, to_status)
                sample["_store_status"] = to_status

    def _add_to_status_index(self, sample_id: str, user_id: str, status: str) -> None:
        user_statuses = self._status_index.setdefault(user_id, {})
        bucket = user_statuses.setdefault(status, [])
        if sample_id not in bucket:
            bucket.append(sample_id)

    def _remove_from_status_index(self, sample_id: str, user_id: str, status: str) -> None:
        bucket = self._status_index.get(user_id, {}).get(status)
        if not bucket:
            return
        try:
            bucket.remove(sample_id)
        except ValueError:
            return

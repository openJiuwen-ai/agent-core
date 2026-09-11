# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared async trajectory-sample store contract and in-memory backend."""

from __future__ import annotations

import asyncio
import copy
from collections.abc import Sequence
from typing import Any, Protocol


class TrajectorySampleStore(Protocol):
    """Stateful queue for scored RL samples waiting for training."""

    async def save_sample(self, sample: dict[str, Any], *, user_id: str = "online") -> None:
        """Save a sample as pending for ``user_id``."""

    async def save_samples_once(
        self,
        samples: Sequence[dict[str, Any]],
        *,
        user_id: str = "online",
    ) -> set[str]:
        """Atomically publish samples not already present and return their IDs."""

    async def get_pending_count(self, user_id: str) -> int:
        """Return pending sample count for ``user_id``."""

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

    async def delete_sample(self, sample_id: str, *, force: bool = False) -> bool:
        """Delete a pending sample and return whether it existed."""


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

    async def save_samples_once(
        self,
        samples: Sequence[dict[str, Any]],
        *,
        user_id: str = "online",
    ) -> set[str]:
        prepared: list[dict[str, Any]] = []
        for sample in samples:
            sample_id = str(sample.get("sample_id") or "").strip()
            if not sample_id:
                raise ValueError("sample_id is required")
            normalized = copy.deepcopy(sample)
            normalized["user_id"] = str(normalized.get("user_id") or user_id or "online")
            normalized["_store_status"] = "pending"
            prepared.append(normalized)

        async with self._lock:
            saved_ids: set[str] = set()
            for normalized in prepared:
                sample_id = str(normalized["sample_id"])
                if sample_id in self._samples:
                    continue
                self._samples[sample_id] = normalized
                self._add_to_status_index(sample_id, normalized["user_id"], "pending")
                saved_ids.add(sample_id)
            return saved_ids

    async def get_pending_count(self, user_id: str) -> int:
        async with self._lock:
            return len(self._status_index.get(user_id, {}).get("pending", []))

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

    async def get_sample(self, sample_id: str) -> dict[str, Any] | None:
        async with self._lock:
            sample = self._samples.get(sample_id)
            return copy.deepcopy(sample) if sample is not None else None

    async def list_samples(
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
                    for sample_id in self._status_index.get(uid, {}).get(item_status, []):
                        sample = self._samples.get(sample_id)
                        if sample is None:
                            continue
                        out.append(copy.deepcopy(sample))
                        if len(out) >= max(1, int(limit)):
                            return out
            return out

    async def patch_sample(self, sample_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
        async with self._lock:
            sample = self._samples.get(sample_id)
            if sample is None:
                return None
            old_status = str(sample.get("_store_status") or "pending")
            new_status = str(updates.get("status") or old_status)
            if new_status != old_status:
                user_id = str(sample.get("user_id") or "online")
                self._remove_from_status_index(sample_id, user_id, old_status)
                self._add_to_status_index(sample_id, user_id, new_status)
                sample["_store_status"] = new_status
            for key in ("reward", "judge", "metadata", "policy_version", "source"):
                if key in updates:
                    sample[key] = copy.deepcopy(updates[key])
            return copy.deepcopy(sample)

    async def delete_sample(self, sample_id: str, *, force: bool = False) -> bool:
        del force
        async with self._lock:
            sample = self._samples.pop(sample_id, None)
            if sample is None:
                return False
            self._remove_from_status_index(
                sample_id,
                str(sample.get("user_id") or "online"),
                str(sample.get("_store_status") or "pending"),
            )
            return True

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

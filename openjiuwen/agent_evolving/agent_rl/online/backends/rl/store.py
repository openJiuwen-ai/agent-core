# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared async trajectory-sample store contract and in-memory backend."""

from __future__ import annotations

from typing import Any

from .._inmemory_queue import InMemoryStatusQueue


class InMemoryTrajectoryStore:
    """Lightweight in-memory trajectory store for scored training samples."""

    def __init__(self) -> None:
        self._queue = InMemoryStatusQueue(id_field="sample_id")

    async def save_sample(self, sample: dict[str, Any], *, user_id: str = "online") -> None:
        await self._queue.save(sample, user_id=user_id)

    async def get_pending_count(self, user_id: str) -> int:
        return await self._queue.pending_count(user_id)

    async def get_users_above_threshold(self, threshold: int) -> list[str]:
        return await self._queue.users_above_threshold(threshold)

    async def fetch_and_mark_training(self, user_id: str, limit: int) -> list[dict[str, Any]]:
        return await self._queue.fetch_and_mark(user_id, limit, to_status="training")

    async def mark_trained(self, sample_ids: list[str]) -> None:
        await self._queue.update_status(sample_ids, from_status="training", to_status="trained")

    async def mark_failed(self, sample_ids: list[str]) -> None:
        await self._queue.update_status(sample_ids, from_status="training", to_status="failed")

    async def reset_to_pending(self, sample_ids: list[str]) -> None:
        await self._queue.update_status(sample_ids, from_status="training", to_status="pending")

    async def stats(self) -> dict[str, int]:
        counts = await self._queue.stats(statuses=("pending", "training", "trained", "failed"))
        pending = counts["pending"]
        training = counts["training"]
        trained = counts["trained"]
        failed = counts["failed"]
        return {
            "total_samples": pending + training + trained + failed,
            "pending_samples": pending,
            "training_samples": training,
            "trained_samples": trained,
            "failed_samples": failed,
        }

    async def get_sample(self, sample_id: str) -> dict[str, Any] | None:
        return await self._queue.get(sample_id)

    async def list_samples(
        self,
        *,
        user_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        return await self._queue.list(user_id=user_id, status=status, limit=limit)

    async def patch_sample(self, sample_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
        return await self._queue.patch(
            sample_id,
            updates,
            allowed_keys=("reward", "judge", "metadata", "policy_version", "source"),
        )

    async def delete_sample(self, sample_id: str, *, force: bool = False) -> bool:
        return await self._queue.delete(sample_id, force=force)

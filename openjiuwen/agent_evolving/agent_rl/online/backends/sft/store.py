# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Shared async store contract for SFT raw trajectories and SFT samples."""

from __future__ import annotations

from typing import Any, Protocol

from .._inmemory_queue import InMemoryStatusQueue


class SFTSampleStore(Protocol):
    """Queue store for ``sft-raw-v1`` inputs and ``sft-sample-v1`` training data."""

    async def save_raw(self, raw: dict[str, Any], *, user_id: str = "online") -> None:
        """Save a raw trajectory as pending rollout input."""

    async def save_sample(self, sample: dict[str, Any], *, user_id: str = "online") -> None:
        """Save an SFT sample as pending training input."""

    async def get_pending_raw_count(self, user_id: str) -> int:
        """Return pending raw trajectory count for ``user_id``."""

    async def get_pending_sample_count(self, user_id: str) -> int:
        """Return pending SFT sample count for ``user_id``."""

    async def get_raw_users_above_threshold(self, threshold: int) -> list[str]:
        """Return users whose pending raw trajectory count reaches ``threshold``."""

    async def get_sample_users_above_threshold(self, threshold: int) -> list[str]:
        """Return users whose pending SFT sample count reaches ``threshold``."""

    async def fetch_raw_and_mark_processing(self, user_id: str, limit: int) -> list[dict[str, Any]]:
        """Move pending raw trajectories to processing and return them."""

    async def fetch_samples_and_mark_training(self, user_id: str, limit: int) -> list[dict[str, Any]]:
        """Move pending SFT samples to training and return them."""

    async def mark_raw_processed(self, raw_ids: list[str]) -> None:
        """Mark raw trajectories as processed."""

    async def mark_raw_failed(self, raw_ids: list[str]) -> None:
        """Mark raw trajectories as failed."""

    async def mark_samples_trained(self, sample_ids: list[str]) -> None:
        """Mark SFT samples as trained."""

    async def mark_samples_failed(self, sample_ids: list[str]) -> None:
        """Mark SFT samples as failed."""

    async def stats(self) -> dict[str, int]:
        """Return aggregate raw/sample counters."""


class InMemorySFTStore:
    """In-memory SFT store used by unit tests and embedded dry-run flows."""

    def __init__(self) -> None:
        self._raw = InMemoryStatusQueue(id_field="raw_id")
        self._samples = InMemoryStatusQueue(id_field="sample_id")

    async def save_raw(self, raw: dict[str, Any], *, user_id: str = "online") -> None:
        payload = dict(raw)
        raw_id = str(payload.get("raw_id") or payload.get("trajectory_id") or payload.get("sample_id") or "").strip()
        if not raw_id:
            raise ValueError("raw_id is required")
        payload.setdefault("raw_id", raw_id)
        payload.setdefault("sample_id", raw_id)
        await self._raw.save(payload, user_id=user_id, initial_status="pending")

    async def save_sample(self, sample: dict[str, Any], *, user_id: str = "online") -> None:
        payload = dict(sample)
        sample_id = str(payload.get("sample_id") or "").strip()
        if not sample_id:
            raise ValueError("sample_id is required")
        payload.setdefault("sample_id", sample_id)
        await self._samples.save(payload, user_id=user_id, initial_status="pending")

    async def get_pending_raw_count(self, user_id: str) -> int:
        return await self._raw.pending_count(user_id)

    async def get_pending_sample_count(self, user_id: str) -> int:
        return await self._samples.pending_count(user_id)

    async def get_raw_users_above_threshold(self, threshold: int) -> list[str]:
        return await self._raw.users_above_threshold(threshold)

    async def get_sample_users_above_threshold(self, threshold: int) -> list[str]:
        return await self._samples.users_above_threshold(threshold)

    async def fetch_raw_and_mark_processing(self, user_id: str, limit: int) -> list[dict[str, Any]]:
        return await self._raw.fetch_and_mark(user_id, limit, to_status="processing")

    async def fetch_samples_and_mark_training(self, user_id: str, limit: int) -> list[dict[str, Any]]:
        return await self._samples.fetch_and_mark(user_id, limit, to_status="training")

    async def mark_raw_processed(self, raw_ids: list[str]) -> None:
        await self._raw.update_status(raw_ids, from_status="processing", to_status="processed")

    async def mark_raw_failed(self, raw_ids: list[str]) -> None:
        await self._raw.update_status(raw_ids, from_status="processing", to_status="failed")

    async def mark_samples_trained(self, sample_ids: list[str]) -> None:
        await self._samples.update_status(sample_ids, from_status="training", to_status="trained")

    async def mark_samples_failed(self, sample_ids: list[str]) -> None:
        await self._samples.update_status(sample_ids, from_status="training", to_status="failed")

    async def stats(self) -> dict[str, int]:
        raw_stats = await self._raw.stats(statuses=("pending", "processing", "processed", "failed"))
        sample_stats = await self._samples.stats(statuses=("pending", "training", "trained", "failed"))
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

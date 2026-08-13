# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Redis-backed SFT store with isolated raw/sample namespaces."""

from __future__ import annotations

from typing import Any

from redis.asyncio import Redis

from openjiuwen.agent_evolving.agent_rl.online.backends.rl.redis_store import RedisTrajectoryStore

SFT_RAW_PROTOCOL_VERSION = "sft-raw-v1"
SFT_SAMPLE_PROTOCOL_VERSION = "sft-sample-v1"

_RAW_PREFIX = "rl:sft_raw"
_RAW_IDX_PREFIX = "rl:sft_raw_idx"
_RAW_USERS_KEY = "rl:sft_raw_users"
_SAMPLE_PREFIX = "rl:sft_sample"
_SAMPLE_IDX_PREFIX = "rl:sft_sample_idx"
_SAMPLE_USERS_KEY = "rl:sft_sample_users"


class RedisSFTStore:
    """Expose raw trajectory and SFT sample queues on top of RedisTrajectoryStore."""

    def __init__(self, redis: Redis) -> None:
        self._raw_store = RedisTrajectoryStore(
            redis,
            key_prefix=_RAW_PREFIX,
            idx_prefix=_RAW_IDX_PREFIX,
            users_set_key=_RAW_USERS_KEY,
        )
        self._sample_store = RedisTrajectoryStore(
            redis,
            key_prefix=_SAMPLE_PREFIX,
            idx_prefix=_SAMPLE_IDX_PREFIX,
            users_set_key=_SAMPLE_USERS_KEY,
        )

    async def save_raw(self, raw: dict[str, Any], *, user_id: str = "online") -> None:
        payload = dict(raw)
        raw_id = str(payload.get("raw_id") or payload.get("trajectory_id") or payload.get("sample_id") or "").strip()
        if not raw_id:
            raise ValueError("raw_id is required")
        payload.setdefault("raw_id", raw_id)
        payload.setdefault("sample_id", raw_id)
        payload.setdefault("protocol_version", SFT_RAW_PROTOCOL_VERSION)
        await self._raw_store.save_sample(payload, user_id=user_id)

    async def save_sample(self, sample: dict[str, Any], *, user_id: str = "online") -> None:
        payload = dict(sample)
        sample_id = str(payload.get("sample_id") or "").strip()
        if not sample_id:
            raise ValueError("sample_id is required")
        payload.setdefault("protocol_version", SFT_SAMPLE_PROTOCOL_VERSION)
        await self._sample_store.save_sample(payload, user_id=user_id)

    async def get_pending_raw_count(self, user_id: str) -> int:
        return await self._raw_store.get_pending_count(user_id)

    async def get_pending_sample_count(self, user_id: str) -> int:
        return await self._sample_store.get_pending_count(user_id)

    async def get_raw_users_above_threshold(self, threshold: int) -> list[str]:
        return await self._raw_store.get_users_above_threshold(threshold)

    async def get_sample_users_above_threshold(self, threshold: int) -> list[str]:
        return await self._sample_store.get_users_above_threshold(threshold)

    async def fetch_raw_and_mark_processing(self, user_id: str, limit: int) -> list[dict[str, Any]]:
        return await self._raw_store.fetch_and_mark_processing(user_id, limit)

    async def fetch_samples_and_mark_training(self, user_id: str, limit: int) -> list[dict[str, Any]]:
        return await self._sample_store.fetch_and_mark_training(user_id, limit)

    async def list_samples(
        self,
        *,
        user_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        return await self._sample_store.list_samples(user_id=user_id, status=status, limit=limit)

    async def list_raw(
        self,
        *,
        user_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        return await self._raw_store.list_samples(user_id=user_id, status=status, limit=limit)

    async def mark_raw_processed(self, raw_ids: list[str]) -> None:
        await self._raw_store.mark_processed(raw_ids)

    async def mark_raw_failed(self, raw_ids: list[str]) -> None:
        await self._raw_store.mark_processing_failed(raw_ids)

    async def mark_samples_trained(self, sample_ids: list[str]) -> None:
        await self._sample_store.mark_trained(sample_ids)

    async def mark_samples_failed(self, sample_ids: list[str]) -> None:
        await self._sample_store.mark_failed(sample_ids)

    async def stats(self) -> dict[str, int]:
        raw_stats = await self._raw_store.stats()
        sample_stats = await self._sample_store.stats()
        return {
            "pending_raw": raw_stats["pending_samples"],
            "processing_raw": raw_stats["processing_samples"],
            "processed_raw": raw_stats["processed_samples"],
            "failed_raw": raw_stats["failed_samples"],
            "pending_samples": sample_stats["pending_samples"],
            "training_samples": sample_stats["training_samples"],
            "trained_samples": sample_stats["trained_samples"],
            "failed_samples": sample_stats["failed_samples"],
        }

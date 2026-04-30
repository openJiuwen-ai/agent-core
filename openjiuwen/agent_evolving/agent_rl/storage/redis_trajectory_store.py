# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Redis-backed shared trajectory store for scored RL training samples."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from redis.asyncio import Redis

_KEY_PREFIX = "rl:traj"
_IDX_PREFIX = "rl:traj_idx"
_USERS_SET_KEY = "rl:traj_users"

logger = logging.getLogger(__name__)

_LUA_FETCH_AND_MARK = """
local pending_key   = KEYS[1]
local training_key  = KEYS[2]
local limit         = tonumber(ARGV[1])
local now_score     = tonumber(ARGV[2])
local new_status    = ARGV[3]
local traj_prefix   = ARGV[4]

local ids = redis.call('ZRANGE', pending_key, 0, limit - 1)
if #ids == 0 then return {} end

redis.call('ZREM', pending_key, unpack(ids))
for _, id in ipairs(ids) do
    redis.call('ZADD', training_key, now_score, id)
    redis.call('HSET', traj_prefix .. id, 'status', new_status)
end
return ids
"""


def _traj_key(sample_id: str) -> str:
    return f"{_KEY_PREFIX}:{sample_id}"


def _idx_key(user_id: str, status: str) -> str:
    return f"{_IDX_PREFIX}:{user_id}:{status}"


def _epoch(dt: datetime) -> float:
    return dt.timestamp()


class RedisTrajectoryStore:
    """Async Redis store keyed by training sample id."""

    def __init__(self, redis: Redis) -> None:
        self._r = redis
        self._fetch_script = self._r.register_script(_LUA_FETCH_AND_MARK)

    async def save_sample(self, sample: dict[str, Any], *, user_id: str = "online") -> None:
        sample_id = str(sample.get("sample_id") or "").strip()
        if not sample_id:
            raise ValueError("sample_id is required")

        normalized = dict(sample)
        normalized_user_id = str(normalized.get("user_id") or user_id or "online")
        normalized["user_id"] = normalized_user_id
        normalized["_store_status"] = "pending"

        created_at = str(normalized.get("created_at") or datetime.now(timezone.utc).isoformat())
        session_id = str(normalized.get("session_id") or "default")
        payload = json.dumps(normalized, ensure_ascii=False)
        score = _epoch(datetime.fromisoformat(created_at.replace("Z", "+00:00")))
        existing_user_id, existing_status = await self._r.hmget(_traj_key(sample_id), ["user_id", "status"])
        if isinstance(existing_user_id, bytes):
            existing_user_id = existing_user_id.decode()
        if isinstance(existing_status, bytes):
            existing_status = existing_status.decode()

        pipe = self._r.pipeline()
        if existing_user_id and existing_status:
            pipe.zrem(_idx_key(existing_user_id, existing_status), sample_id)
        pipe.hset(
            _traj_key(sample_id),
            mapping={
                "sample_id": sample_id,
                "user_id": normalized_user_id,
                "session_id": session_id,
                "created_at": created_at,
                "status": "pending",
                "sample_json": payload,
            },
        )
        pipe.zadd(_idx_key(normalized_user_id, "pending"), {sample_id: score})
        pipe.sadd(_USERS_SET_KEY, normalized_user_id)
        await pipe.execute()

    async def get_pending_count(self, user_id: str) -> int:
        return int(await self._r.zcard(_idx_key(user_id, "pending")) or 0)

    async def get_users_above_threshold(self, threshold: int) -> list[str]:
        members = await self._r.smembers(_USERS_SET_KEY)
        if not members:
            return []

        user_ids = [m.decode() if isinstance(m, bytes) else m for m in members]
        pipe = self._r.pipeline()
        for uid in user_ids:
            pipe.zcard(_idx_key(uid, "pending"))
            pipe.zcard(_idx_key(uid, "training"))
            pipe.zcard(_idx_key(uid, "trained"))
            pipe.zcard(_idx_key(uid, "failed"))
        counts = await pipe.execute()

        result: list[str] = []
        stale: list[str] = []
        for offset, uid in enumerate(user_ids):
            pending = int(counts[offset * 4] or 0)
            training = int(counts[offset * 4 + 1] or 0)
            trained = int(counts[offset * 4 + 2] or 0)
            failed = int(counts[offset * 4 + 3] or 0)
            if pending >= threshold:
                result.append(uid)
            elif pending == training == trained == failed == 0:
                stale.append(uid)
        if stale:
            await self._r.srem(_USERS_SET_KEY, *stale)
        return result

    async def fetch_and_mark_training(self, user_id: str, limit: int) -> list[dict[str, Any]]:
        raw_ids = await self._fetch_script(
            keys=[_idx_key(user_id, "pending"), _idx_key(user_id, "training")],
            args=[max(1, int(limit)), _epoch(datetime.now(timezone.utc)), "training", f"{_KEY_PREFIX}:"],
        )
        if not raw_ids:
            return []

        sample_ids = [value.decode() if isinstance(value, bytes) else value for value in raw_ids]
        pipe = self._r.pipeline()
        for sample_id in sample_ids:
            pipe.hget(_traj_key(sample_id), "sample_json")
        rows = await pipe.execute()

        samples: list[dict[str, Any]] = []
        for raw in rows:
            if raw is None:
                continue
            payload = raw.decode() if isinstance(raw, bytes) else raw
            sample = json.loads(payload)
            sample["_store_status"] = "training"
            samples.append(sample)
        return samples

    async def mark_trained(self, sample_ids: list[str]) -> None:
        await self._update_status(sample_ids, from_status="training", to_status="trained")

    async def mark_failed(self, sample_ids: list[str]) -> None:
        await self._update_status(sample_ids, from_status="training", to_status="failed")

    async def reset_to_pending(self, sample_ids: list[str]) -> None:
        await self._update_status(sample_ids, from_status="training", to_status="pending")

    async def stats(self) -> dict[str, int]:
        members = await self._r.smembers(_USERS_SET_KEY)
        if not members:
            return {
                "total_samples": 0,
                "pending_samples": 0,
                "training_samples": 0,
                "trained_samples": 0,
                "failed_samples": 0,
            }

        user_ids = [m.decode() if isinstance(m, bytes) else m for m in members]
        pending = 0
        training = 0
        trained = 0
        failed = 0
        pipe = self._r.pipeline()
        for uid in user_ids:
            pipe.zcard(_idx_key(uid, "pending"))
            pipe.zcard(_idx_key(uid, "training"))
            pipe.zcard(_idx_key(uid, "trained"))
            pipe.zcard(_idx_key(uid, "failed"))
        counts = await pipe.execute()
        for offset in range(0, len(counts), 4):
            pending += int(counts[offset] or 0)
            training += int(counts[offset + 1] or 0)
            trained += int(counts[offset + 2] or 0)
            failed += int(counts[offset + 3] or 0)
        return {
            "total_samples": pending + training + trained + failed,
            "pending_samples": pending,
            "training_samples": training,
            "trained_samples": trained,
            "failed_samples": failed,
        }

    async def _update_status(self, sample_ids: list[str], *, from_status: str, to_status: str) -> None:
        if not sample_ids:
            return

        pipe = self._r.pipeline()
        for sample_id in sample_ids:
            pipe.hmget(_traj_key(sample_id), ["user_id", "sample_json"])
        rows = await pipe.execute()

        transitions: list[tuple[str, str, dict[str, Any]]] = []
        for sample_id, row in zip(sample_ids, rows):
            if not row or row[0] is None:
                continue
            user_id = row[0].decode() if isinstance(row[0], bytes) else row[0]
            payload = row[1].decode() if isinstance(row[1], bytes) else row[1]
            if payload is None:
                logger.warning(
                    "Skipping status transition for sample=%s due to missing sample_json; "
                    "keeping %s index unchanged",
                    sample_id,
                    from_status,
                )
                continue
            try:
                sample = json.loads(payload)
            except (json.JSONDecodeError, TypeError) as exc:
                logger.warning(
                    "Skipping status transition for sample=%s due to invalid sample_json; "
                    "keeping %s index unchanged: %s",
                    sample_id,
                    from_status,
                    exc,
                )
                continue
            sample["_store_status"] = to_status
            transitions.append((sample_id, user_id, sample))

        if not transitions:
            return

        now_score = _epoch(datetime.now(timezone.utc))
        pipe = self._r.pipeline()
        for sample_id, user_id, sample in transitions:
            pipe.zrem(_idx_key(user_id, from_status), sample_id)
            pipe.zadd(_idx_key(user_id, to_status), {sample_id: now_score})
            pipe.hset(
                _traj_key(sample_id),
                mapping={
                    "status": to_status,
                    "sample_json": json.dumps(sample, ensure_ascii=False),
                },
            )
        await pipe.execute()

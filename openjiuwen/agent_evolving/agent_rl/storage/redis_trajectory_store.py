# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Redis-backed shared trajectory store for scored RL training samples."""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any, Optional

from redis.asyncio import Redis
from redis.exceptions import WatchError

_KEY_PREFIX = "rl:v1:traj"
_IDX_PREFIX = "rl:v1:traj_idx"
_USERS_SET_KEY = "rl:v1:traj_users"

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


def trajectory_key(sample_id: str) -> str:
    """Return the canonical Redis hash key for one trajectory."""

    return f"{_KEY_PREFIX}:{sample_id}"


def trajectory_index_key(user_id: str, status: str) -> str:
    """Return the canonical Redis status-index key for one owner."""

    return f"{_IDX_PREFIX}:{user_id}:{status}"


_traj_key = trajectory_key
_idx_key = trajectory_index_key


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

    async def save_samples_once(
        self,
        samples: Sequence[dict[str, Any]],
        *,
        user_id: str = "online",
    ) -> set[str]:
        """Publish new samples together without resetting an existing sample's status."""
        prepared: list[tuple[str, str, str, str, str, float]] = []
        for sample in samples:
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
            prepared.append((sample_id, normalized_user_id, session_id, created_at, payload, score))

        if not prepared:
            return set()

        sample_keys = [_traj_key(sample_id) for sample_id, *_ in prepared]
        while True:
            async with self._r.pipeline(transaction=True) as pipe:
                try:
                    await pipe.watch(*sample_keys)
                    statuses = [await pipe.hget(key, "status") for key in sample_keys]
                    new_samples = [item for item, status in zip(prepared, statuses) if status is None]
                    if not new_samples:
                        return set()
                    pipe.multi()
                    for sample_id, normalized_user_id, session_id, created_at, payload, score in new_samples:
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
                    return {sample_id for sample_id, *_ in new_samples}
                except WatchError:
                    continue

    async def get_pending_count(self, user_id: str) -> int:
        return int(await self._r.zcard(_idx_key(user_id, "pending")) or 0)

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

    async def get_sample(self, sample_id: str) -> Optional[dict[str, Any]]:
        payload, status = await self._r.hmget(_traj_key(sample_id), ["sample_json", "status"])
        if payload is None:
            return None
        if isinstance(payload, bytes):
            payload = payload.decode()
        if isinstance(status, bytes):
            status = status.decode()
        try:
            sample = json.loads(payload)
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning("Failed to decode trajectory sample=%s: %s", sample_id, exc)
            return None
        sample["_store_status"] = str(status or sample.get("_store_status") or "pending")
        return sample

    async def list_samples(
        self,
        *,
        user_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        sample_ids = await self._list_sample_ids(user_id=user_id, status=status, limit=max(1, int(limit)))
        if not sample_ids:
            return []

        pipe = self._r.pipeline()
        for sample_id in sample_ids:
            pipe.hmget(_traj_key(sample_id), ["sample_json", "status"])
        rows = await pipe.execute()

        out: list[dict[str, Any]] = []
        for sample_id, row in zip(sample_ids, rows):
            if not row or row[0] is None:
                continue
            payload = row[0].decode() if isinstance(row[0], bytes) else row[0]
            item_status = row[1].decode() if len(row) > 1 and isinstance(row[1], bytes) else row[1]
            try:
                sample = json.loads(payload)
            except (json.JSONDecodeError, TypeError) as exc:
                logger.warning("Skipping invalid trajectory sample=%s in list: %s", sample_id, exc)
                continue
            sample["_store_status"] = str(item_status or sample.get("_store_status") or "pending")
            out.append(sample)
        return out

    async def patch_sample(self, sample_id: str, updates: dict[str, Any]) -> Optional[dict[str, Any]]:
        row = await self._r.hmget(_traj_key(sample_id), ["user_id", "status", "sample_json", "created_at"])
        if not row or row[2] is None:
            return None
        user_id = row[0].decode() if isinstance(row[0], bytes) else row[0]
        old_status = row[1].decode() if isinstance(row[1], bytes) else row[1]
        payload = row[2].decode() if isinstance(row[2], bytes) else row[2]
        created_at = row[3].decode() if isinstance(row[3], bytes) else row[3]
        sample = json.loads(payload)
        user_id = str(user_id or sample.get("user_id") or "online")
        old_status = str(old_status or sample.get("_store_status") or "pending")
        new_status = str(updates.get("status") or old_status)

        for key in ("reward", "judge", "metadata", "policy_version", "source"):
            if key in updates:
                sample[key] = updates[key]
        sample["_store_status"] = new_status
        updated_at = datetime.now(timezone.utc).isoformat()
        sample["updated_at"] = updated_at

        score = _epoch(datetime.fromisoformat(str(created_at or updated_at).replace("Z", "+00:00")))
        pipe = self._r.pipeline()
        if new_status != old_status:
            pipe.zrem(_idx_key(user_id, old_status), sample_id)
            pipe.zadd(_idx_key(user_id, new_status), {sample_id: score})
        pipe.hset(
            _traj_key(sample_id),
            mapping={
                "status": new_status,
                "sample_json": json.dumps(sample, ensure_ascii=False),
                "updated_at": updated_at,
            },
        )
        await pipe.execute()
        return sample

    async def delete_sample(self, sample_id: str, *, force: bool = False) -> bool:
        row = await self._r.hmget(_traj_key(sample_id), ["user_id", "status"])
        if not row or row[0] is None:
            return False
        user_id = row[0].decode() if isinstance(row[0], bytes) else row[0]
        status = row[1].decode() if isinstance(row[1], bytes) else row[1]
        status = str(status or "pending")
        if status == "training" and not force:
            raise RuntimeError("cannot delete training trajectory without force=true")
        pipe = self._r.pipeline()
        pipe.zrem(_idx_key(str(user_id), status), sample_id)
        pipe.delete(_traj_key(sample_id))
        await pipe.execute()
        return True

    async def management_stats(
        self,
        *,
        user_id: str | None = None,
        model_id: str | None = None,
    ) -> dict[str, Any]:
        samples = await self.list_samples(user_id=user_id, limit=1000000)
        if model_id:
            samples = [
                sample for sample in samples if str(sample.get("model") or sample.get("model_id") or "") == model_id
            ]
        by_status: dict[str, int] = {}
        by_source: dict[str, int] = {}
        for sample in samples:
            item_status = str(sample.get("_store_status") or "pending")
            by_status[item_status] = by_status.get(item_status, 0) + 1
            source = str(sample.get("source") or sample.get("mode") or "unknown")
            by_source[source] = by_source.get(source, 0) + 1
        return {
            "total": len(samples),
            "by_status": by_status,
            "by_source": by_source,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    async def _list_sample_ids(
        self,
        *,
        user_id: str | None,
        status: str | None,
        limit: int,
    ) -> list[str]:
        if user_id:
            user_ids = [user_id]
        else:
            members = await self._r.smembers(_USERS_SET_KEY)
            user_ids = sorted(m.decode() if isinstance(m, bytes) else m for m in members)

        statuses = [status] if status else ["pending", "training", "trained", "failed", "deleted"]
        out: list[str] = []
        for uid in user_ids:
            for item_status in statuses:
                raw_ids = await self._r.zrange(_idx_key(uid, item_status), 0, max(0, limit - len(out) - 1))
                for raw_id in raw_ids:
                    sample_id = raw_id.decode() if isinstance(raw_id, bytes) else raw_id
                    out.append(str(sample_id))
                    if len(out) >= limit:
                        return out
        return out

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
                    "Skipping status transition for sample=%s due to missing sample_json; keeping %s index unchanged",
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

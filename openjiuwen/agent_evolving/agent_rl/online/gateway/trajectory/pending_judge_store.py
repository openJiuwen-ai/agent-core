# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Pending delayed-judge store for rail-v1 samples."""

from __future__ import annotations

import copy
import json
import time
from typing import Any, Optional

_KEY_PREFIX = "pending_judge"
_SESSION_PREFIX = "pending_judge_session"


def _sample_key(session_id: str, trajectory_id: str, step_index: int) -> str:
    return f"{_KEY_PREFIX}:{session_id}:{trajectory_id}:{step_index}"


def _session_key(session_id: str) -> str:
    return f"{_SESSION_PREFIX}:{session_id}"


class PendingJudgeStore:
    """Store per-turn samples until follow-up feedback or session close."""

    def __init__(self, *, redis: Any, ttl_sec: int = 24 * 3600) -> None:
        if redis is None:
            raise ValueError("PendingJudgeStore requires redis client")
        self._redis = redis
        self._ttl_sec = int(ttl_sec)

    async def put(self, sample: dict[str, Any]) -> None:
        session_id = str(sample.get("session_id") or "")
        trajectory_id = str(sample.get("trajectory_id") or "")
        step_index = int(sample.get("step_index") or 0)
        key = _sample_key(session_id, trajectory_id, step_index)
        payload = copy.deepcopy(sample)
        payload["_pending_key"] = key
        payload.setdefault("_pending_created_at", time.time())
        await self._redis.set(key, json.dumps(payload, ensure_ascii=False), ex=self._ttl_sec)
        await self._redis.zadd(_session_key(session_id), {key: float(payload["_pending_created_at"])})
        await self._redis.expire(_session_key(session_id), self._ttl_sec)

    async def get_by_session(self, session_id: str) -> list[dict[str, Any]]:
        keys = await self._redis.zrange(_session_key(session_id), 0, -1)
        if not keys:
            return []
        keys = [k.decode() if isinstance(k, bytes) else k for k in keys]
        rows = await self._redis.mget(keys)
        samples: list[dict[str, Any]] = []
        for raw in rows:
            if raw is None:
                continue
            payload = raw.decode() if isinstance(raw, bytes) else raw
            samples.append(json.loads(payload))
        return samples

    async def pop_one(self, session_id: str, trajectory_id: str, step_index: int) -> Optional[dict[str, Any]]:
        key = _sample_key(session_id, trajectory_id, step_index)
        raw = await self._redis.get(key)
        pipe = self._redis.pipeline()
        pipe.delete(key)
        pipe.zrem(_session_key(session_id), key)
        await pipe.execute()
        if raw is None:
            return None
        payload = raw.decode() if isinstance(raw, bytes) else raw
        return json.loads(payload)

    async def pop_earliest(self, session_id: str) -> Optional[dict[str, Any]]:
        samples = await self.get_by_session(session_id)
        if not samples:
            return None
        first = samples[0]
        return await self.pop_one(
            session_id,
            str(first.get("trajectory_id") or ""),
            int(first.get("step_index") or 0),
        )

    async def pop_all(self, session_id: str) -> list[dict[str, Any]]:
        samples = await self.get_by_session(session_id)
        out: list[dict[str, Any]] = []
        for sample in samples:
            popped = await self.pop_one(
                session_id,
                str(sample.get("trajectory_id") or ""),
                int(sample.get("step_index") or 0),
            )
            if popped is not None:
                out.append(popped)
        return out

    @staticmethod
    def _sort_key(sample: dict[str, Any]) -> tuple[float, int]:
        return (
            float(sample.get("_pending_created_at") or 0.0),
            int(sample.get("step_index") or 0),
        )

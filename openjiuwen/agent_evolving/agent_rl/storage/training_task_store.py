# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Redis-backed store for manually triggered online-RL training tasks."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from redis.asyncio import Redis

_TASK_PREFIX = "rl:training_task"
_TASK_INDEX = "rl:training_tasks"
_ACTIVE_KEY = "rl:training_task_active"


def _task_key(task_id: str) -> str:
    return f"{_TASK_PREFIX}:{task_id}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


class TrainingTaskStore:
    """Persist a single active training task used by the online scheduler."""

    def __init__(self, redis: Redis) -> None:
        self._r = redis

    async def create_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        active = await self.get_active_task()
        if active and active.get("status") in {"pending", "running", "stopping"}:
            raise RuntimeError("an active training task already exists")

        task_id = str(payload.get("task_id") or "").strip() or f"task-{uuid.uuid4().hex[:12]}"
        existing = await self._r.exists(_task_key(task_id))
        if existing:
            raise RuntimeError(f"task_id {task_id} already exists")

        now = _now_iso()
        task = {
            "task_id": task_id,
            "status": "pending",
            "user_id": str(payload.get("user_id") or "").strip(),
            "created_at": now,
            "updated_at": now,
            "started_at": "",
            "finished_at": "",
            "error": "",
            "sample_count": int(payload.get("sample_count") or 0),
            "training_count": int(payload.get("training_count") or 0),
            "drain_pending_on_train": bool(payload.get("drain_pending_on_train", True)),
            "max_samples_per_run": int(payload.get("max_samples_per_run") or 0),
            "ppo_samples_per_step": int(payload.get("ppo_samples_per_step") or 0),
            "allow_partial_last_step": bool(payload.get("allow_partial_last_step", True)),
            "metadata": json.dumps(payload.get("metadata") or {}, ensure_ascii=False),
        }
        pipe = self._r.pipeline()
        pipe.hset(_task_key(task_id), mapping={key: str(value) for key, value in task.items()})
        pipe.zadd(_TASK_INDEX, {task_id: datetime.now(timezone.utc).timestamp()})
        pipe.set(_ACTIVE_KEY, task_id)
        await pipe.execute()
        return await self.get_task(task_id) or task

    async def list_tasks(self, *, limit: int = 100) -> list[dict[str, Any]]:
        task_ids = await self._r.zrevrange(_TASK_INDEX, 0, max(0, int(limit) - 1))
        task_ids = [task_id.decode() if isinstance(task_id, bytes) else str(task_id) for task_id in task_ids]
        out: list[dict[str, Any]] = []
        for task_id in task_ids:
            task = await self.get_task(task_id)
            if task is not None:
                out.append(task)
        return out

    async def get_active_task(self) -> Optional[dict[str, Any]]:
        task_id = await self._r.get(_ACTIVE_KEY)
        if not task_id:
            return None
        if isinstance(task_id, bytes):
            task_id = task_id.decode()
        return await self.get_task(str(task_id))

    async def get_task(self, task_id: str) -> Optional[dict[str, Any]]:
        row = await self._r.hgetall(_task_key(task_id))
        if not row:
            return None
        out: dict[str, Any] = {}
        for key, value in row.items():
            key_str = key.decode() if isinstance(key, bytes) else str(key)
            value_str = value.decode() if isinstance(value, bytes) else str(value)
            out[key_str] = value_str
        out["sample_count"] = int(out.get("sample_count") or 0)
        out["training_count"] = int(out.get("training_count") or 0)
        out["drain_pending_on_train"] = _truthy(out.get("drain_pending_on_train"))
        out["max_samples_per_run"] = int(out.get("max_samples_per_run") or 0)
        out["ppo_samples_per_step"] = int(out.get("ppo_samples_per_step") or 0)
        out["allow_partial_last_step"] = _truthy(out.get("allow_partial_last_step"))
        try:
            out["metadata"] = json.loads(out.get("metadata") or "{}")
        except Exception:
            out["metadata"] = {}
        return out

    async def claim_pending_task(self, *, user_id: str | None, sample_count: int) -> Optional[dict[str, Any]]:
        task = await self.get_active_task()
        if task is None or task.get("status") != "pending":
            return None
        if user_id and task.get("user_id") and str(task["user_id"]) not in {"", user_id}:
            return None
        now = _now_iso()
        await self._r.hset(
            _task_key(task["task_id"]),
            mapping={
                "status": "running",
                "started_at": now,
                "updated_at": now,
                "sample_count": str(int(sample_count)),
                "user_id": str(user_id or task.get("user_id") or ""),
            },
        )
        task["status"] = "running"
        task["started_at"] = now
        task["updated_at"] = now
        task["sample_count"] = int(sample_count)
        task["user_id"] = str(user_id or task.get("user_id") or "")
        return task

    async def update_task_status(
        self,
        task_id: str,
        *,
        status: str,
        error: str = "",
    ) -> Optional[dict[str, Any]]:
        task = await self.get_task(task_id)
        if task is None:
            return None
        payload = {
            "status": status,
            "updated_at": _now_iso(),
            "error": error,
        }
        if status in {"succeeded", "failed", "canceled"}:
            payload["finished_at"] = _now_iso()
        await self._r.hset(_task_key(task_id), mapping={key: str(value) for key, value in payload.items()})
        updated = await self.get_task(task_id)
        if status in {"succeeded", "failed", "canceled"}:
            active_task_id = await self._r.get(_ACTIVE_KEY)
            if isinstance(active_task_id, bytes):
                active_task_id = active_task_id.decode()
            if active_task_id == task_id:
                await self._r.delete(_ACTIVE_KEY)
        return updated

    async def request_stop(self, task_id: str) -> Optional[dict[str, Any]]:
        task = await self.get_task(task_id)
        if task is None:
            return None
        status = str(task.get("status") or "pending")
        if status == "pending":
            return await self.update_task_status(task_id, status="canceled")
        if status == "running":
            return await self.update_task_status(task_id, status="stopping")
        return task

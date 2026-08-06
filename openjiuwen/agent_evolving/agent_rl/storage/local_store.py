# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""File-backed online RL stores for single-node deployments without Redis."""

from __future__ import annotations

import copy
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional, TypeVar

from filelock import FileLock

_STATE_FILENAME = "online_rl_store.json"
_LOCK_FILENAME = "online_rl_store.lock"
_PENDING_JUDGE_TTL_SEC = 24 * 3600
logger = logging.getLogger(__name__)

T = TypeVar("T")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sort_sample_key(sample: dict[str, Any]) -> tuple[str, str]:
    return (
        str(sample.get("created_at") or sample.get("updated_at") or ""),
        str(sample.get("sample_id") or ""),
    )


def _sample_key(session_id: str, trajectory_id: str, step_index: int) -> str:
    return f"pending_judge:{session_id}:{trajectory_id}:{step_index}"


class _JsonStateStore:
    """Small locked JSON database shared by gateway and scheduler processes."""

    def __init__(self, root_dir: str | os.PathLike[str]) -> None:
        self.root_dir = Path(root_dir)
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self._state_path = self.root_dir / _STATE_FILENAME
        self._lock = FileLock(str(self.root_dir / _LOCK_FILENAME))

    async def transact(self, fn: Callable[[dict[str, Any]], T]) -> T:
        import asyncio

        return await asyncio.to_thread(self._transact_sync, fn)

    def _transact_sync(self, fn: Callable[[dict[str, Any]], T]) -> T:
        with self._lock:
            state = self._load_state()
            result = fn(state)
            self._write_state(state)
            return result

    def _load_state(self) -> dict[str, Any]:
        if not self._state_path.exists():
            return self._empty_state()
        try:
            raw = json.loads(self._state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            backup_path = self._backup_state_file()
            logger.warning(
                "Corrupted JSON state file %s moved to %s; starting with empty state",
                self._state_path,
                backup_path,
                exc_info=True,
            )
            return self._empty_state()
        except OSError:
            backup_path = self._backup_state_file()
            logger.warning(
                "Unreadable JSON state file %s moved to %s; starting with empty state",
                self._state_path,
                backup_path,
                exc_info=True,
            )
            return self._empty_state()
        state = self._empty_state()
        for key, value in raw.items():
            if key in state and isinstance(value, type(state[key])):
                state[key] = value
        return state

    def _backup_state_file(self) -> Optional[Path]:
        if not self._state_path.exists():
            return None
        backup_path = self._state_path.with_suffix(f"{self._state_path.suffix}.bak")
        if backup_path.exists():
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
            backup_path = self._state_path.with_suffix(f"{self._state_path.suffix}.{timestamp}.bak")
        try:
            os.replace(self._state_path, backup_path)
        except OSError:
            logger.exception("Failed to back up JSON state file %s to %s", self._state_path, backup_path)
            raise
        return backup_path

    def _write_state(self, state: dict[str, Any]) -> None:
        tmp_path = self._state_path.with_suffix(".json.tmp")
        tmp_path.write_text(
            json.dumps(state, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(tmp_path, self._state_path)

    @staticmethod
    def _empty_state() -> dict[str, Any]:
        return {
            "samples": {},
            "tasks": {},
            "task_order": [],
            "active_task_id": "",
            "pending_judge": {},
            "pending_judge_sessions": {},
        }


class LocalTrajectoryStore:
    """Local file-backed trajectory queue with the RedisTrajectoryStore API."""

    def __init__(self, root_dir: str | os.PathLike[str]) -> None:
        self._state = _JsonStateStore(root_dir)

    async def save_sample(self, sample: dict[str, Any], *, user_id: str = "online") -> None:
        sample_id = str(sample.get("sample_id") or "").strip()
        if not sample_id:
            raise ValueError("sample_id is required")

        normalized = copy.deepcopy(sample)
        normalized_user_id = str(normalized.get("user_id") or user_id or "online")
        normalized["user_id"] = normalized_user_id
        normalized["_store_status"] = "pending"
        normalized.setdefault("created_at", _now_iso())

        def _save(state: dict[str, Any]) -> None:
            state["samples"][sample_id] = normalized

        await self._state.transact(_save)

    async def get_pending_count(self, user_id: str) -> int:
        def _count(state: dict[str, Any]) -> int:
            count = 0
            for sample in state["samples"].values():
                if str(sample.get("user_id") or "") != user_id:
                    continue
                if str(sample.get("_store_status") or "pending") != "pending":
                    continue
                count += 1
            return count

        return await self._state.transact(_count)

    async def get_users_above_threshold(self, threshold: int) -> list[str]:
        def _users(state: dict[str, Any]) -> list[str]:
            counts: dict[str, int] = {}
            for sample in state["samples"].values():
                if str(sample.get("_store_status") or "pending") != "pending":
                    continue
                user_id = str(sample.get("user_id") or "online")
                counts[user_id] = counts.get(user_id, 0) + 1
            return sorted(user_id for user_id, count in counts.items() if count >= threshold)

        return await self._state.transact(_users)

    async def fetch_and_mark_training(self, user_id: str, limit: int) -> list[dict[str, Any]]:
        normalized_limit = max(1, int(limit))

        def _fetch(state: dict[str, Any]) -> list[dict[str, Any]]:
            candidates: list[dict[str, Any]] = []
            for sample in state["samples"].values():
                if str(sample.get("user_id") or "") != user_id:
                    continue
                if str(sample.get("_store_status") or "pending") != "pending":
                    continue
                candidates.append(sample)
            candidates.sort(key=_sort_sample_key)
            selected = candidates[:normalized_limit]
            out: list[dict[str, Any]] = []
            for sample in selected:
                sample_id = str(sample.get("sample_id") or "")
                stored = state["samples"].get(sample_id)
                if stored is None:
                    continue
                stored["_store_status"] = "training"
                stored["updated_at"] = _now_iso()
                out.append(copy.deepcopy(stored))
            return out

        return await self._state.transact(_fetch)

    async def mark_trained(self, sample_ids: list[str]) -> None:
        await self._update_status(sample_ids, from_status="training", to_status="trained")

    async def mark_failed(self, sample_ids: list[str]) -> None:
        await self._update_status(sample_ids, from_status="training", to_status="failed")

    async def reset_to_pending(self, sample_ids: list[str]) -> None:
        await self._update_status(sample_ids, from_status="training", to_status="pending")

    async def stats(self) -> dict[str, int]:
        def _stats(state: dict[str, Any]) -> dict[str, int]:
            counts = {"pending": 0, "training": 0, "trained": 0, "failed": 0}
            for sample in state["samples"].values():
                status = str(sample.get("_store_status") or "pending")
                if status in counts:
                    counts[status] += 1
            return {
                "total_samples": sum(counts.values()),
                "pending_samples": counts["pending"],
                "training_samples": counts["training"],
                "trained_samples": counts["trained"],
                "failed_samples": counts["failed"],
            }

        return await self._state.transact(_stats)

    async def get_sample(self, sample_id: str) -> Optional[dict[str, Any]]:
        def _get(state: dict[str, Any]) -> Optional[dict[str, Any]]:
            sample = state["samples"].get(sample_id)
            return copy.deepcopy(sample) if sample is not None else None

        return await self._state.transact(_get)

    async def list_samples(
        self,
        *,
        user_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        normalized_limit = max(1, int(limit))

        def _list(state: dict[str, Any]) -> list[dict[str, Any]]:
            samples = list(state["samples"].values())
            samples.sort(key=_sort_sample_key)
            out: list[dict[str, Any]] = []
            for sample in samples:
                if user_id and str(sample.get("user_id") or "") != user_id:
                    continue
                if status and str(sample.get("_store_status") or "pending") != status:
                    continue
                out.append(copy.deepcopy(sample))
                if len(out) >= normalized_limit:
                    break
            return out

        return await self._state.transact(_list)

    async def patch_sample(self, sample_id: str, updates: dict[str, Any]) -> Optional[dict[str, Any]]:
        def _patch(state: dict[str, Any]) -> Optional[dict[str, Any]]:
            sample = state["samples"].get(sample_id)
            if sample is None:
                return None
            old_status = str(sample.get("_store_status") or "pending")
            new_status = str(updates.get("status") or old_status)
            if old_status == "training" and new_status == "deleted" and not bool(updates.get("force")):
                raise RuntimeError("cannot delete training trajectory without force=true")
            for key in ("reward", "judge", "metadata", "policy_version", "source"):
                if key in updates:
                    sample[key] = copy.deepcopy(updates[key])
            sample["_store_status"] = new_status
            sample["updated_at"] = _now_iso()
            return copy.deepcopy(sample)

        return await self._state.transact(_patch)

    async def delete_sample(self, sample_id: str, *, force: bool = False) -> bool:
        def _delete(state: dict[str, Any]) -> bool:
            sample = state["samples"].get(sample_id)
            if sample is None:
                return False
            if str(sample.get("_store_status") or "pending") == "training" and not force:
                raise RuntimeError("cannot delete training trajectory without force=true")
            del state["samples"][sample_id]
            return True

        return await self._state.transact(_delete)

    async def management_stats(
        self,
        *,
        user_id: str | None = None,
        model_id: str | None = None,
    ) -> dict[str, Any]:
        def _stats(state: dict[str, Any]) -> dict[str, Any]:
            by_status: dict[str, int] = {}
            by_source: dict[str, int] = {}
            total = 0
            for sample in state["samples"].values():
                if user_id and str(sample.get("user_id") or "") != user_id:
                    continue
                if model_id and str(sample.get("model") or sample.get("model_id") or "") != model_id:
                    continue
                total += 1
                status = str(sample.get("_store_status") or "pending")
                by_status[status] = by_status.get(status, 0) + 1
                source = str(sample.get("source") or sample.get("mode") or "unknown")
                by_source[source] = by_source.get(source, 0) + 1
            return {
                "total": total,
                "by_status": by_status,
                "by_source": by_source,
                "updated_at": _now_iso(),
            }

        return await self._state.transact(_stats)

    async def _update_status(self, sample_ids: list[str], *, from_status: str, to_status: str) -> None:
        if not sample_ids:
            return

        def _update(state: dict[str, Any]) -> None:
            for sample_id in sample_ids:
                sample = state["samples"].get(sample_id)
                if sample is None:
                    continue
                if str(sample.get("_store_status") or "pending") != from_status:
                    continue
                sample["_store_status"] = to_status
                sample["updated_at"] = _now_iso()

        await self._state.transact(_update)


class LocalTrainingTaskStore:
    """Local file-backed training task store with single-active-task semantics."""

    def __init__(self, root_dir: str | os.PathLike[str]) -> None:
        self._state = _JsonStateStore(root_dir)

    async def create_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        def _create(state: dict[str, Any]) -> dict[str, Any]:
            active_task_id = str(state.get("active_task_id") or "")
            active = state["tasks"].get(active_task_id) if active_task_id else None
            if active and active.get("status") in {"pending", "running", "stopping"}:
                raise RuntimeError("an active training task already exists")

            task_id = str(payload.get("task_id") or "").strip() or f"task-{uuid.uuid4().hex[:12]}"
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
                "metadata": copy.deepcopy(payload.get("metadata") or {}),
            }
            state["tasks"][task_id] = task
            order = [item for item in state.get("task_order", []) if item != task_id]
            order.append(task_id)
            state["task_order"] = order
            state["active_task_id"] = task_id
            return copy.deepcopy(task)

        return await self._state.transact(_create)

    async def list_tasks(self, *, limit: int = 100) -> list[dict[str, Any]]:
        normalized_limit = max(1, int(limit))

        def _list(state: dict[str, Any]) -> list[dict[str, Any]]:
            out = []
            for task_id in reversed(state.get("task_order", [])):
                task = state["tasks"].get(task_id)
                if task is not None:
                    out.append(copy.deepcopy(task))
                if len(out) >= normalized_limit:
                    break
            return out

        return await self._state.transact(_list)

    async def get_active_task(self) -> Optional[dict[str, Any]]:
        def _get(state: dict[str, Any]) -> Optional[dict[str, Any]]:
            task_id = str(state.get("active_task_id") or "")
            task = state["tasks"].get(task_id) if task_id else None
            return copy.deepcopy(task) if task is not None else None

        return await self._state.transact(_get)

    async def get_task(self, task_id: str) -> Optional[dict[str, Any]]:
        def _get(state: dict[str, Any]) -> Optional[dict[str, Any]]:
            task = state["tasks"].get(task_id)
            return copy.deepcopy(task) if task is not None else None

        return await self._state.transact(_get)

    async def claim_pending_task(self, *, user_id: str | None, sample_count: int) -> Optional[dict[str, Any]]:
        def _claim(state: dict[str, Any]) -> Optional[dict[str, Any]]:
            task_id = str(state.get("active_task_id") or "")
            task = state["tasks"].get(task_id) if task_id else None
            if task is None or task.get("status") != "pending":
                return None
            if user_id and task.get("user_id") and str(task["user_id"]) not in {"", user_id}:
                return None
            now = _now_iso()
            task["status"] = "running"
            task["started_at"] = now
            task["updated_at"] = now
            task["sample_count"] = int(sample_count)
            task["user_id"] = str(user_id or task.get("user_id") or "")
            return copy.deepcopy(task)

        return await self._state.transact(_claim)

    async def update_task_status(
        self,
        task_id: str,
        *,
        status: str,
        error: str = "",
    ) -> Optional[dict[str, Any]]:
        def _update(state: dict[str, Any]) -> Optional[dict[str, Any]]:
            task = state["tasks"].get(task_id)
            if task is None:
                return None
            task["status"] = status
            task["updated_at"] = _now_iso()
            task["error"] = error
            if status in {"succeeded", "failed", "canceled"}:
                task["finished_at"] = _now_iso()
                if str(state.get("active_task_id") or "") == task_id:
                    state["active_task_id"] = ""
            return copy.deepcopy(task)

        return await self._state.transact(_update)

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


class LocalPendingJudgeStore:
    """Local file-backed replacement for PendingJudgeStore."""

    def __init__(self, root_dir: str | os.PathLike[str], *, ttl_sec: int = _PENDING_JUDGE_TTL_SEC) -> None:
        self._state = _JsonStateStore(root_dir)
        self._ttl_sec = int(ttl_sec)

    async def put(self, sample: dict[str, Any]) -> None:
        session_id = str(sample.get("session_id") or "")
        trajectory_id = str(sample.get("trajectory_id") or "")
        step_index = int(sample.get("step_index") or 0)
        key = _sample_key(session_id, trajectory_id, step_index)
        payload = copy.deepcopy(sample)
        payload["_pending_key"] = key
        payload.setdefault("_pending_created_at", time.time())

        def _put(state: dict[str, Any]) -> None:
            self._purge_expired_locked(state)
            state["pending_judge"][key] = payload
            session_keys = state["pending_judge_sessions"].setdefault(session_id, [])
            if key not in session_keys:
                session_keys.append(key)

        await self._state.transact(_put)

    async def get_by_session(self, session_id: str) -> list[dict[str, Any]]:
        def _get(state: dict[str, Any]) -> list[dict[str, Any]]:
            self._purge_expired_locked(state)
            keys = state["pending_judge_sessions"].get(session_id, [])
            samples = [
                state["pending_judge"][key]
                for key in keys
                if key in state["pending_judge"]
            ]
            samples.sort(key=self._sort_key)
            return copy.deepcopy(samples)

        return await self._state.transact(_get)

    async def pop_one(self, session_id: str, trajectory_id: str, step_index: int) -> Optional[dict[str, Any]]:
        key = _sample_key(session_id, trajectory_id, step_index)

        def _pop(state: dict[str, Any]) -> Optional[dict[str, Any]]:
            self._purge_expired_locked(state)
            sample = state["pending_judge"].pop(key, None)
            keys = state["pending_judge_sessions"].get(session_id, [])
            state["pending_judge_sessions"][session_id] = [item for item in keys if item != key]
            if not state["pending_judge_sessions"][session_id]:
                state["pending_judge_sessions"].pop(session_id, None)
            return copy.deepcopy(sample) if sample is not None else None

        return await self._state.transact(_pop)

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
        def _pop_all(state: dict[str, Any]) -> list[dict[str, Any]]:
            self._purge_expired_locked(state)
            keys = list(state["pending_judge_sessions"].get(session_id, []))
            samples: list[dict[str, Any]] = []
            for key in keys:
                sample = state["pending_judge"].pop(key, None)
                if sample is not None:
                    samples.append(sample)
            state["pending_judge_sessions"].pop(session_id, None)
            samples.sort(key=self._sort_key)
            return copy.deepcopy(samples)

        return await self._state.transact(_pop_all)

    def _purge_expired_locked(self, state: dict[str, Any]) -> None:
        if self._ttl_sec <= 0:
            return
        cutoff = time.time() - self._ttl_sec
        expired = [
            key
            for key, sample in state["pending_judge"].items()
            if float(sample.get("_pending_created_at") or 0.0) < cutoff
        ]
        if not expired:
            return
        for key in expired:
            state["pending_judge"].pop(key, None)
        for session_id, keys in list(state["pending_judge_sessions"].items()):
            kept = [key for key in keys if key not in expired]
            if kept:
                state["pending_judge_sessions"][session_id] = kept
            else:
                state["pending_judge_sessions"].pop(session_id, None)

    @staticmethod
    def _sort_key(sample: dict[str, Any]) -> tuple[float, int]:
        return (
            float(sample.get("_pending_created_at") or 0.0),
            int(sample.get("step_index") or 0),
        )

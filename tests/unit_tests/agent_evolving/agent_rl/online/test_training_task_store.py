from __future__ import annotations

from collections import defaultdict
from typing import Any

import pytest


class _FakePipeline:
    def __init__(self, redis: "_FakeRedis") -> None:
        self._redis = redis
        self._ops: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def __getattr__(self, name: str):
        def _record(*args, **kwargs):
            self._ops.append((name, args, kwargs))
            return self
        return _record

    async def execute(self):
        out = []
        for name, args, kwargs in self._ops:
            out.append(await getattr(self._redis, name)(*args, **kwargs))
        self._ops.clear()
        return out


class _FakeRedis:
    def __init__(self) -> None:
        self._kv: dict[str, Any] = {}
        self._hashes: dict[str, dict[str, Any]] = defaultdict(dict)
        self._zsets: dict[str, dict[Any, float]] = defaultdict(dict)

    def pipeline(self):
        return _FakePipeline(self)

    async def set(self, key: str, value: Any) -> None:
        self._kv[key] = value

    async def get(self, key: str) -> Any:
        return self._kv.get(key)

    async def exists(self, key: str) -> int:
        return int(key in self._kv or bool(self._hashes.get(key)) or bool(self._zsets.get(key)))

    async def delete(self, key: str) -> int:
        existed = key in self._kv or key in self._hashes
        self._kv.pop(key, None)
        self._hashes.pop(key, None)
        return int(existed)

    async def hset(self, key: str, mapping: dict[str, Any]) -> int:
        self._hashes[key].update(mapping)
        return 1

    async def hgetall(self, key: str) -> dict[str, Any]:
        return dict(self._hashes.get(key, {}))

    async def zadd(self, key: str, mapping: dict[Any, float]) -> int:
        self._zsets[key].update(mapping)
        return len(mapping)

    async def zrange(self, key: str, start: int, end: int) -> list[Any]:
        members = [
            member
            for member, _ in sorted(self._zsets[key].items(), key=lambda item: (item[1], str(item[0])))
        ]
        if end == -1:
            end = len(members) - 1
        return members[start:end + 1]

    async def zrevrange(self, key: str, start: int, end: int) -> list[Any]:
        members = [
            member
            for member, _ in sorted(self._zsets[key].items(), key=lambda item: (item[1], str(item[0])), reverse=True)
        ]
        if end == -1:
            end = len(members) - 1
        return members[start:end + 1]


@pytest.mark.asyncio
async def test_training_task_store_creates_lists_and_cancels_pending_task():
    from openjiuwen.agent_evolving.agent_rl.storage.training_task_store import TrainingTaskStore

    store = TrainingTaskStore(_FakeRedis())

    task = await store.create_task({"task_id": "task-1", "user_id": "u1", "metadata": {"source": "test"}})

    assert task["task_id"] == "task-1"
    assert task["status"] == "pending"
    assert task["user_id"] == "u1"
    assert task["metadata"] == {"source": "test"}
    assert [item["task_id"] for item in await store.list_tasks(limit=10)] == ["task-1"]

    with pytest.raises(RuntimeError):
        await store.create_task({"task_id": "task-2"})

    canceled = await store.request_stop("task-1")
    assert canceled is not None
    assert canceled["status"] == "canceled"
    assert await store.get_active_task() is None


@pytest.mark.asyncio
async def test_training_task_store_lists_latest_tasks_first():
    from openjiuwen.agent_evolving.agent_rl.storage.training_task_store import TrainingTaskStore

    store = TrainingTaskStore(_FakeRedis())

    await store.create_task({"task_id": "task-1"})
    await store.update_task_status("task-1", status="canceled")
    await store.create_task({"task_id": "task-2"})
    await store.update_task_status("task-2", status="canceled")
    await store.create_task({"task_id": "task-3"})

    assert [item["task_id"] for item in await store.list_tasks(limit=2)] == ["task-3", "task-2"]


@pytest.mark.asyncio
async def test_training_task_store_rejects_existing_non_active_task_id():
    from openjiuwen.agent_evolving.agent_rl.storage.training_task_store import TrainingTaskStore

    store = TrainingTaskStore(_FakeRedis())

    await store.create_task({"task_id": "task-1", "user_id": "u1"})
    await store.update_task_status("task-1", status="canceled")

    with pytest.raises(RuntimeError, match="task_id task-1 already exists"):
        await store.create_task({"task_id": "task-1", "user_id": "u2"})

    task = await store.get_task("task-1")
    assert task is not None
    assert task["status"] == "canceled"
    assert task["user_id"] == "u1"

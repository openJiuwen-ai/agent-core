"""单元测试：trajectory store + LoRARepository。"""

from __future__ import annotations

import asyncio
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
            fn = getattr(self._redis, name)
            out.append(await fn(*args, **kwargs))
        self._ops.clear()
        return out


class _FakeRedis:
    def __init__(self) -> None:
        self._hashes: dict[str, dict[str, Any]] = defaultdict(dict)
        self._sets: dict[str, set[Any]] = defaultdict(set)
        self._zsets: dict[str, dict[Any, float]] = defaultdict(dict)

    def pipeline(self):
        return _FakePipeline(self)

    def register_script(self, _lua: str):
        async def _script(*, keys, args):
            pending_key, training_key = keys
            limit = int(args[0])
            now_score = float(args[1])
            new_status = args[2]
            traj_prefix = args[3]
            ordered = sorted(self._zsets[pending_key].items(), key=lambda item: item[1])[:limit]
            ids = [sample_id for sample_id, _ in ordered]
            for sample_id in ids:
                self._zsets[pending_key].pop(sample_id, None)
                self._zsets[training_key][sample_id] = now_score
                self._hashes[f"{traj_prefix}{sample_id}"]["status"] = new_status
            return ids
        return _script

    async def hset(self, key: str, field: str | None = None, value: Any = None, mapping: dict[str, Any] | None = None):
        if mapping is not None:
            self._hashes[key].update(mapping)
        else:
            self._hashes[key][field] = value
        return 1

    async def hget(self, key: str, field: str):
        return self._hashes[key].get(field)

    async def hmget(self, key: str, fields: list[str]):
        return [self._hashes[key].get(field) for field in fields]

    async def zadd(self, key: str, mapping: dict[Any, float]):
        self._zsets[key].update(mapping)
        return len(mapping)

    async def zcard(self, key: str):
        return len(self._zsets[key])

    async def zrange(self, key: str, start: int, end: int):
        members = [member for member, _ in sorted(self._zsets[key].items(), key=lambda item: item[1])]
        if end == -1:
            end = len(members) - 1
        return members[start:end + 1]

    async def zrem(self, key: str, *members: Any):
        removed = 0
        for member in members:
            if member in self._zsets[key]:
                self._zsets[key].pop(member, None)
                removed += 1
        return removed

    async def sadd(self, key: str, *members: Any):
        for member in members:
            self._sets[key].add(member)
        return len(members)

    async def srem(self, key: str, *members: Any):
        removed = 0
        for member in members:
            if member in self._sets[key]:
                self._sets[key].remove(member)
                removed += 1
        return removed

    async def smembers(self, key: str):
        return set(self._sets[key])

    async def delete(self, key: str):
        existed = key in self._hashes
        self._hashes.pop(key, None)
        return int(existed)


def _sample(sample_id: str, *, user_id: str = "online") -> dict[str, Any]:
    return {
        "sample_id": sample_id,
        "user_id": user_id,
        "session_id": "sess-1",
        "created_at": "2026-01-01T00:00:00+00:00",
        "request": {"messages": [{"role": "user", "content": "hello"}]},
        "response": {"message": {"role": "assistant", "content": "world"}},
        "trajectory": {
            "input_ids": [1, 2, 3],
            "response_ids": [4, 5],
            "response_logprobs": [-0.1, -0.2],
        },
        "judge": {"score": 0.5},
    }


@pytest.mark.asyncio
async def test_inmemory_trajectory_store_status_flow():
    from openjiuwen.agent_evolving.agent_rl.storage.trajectory_store import InMemoryTrajectoryStore

    store = InMemoryTrajectoryStore()
    await store.save_sample(_sample("s1"))
    await store.save_sample(_sample("s2"))

    assert await store.get_pending_count("online") == 2
    assert await store.get_users_above_threshold(2) == ["online"]

    samples = await store.fetch_and_mark_training("online", 2)
    assert [sample["sample_id"] for sample in samples] == ["s1", "s2"]

    await store.mark_trained(["s1"])
    await store.mark_failed(["s2"])
    stats = await store.stats()
    assert stats["pending_samples"] == 0
    assert stats["trained_samples"] == 1
    assert stats["failed_samples"] == 1


@pytest.mark.asyncio
async def test_redis_trajectory_store_status_flow():
    from openjiuwen.agent_evolving.agent_rl.storage.redis_trajectory_store import RedisTrajectoryStore

    store = RedisTrajectoryStore(_FakeRedis())
    await store.save_sample(_sample("s1"))
    await store.save_sample(_sample("s2"))

    assert await store.get_pending_count("online") == 2
    assert await store.get_users_above_threshold(2) == ["online"]

    samples = await store.fetch_and_mark_training("online", 2)
    assert [sample["sample_id"] for sample in samples] == ["s1", "s2"]

    await store.mark_trained(["s1"])
    await store.reset_to_pending(["s2"])
    stats = await store.stats()
    assert stats["pending_samples"] == 1
    assert stats["trained_samples"] == 1
    assert stats["failed_samples"] == 0


@pytest.mark.asyncio
async def test_redis_trajectory_store_save_sample_replaces_old_status_index():
    from openjiuwen.agent_evolving.agent_rl.storage.redis_trajectory_store import RedisTrajectoryStore

    redis = _FakeRedis()
    store = RedisTrajectoryStore(redis)
    await store.save_sample(_sample("s1"))
    await store.fetch_and_mark_training("online", 1)

    await store.save_sample(_sample("s1"))

    stats = await store.stats()
    assert stats["pending_samples"] == 1
    assert stats["training_samples"] == 0


@pytest.mark.asyncio
async def test_redis_trajectory_store_update_status_tolerates_missing_payload():
    from openjiuwen.agent_evolving.agent_rl.storage.redis_trajectory_store import RedisTrajectoryStore

    redis = _FakeRedis()
    store = RedisTrajectoryStore(redis)
    await store.save_sample(_sample("s1"))
    await store.fetch_and_mark_training("online", 1)

    redis._hashes["rl:traj:s1"]["sample_json"] = None
    await store.mark_trained(["s1"])

    stats = await store.stats()
    assert stats["pending_samples"] == 0
    assert stats["training_samples"] == 1
    assert stats["trained_samples"] == 0


@pytest.mark.asyncio
async def test_redis_trajectory_store_management_crud():
    from openjiuwen.agent_evolving.agent_rl.storage.redis_trajectory_store import RedisTrajectoryStore

    store = RedisTrajectoryStore(_FakeRedis())
    await store.save_sample({**_sample("s1", user_id="u1"), "task_id": "coding", "source": "api"})

    listed = await store.list_samples(user_id="u1", status="pending")
    assert [item["sample_id"] for item in listed] == ["s1"]

    patched = await store.patch_sample("s1", {"status": "failed", "metadata": {"reviewed": True}})
    assert patched is not None
    assert patched["_store_status"] == "failed"
    assert patched["metadata"]["reviewed"] is True

    stats = await store.management_stats(user_id="u1")
    assert stats["by_status"]["failed"] == 1
    assert stats["by_source"]["api"] == 1

    assert await store.delete_sample("s1") is True
    assert await store.get_sample("s1") is None

class TestLoRARepository:
    def setup_method(self):
        import tempfile
        from openjiuwen.agent_evolving.agent_rl.storage.lora_repo import LoRAPublishRequest, LoRARepository
        self.publish_request_cls = LoRAPublishRequest
        self.tmpdir = tempfile.mkdtemp()
        self.repo = LoRARepository(self.tmpdir)

    def teardown_method(self):
        import shutil
        shutil.rmtree(self.tmpdir)

    def _make_lora_dir(self, name: str = "adapter") -> str:
        """创建一个包含 dummy 文件的临时 LoRA 目录。"""
        import tempfile, os
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "adapter_model.safetensors"), "w") as f:
            f.write("dummy")
        return d

    def _publish(self, user_id: str, lora_path: str, **kwargs):
        return self.repo.publish(self.publish_request_cls(user_id=user_id, lora_path=lora_path, **kwargs))

    def test_publish_and_get_latest(self):
        import shutil
        lora_dir = self._make_lora_dir()
        v = self._publish("user1", lora_dir, metadata={"trajectory_count": 10, "reward_avg": 0.6})
        shutil.rmtree(lora_dir)

        assert v.version == "v1"
        assert v.trajectory_count == 10

        latest = self.repo.get_latest("user1")
        assert latest is not None
        assert latest.version == "v1"
        assert latest.availability_status == "pending"

    def test_latest_points_to_newest(self):
        import shutil
        for i in range(3):
            d = self._make_lora_dir()
            self._publish("user1", d, metadata={"trajectory_count": i, "reward_avg": 0.0})
            shutil.rmtree(d)

        latest = self.repo.get_latest("user1")
        assert latest.version == "v3"

    def test_latest_available_skips_unavailable_versions_and_persists(self):
        import shutil

        d1 = self._make_lora_dir()
        d2 = self._make_lora_dir()
        d3 = self._make_lora_dir()
        v1 = self._publish("user1", d1)
        v2 = self._publish("user1", d2)
        v3 = self._publish("user1", d3)
        shutil.rmtree(d1)
        shutil.rmtree(d2)
        shutil.rmtree(d3)

        self.repo.set_availability("user1", v1.version, available=True, reason="eval passed")
        self.repo.set_availability("user1", v2.version, available=False, reason="eval failed")
        assert self.repo.get_latest_available("user1").version == "v1"
        assert self.repo.get_latest("user1").version == "v3"

        from openjiuwen.agent_evolving.agent_rl.storage.lora_repo import LoRARepository
        reloaded = LoRARepository(self.tmpdir)
        latest_available = reloaded.get_latest_available("user1")
        assert latest_available is not None
        assert latest_available.version == "v1"
        assert latest_available.availability_status == "available"
        assert latest_available.availability_reason == "eval passed"
        assert reloaded.get_version("user1", v3.version).availability_status == "pending"

    def test_get_latest_returns_none_for_new_user(self):
        assert self.repo.get_latest("no_such_user") is None

    def test_publish_accepts_scheduler_metadata_keys(self):
        import shutil

        lora_dir = self._make_lora_dir()
        v = self._publish("user1", lora_dir, metadata={"sample_count": 12, "avg_score": 0.75})
        shutil.rmtree(lora_dir)

        assert v.trajectory_count == 12
        assert v.reward_avg == 0.75

    def test_publish_ignores_non_numeric_version_dirs(self):
        import shutil

        user_dir = self.repo.root / "user1"
        user_dir.mkdir(parents=True, exist_ok=True)
        (user_dir / "v_test").mkdir()

        lora_dir = self._make_lora_dir()
        v = self._publish("user1", lora_dir)
        shutil.rmtree(lora_dir)

        assert v.version == "v1"

    def test_manage_specific_lora_version(self):
        import shutil

        d1 = self._make_lora_dir()
        d2 = self._make_lora_dir()
        v1 = self._publish("user1", d1)
        v2 = self._publish("user1", d2)
        shutil.rmtree(d1)
        shutil.rmtree(d2)

        assert self.repo.get_version("user1", v1.version).version == "v1"
        self.repo.set_latest("user1", v1.version)
        assert self.repo.get_latest("user1").version == "v1"
        self.repo.delete_version("user1", v2.version)
        assert self.repo.get_version("user1", v2.version) is None

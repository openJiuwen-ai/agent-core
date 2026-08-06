from __future__ import annotations

import asyncio


class _FakeStore:
    def __init__(self) -> None:
        self.samples: list[dict] = []
        self.trained: list[list[str]] = []
        self.failed: list[list[str]] = []
        self.reset: list[list[str]] = []

    async def mark_trained(self, sample_ids: list[str]) -> None:
        self.trained.append(list(sample_ids))

    async def mark_failed(self, sample_ids: list[str]) -> None:
        self.failed.append(list(sample_ids))

    async def reset_to_pending(self, sample_ids: list[str]) -> None:
        self.reset.append(list(sample_ids))

    async def get_pending_count(self, user_id: str) -> int:
        return len([sample for sample in self.samples if sample["user_id"] == user_id])

    async def get_users_above_threshold(self, threshold: int) -> list[str]:
        users = sorted({sample["user_id"] for sample in self.samples})
        return [user for user in users if await self.get_pending_count(user) >= threshold]

    async def fetch_and_mark_training(self, user_id: str, limit: int) -> list[dict]:
        selected = [sample for sample in self.samples if sample["user_id"] == user_id][:limit]
        self.samples = [sample for sample in self.samples if sample not in selected]
        return selected


class _FakeTrainingTaskStore:
    def __init__(self, task: dict | None = None) -> None:
        self.task = task
        self.claims: list[dict] = []
        self.updates: list[dict] = []

    async def get_active_task(self):
        return self.task

    async def claim_pending_task(self, *, user_id: str | None, sample_count: int):
        if self.task is None or self.task.get("status") != "pending":
            return None
        self.task = {**self.task, "status": "running", "user_id": user_id or "", "sample_count": sample_count}
        self.claims.append({"user_id": user_id, "sample_count": sample_count})
        return self.task

    async def get_task(self, task_id: str):
        if self.task and self.task.get("task_id") == task_id:
            return self.task
        return None

    async def update_task_status(self, task_id: str, *, status: str, error: str = ""):
        if self.task and self.task.get("task_id") == task_id:
            self.task = {**self.task, "status": status, "error": error}
            self.updates.append({"task_id": task_id, "status": status, "error": error})
            return self.task
        return None


class _FakeTrainer:
    def __init__(self, *, should_fail: bool = False) -> None:
        self.should_fail = should_fail
        self.calls: list[dict] = []

    async def train_batch(self, **kwargs):
        self.calls.append(dict(kwargs))
        if self.should_fail:
            raise RuntimeError("boom")
        return "/tmp/lora"


def test_train_batch_marks_trained_on_success():
    from openjiuwen.agent_evolving.agent_rl.online.scheduler.online_training_scheduler import (
        OnlineTrainingScheduler,
    )

    scheduler = OnlineTrainingScheduler(redis_url="")
    scheduler._trajectory_store = _FakeStore()
    scheduler._trainer = _FakeTrainer()
    scheduler._training_count = 3

    asyncio.run(
        scheduler._train_batch(
            user_id="u1",
            samples=[{"sample_id": "s1"}],
            sample_ids=["s1"],
        )
    )

    assert scheduler._trajectory_store.trained == [["s1"]]
    assert scheduler._trajectory_store.failed == []
    assert scheduler._trainer.calls == [{
        "user_id": "u1",
        "samples": [{"sample_id": "s1"}],
        "training_count": 3,
        "tmp_root": "/tmp/agent_rl_online",
    }]


def test_train_batch_marks_failed_on_error():
    from openjiuwen.agent_evolving.agent_rl.online.scheduler.online_training_scheduler import (
        OnlineTrainingScheduler,
    )

    scheduler = OnlineTrainingScheduler(redis_url="")
    scheduler._trajectory_store = _FakeStore()
    scheduler._trainer = _FakeTrainer(should_fail=True)
    scheduler._training_count = 7

    asyncio.run(
        scheduler._train_batch(
            user_id="u2",
            samples=[{"sample_id": "s2"}],
            sample_ids=["s2"],
        )
    )

    assert scheduler._trajectory_store.trained == []
    assert scheduler._trajectory_store.failed == [["s2"]]


def test_task_store_mode_waits_for_training_task_before_fetching_samples():
    from openjiuwen.agent_evolving.agent_rl.online.scheduler.online_training_scheduler import (
        OnlineTrainingScheduler,
    )

    store = _FakeStore()
    store.samples = [{"sample_id": f"s{i}", "user_id": "u1"} for i in range(4)]
    scheduler = OnlineTrainingScheduler(redis_url="", min_samples_for_training=4)
    scheduler._trajectory_store = store
    scheduler._training_task_store = _FakeTrainingTaskStore(None)
    scheduler._trainer = _FakeTrainer()

    asyncio.run(scheduler._poll_once())

    assert len(store.samples) == 4
    assert scheduler._active_training_task is None


def test_task_store_mode_training_task_claims_pending_samples_once():
    from openjiuwen.agent_evolving.agent_rl.online.scheduler.online_training_scheduler import (
        OnlineTrainingScheduler,
    )

    store = _FakeStore()
    store.samples = [{"sample_id": f"s{i}", "user_id": "u1"} for i in range(4)]
    task_store = _FakeTrainingTaskStore({"task_id": "task-1", "status": "pending", "user_id": "u1"})
    scheduler = OnlineTrainingScheduler(redis_url="", min_samples_for_training=4)
    scheduler._trajectory_store = store
    scheduler._training_task_store = task_store
    scheduler._trainer = _FakeTrainer()

    async def _run():
        await scheduler._poll_once()
        await scheduler._reap_training_task(wait=True)

    asyncio.run(_run())

    assert task_store.claims == [{"user_id": "u1", "sample_count": 4}]
    assert task_store.updates[-1]["status"] == "succeeded"
    assert store.trained == [[f"s{i}" for i in range(4)]]


def test_task_store_mode_without_user_trains_all_ready_users():
    from openjiuwen.agent_evolving.agent_rl.online.scheduler.online_training_scheduler import (
        OnlineTrainingScheduler,
    )

    store = _FakeStore()
    store.samples = (
        [{"sample_id": f"u1-s{i}", "user_id": "u1"} for i in range(4)]
        + [{"sample_id": f"u2-s{i}", "user_id": "u2"} for i in range(4)]
        + [{"sample_id": f"u3-s{i}", "user_id": "u3"} for i in range(3)]
    )
    task_store = _FakeTrainingTaskStore({"task_id": "task-all", "status": "pending", "user_id": ""})
    scheduler = OnlineTrainingScheduler(redis_url="", min_samples_for_training=4)
    scheduler._trajectory_store = store
    scheduler._training_task_store = task_store
    scheduler._trainer = _FakeTrainer()

    async def _run():
        await scheduler._poll_once()
        await scheduler._reap_training_task(wait=True)

    asyncio.run(_run())

    assert task_store.claims == [{"user_id": None, "sample_count": 8}]
    assert task_store.updates[-1]["status"] == "succeeded"
    assert [call["user_id"] for call in scheduler._trainer.calls] == ["u1", "u2"]
    assert [len(call["samples"]) for call in scheduler._trainer.calls] == [4, 4]
    assert store.trained == [
        [f"u1-s{i}" for i in range(4)],
        [f"u2-s{i}" for i in range(4)],
    ]
    assert [sample["sample_id"] for sample in store.samples] == [f"u3-s{i}" for i in range(3)]

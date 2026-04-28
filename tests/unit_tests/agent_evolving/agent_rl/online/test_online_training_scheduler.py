from __future__ import annotations

import asyncio


class _FakeStore:
    def __init__(self) -> None:
        self.trained: list[list[str]] = []
        self.failed: list[list[str]] = []

    async def mark_trained(self, sample_ids: list[str]) -> None:
        self.trained.append(list(sample_ids))

    async def mark_failed(self, sample_ids: list[str]) -> None:
        self.failed.append(list(sample_ids))


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

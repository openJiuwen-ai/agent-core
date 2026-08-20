from __future__ import annotations

import asyncio
from contextlib import suppress


class _FakeStore:
    def __init__(self) -> None:
        self.trained: list[list[str]] = []
        self.failed: list[list[str]] = []
        self.pending_count: int = 0
        self.users: list[str] = []
        self.fetched: list[tuple[str, int]] = []

    async def mark_trained(self, sample_ids: list[str]) -> None:
        self.trained.append(list(sample_ids))

    async def mark_failed(self, sample_ids: list[str]) -> None:
        self.failed.append(list(sample_ids))

    async def get_pending_count(self, user_id: str) -> int:
        del user_id
        return self.pending_count

    async def get_users_above_threshold(self, threshold: int) -> list[str]:
        if threshold <= self.pending_count:
            return list(self.users)
        return []

    async def fetch_and_mark_training(self, user_id: str, limit: int) -> list[dict]:
        self.fetched.append((user_id, limit))
        return [{"sample_id": f"{user_id}-{index}"} for index in range(limit)]


class _FakeSFTStore:
    def __init__(self) -> None:
        self.saved_samples: list[dict] = []
        self.raw_processed: list[list[str]] = []
        self.raw_failed: list[list[str]] = []
        self.samples_trained: list[list[str]] = []
        self.samples_failed: list[list[str]] = []
        self.pending_sample_count: int = 0
        self.sample_users: list[str] = []
        self.raw_users: list[str] = []
        self.sample_user_limits: dict[str, int] = {}
        self.raw_user_limits: dict[str, int] = {}
        self.fetched_samples: list[tuple[str, int]] = []
        self.fetched_raw: list[tuple[str, int]] = []

    async def save_sample(self, sample: dict, *, user_id: str):
        del user_id
        self.saved_samples.append(dict(sample))

    async def mark_raw_processed(self, raw_ids: list[str]) -> None:
        self.raw_processed.append(list(raw_ids))

    async def mark_raw_failed(self, raw_ids: list[str]) -> None:
        self.raw_failed.append(list(raw_ids))

    async def mark_samples_trained(self, sample_ids: list[str]) -> None:
        self.samples_trained.append(list(sample_ids))

    async def mark_samples_failed(self, sample_ids: list[str]) -> None:
        self.samples_failed.append(list(sample_ids))

    async def get_pending_sample_count(self, user_id: str) -> int:
        if user_id in self.sample_user_limits:
            return self.sample_user_limits[user_id]
        if user_id not in self.sample_users:
            return 0
        return self.pending_sample_count

    async def get_sample_users_above_threshold(self, threshold: int) -> list[str]:
        del threshold
        return list(self.sample_users)

    async def get_raw_users_above_threshold(self, threshold: int) -> list[str]:
        del threshold
        return list(self.raw_users)

    async def fetch_samples_and_mark_training(self, user_id: str, limit: int) -> list[dict]:
        self.fetched_samples.append((user_id, limit))
        if user_id not in self.sample_users and user_id not in self.sample_user_limits:
            return []
        limit = min(limit, self.sample_user_limits.get(user_id, limit))
        return [{"sample_id": "sft-s1"} for _ in range(limit)]

    async def fetch_raw_and_mark_processing(self, user_id: str, limit: int) -> list[dict]:
        self.fetched_raw.append((user_id, limit))
        if user_id not in self.raw_users and user_id not in self.raw_user_limits:
            return []
        limit = min(limit, self.raw_user_limits.get(user_id, limit))
        return [{"raw_id": "raw-1"} for _ in range(limit)]


class _FakeTrainingTaskStore:
    def __init__(self, task: dict | None = None) -> None:
        self.task = task or {
            "task_id": "task-1",
            "status": "pending",
            "user_id": "u1",
        }
        self.claimed: list[tuple[str | None, int]] = []
        self.updated: list[tuple[str, str]] = []

    async def get_active_task(self) -> dict | None:
        return dict(self.task)

    async def get_task(self, task_id: str) -> dict | None:
        del task_id
        return dict(self.task)

    async def claim_pending_task(self, *, user_id: str | None, sample_count: int) -> dict | None:
        self.claimed.append((user_id, sample_count))
        self.task = {
            **self.task,
            "status": "running",
            "user_id": user_id or self.task.get("user_id", ""),
            "sample_count": sample_count,
        }
        return dict(self.task)

    async def update_task_status(self, task_id: str, *, status: str, error: str = "") -> dict | None:
        del error
        self.updated.append((task_id, status))
        self.task = {**self.task, "status": status}
        return dict(self.task)


class _FakeTrainer:
    def __init__(self, *, should_fail: bool = False) -> None:
        self.should_fail = should_fail
        self.calls: list[dict] = []
        self.stop_calls = 0

    async def train_batch(self, **kwargs):
        self.calls.append(dict(kwargs))
        if self.should_fail:
            raise RuntimeError("boom")
        return "/tmp/lora"

    def request_stop(self) -> dict[str, object]:
        self.stop_calls += 1
        return {"active": True, "action": "signal:SIGINT", "name": "fake"}


class _FakeRollouter:
    def __init__(self) -> None:
        self.requests = []

    async def rollout(self, request):
        from openjiuwen.agent_evolving.agent_rl.online.scheduler.plugins import RolloutResult

        self.requests.append(request)
        return RolloutResult(trajectories=[{"sample_id": "new-sample"}])


class _FakeSFTTrainer:
    def __init__(self, *, should_fail: bool = False) -> None:
        self.should_fail = should_fail
        self.rollout_calls: list[dict] = []
        self.train_calls: list[dict] = []
        self.stop_calls = 0

    async def build_samples_from_raw(self, **kwargs):
        self.rollout_calls.append(dict(kwargs))
        if self.should_fail:
            raise RuntimeError("boom")
        return [{"sample_id": "sft-s1", "messages": [], "assistant_message": {"content": "ok"}}]

    async def train_batch(self, **kwargs):
        self.train_calls.append(dict(kwargs))
        if self.should_fail:
            raise RuntimeError("boom")
        return "/tmp/sft-lora"

    def request_stop(self) -> dict[str, object]:
        self.stop_calls += 1
        return {"active": True, "action": "signal:SIGINT", "name": "fake-sft"}


def test_train_batch_marks_trained_on_success():
    from openjiuwen.agent_evolving.agent_rl.online.core.scheduler import (
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


def test_train_batch_calls_rollouter_without_mutating_samples():
    from openjiuwen.agent_evolving.agent_rl.online.core.scheduler import (
        OnlineTrainingScheduler,
    )

    rollouter = _FakeRollouter()
    sample = {
        "sample_id": "s1",
        "request": {"messages": [{"role": "user", "content": "hello"}]},
    }
    scheduler = OnlineTrainingScheduler(redis_url="", rollouter=rollouter)
    scheduler._trajectory_store = _FakeStore()
    scheduler._trainer = _FakeTrainer()
    scheduler._training_count = 4

    asyncio.run(
        scheduler._train_batch(
            user_id="u1",
            samples=[sample],
            sample_ids=["s1"],
        )
    )

    assert len(rollouter.requests) == 1
    assert rollouter.requests[0].prompts == [[{"role": "user", "content": "hello"}]]
    assert scheduler._trainer.calls[0]["samples"] == [sample]
    assert scheduler._trajectory_store.trained == [["s1"]]


def test_train_batch_marks_failed_on_error():
    from openjiuwen.agent_evolving.agent_rl.online.core.scheduler import (
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


def test_sft_rollout_marks_raw_processed_and_saves_samples():
    from openjiuwen.agent_evolving.agent_rl.online.core.scheduler import (
        OnlineTrainingScheduler,
    )

    scheduler = OnlineTrainingScheduler(redis_url="", train_backend="SFT", sft_dry_run=True)
    scheduler._sft_store = _FakeSFTStore()
    scheduler._trainer = _FakeSFTTrainer()

    asyncio.run(
        scheduler._rollout_sft_raw(
            user_id="u1",
            raw_trajectories=[{"raw_id": "r1"}],
            raw_ids=["r1"],
        )
    )

    assert scheduler._sft_store.saved_samples[0]["sample_id"] == "sft-s1"
    assert scheduler._sft_store.raw_processed == [["r1"]]
    assert scheduler._sft_store.raw_failed == []


def test_sft_train_batch_marks_samples_failed_on_error():
    from openjiuwen.agent_evolving.agent_rl.online.core.scheduler import (
        OnlineTrainingScheduler,
    )

    scheduler = OnlineTrainingScheduler(redis_url="", train_backend="SFT", sft_dry_run=True)
    scheduler._sft_store = _FakeSFTStore()
    scheduler._trainer = _FakeSFTTrainer(should_fail=True)
    scheduler._training_count = 2

    asyncio.run(
        scheduler._train_sft_batch(
            user_id="u1",
            samples=[{"sample_id": "sft-s1"}],
            sample_ids=["sft-s1"],
        )
    )

    assert scheduler._sft_store.samples_trained == []
    assert scheduler._sft_store.samples_failed == [["sft-s1"]]


def test_sft_api_trigger_ignores_threshold_when_drain_pending():
    from openjiuwen.agent_evolving.agent_rl.online.core.scheduler import (
        OnlineTrainingScheduler,
    )

    scheduler = OnlineTrainingScheduler(
        redis_url="",
        train_backend="SFT",
        min_samples_for_training=1,
        drain_pending_on_train=True,
        sft_dry_run=True,
    )
    scheduler._sft_store = _FakeSFTStore()
    scheduler._sft_store.pending_sample_count = 1
    scheduler._sft_store.sample_users = ["u1"]
    scheduler._sft_store.sample_user_limits = {"u1": 1}
    scheduler._training_task_store = _FakeTrainingTaskStore()
    scheduler._trainer = _FakeSFTTrainer()

    async def _run():
        fetch_limit = await scheduler._resolve_sft_sample_fetch_limit("u1")
        assert fetch_limit == 1
        ok = await scheduler._poll_sft_training_task_once()
        assert ok is True
        await scheduler._reap_training_task(wait=True)

    asyncio.run(_run())
    assert scheduler._sft_store.fetched_samples == [("u1", 1)]
    assert scheduler._training_task_store.claimed == [("u1", 1)]
    assert scheduler._trainer.train_calls


def test_ppo_api_trigger_ignores_threshold_when_drain_pending():
    from openjiuwen.agent_evolving.agent_rl.online.core.scheduler import (
        OnlineTrainingScheduler,
    )

    scheduler = OnlineTrainingScheduler(
        redis_url="",
        min_samples_for_training=999,
        drain_pending_on_train=True,
        ppo_samples_per_step=4,
        allow_partial_last_step=False,
    )
    scheduler._trajectory_store = _FakeStore()
    scheduler._trajectory_store.pending_count = 6
    scheduler._training_task_store = _FakeTrainingTaskStore()
    scheduler._trainer = _FakeTrainer()

    async def _run():
        fetch_limit = await scheduler._resolve_ppo_fetch_limit("u1", require_min_samples=False)
        assert fetch_limit == 4
        ok = await scheduler._poll_ppo_training_task_once()
        assert ok is True
        await scheduler._reap_training_task(wait=True)

    asyncio.run(_run())
    assert scheduler._trajectory_store.fetched == [("u1", 4)]
    assert scheduler._training_task_store.claimed == [("u1", 4)]
    assert scheduler._trainer.calls[0]["samples"] == [
        {"sample_id": "u1-0"},
        {"sample_id": "u1-1"},
        {"sample_id": "u1-2"},
        {"sample_id": "u1-3"},
    ]


def test_ppo_api_trigger_honors_task_fetch_limit():
    from openjiuwen.agent_evolving.agent_rl.online.core.scheduler import (
        OnlineTrainingScheduler,
    )

    scheduler = OnlineTrainingScheduler(
        redis_url="",
        min_samples_for_training=999,
        drain_pending_on_train=True,
    )
    scheduler._trajectory_store = _FakeStore()
    scheduler._trajectory_store.pending_count = 10
    scheduler._training_task_store = _FakeTrainingTaskStore(
        task={
            "task_id": "task-ppo-limit",
            "status": "pending",
            "user_id": "u1",
            "max_samples_per_run": 4,
            "ppo_samples_per_step": 4,
            "allow_partial_last_step": False,
        }
    )
    scheduler._trainer = _FakeTrainer()

    async def _run():
        ok = await scheduler._poll_ppo_training_task_once()
        assert ok is True
        await scheduler._reap_training_task(wait=True)

    asyncio.run(_run())
    assert scheduler._trajectory_store.fetched == [("u1", 4)]
    assert scheduler._training_task_store.claimed == [("u1", 4)]


def test_sft_api_trigger_without_user_id_trains_all_users():
    from openjiuwen.agent_evolving.agent_rl.online.core.scheduler import (
        OnlineTrainingScheduler,
    )

    scheduler = OnlineTrainingScheduler(
        redis_url="",
        train_backend="SFT",
        min_samples_for_training=1,
        drain_pending_on_train=True,
        sft_dry_run=True,
    )
    scheduler._sft_store = _FakeSFTStore()
    scheduler._sft_store.sample_users = ["u1", "u2"]
    scheduler._sft_store.raw_users = ["u3"]
    scheduler._sft_store.sample_user_limits = {"u1": 1, "u2": 1}
    scheduler._sft_store.raw_user_limits = {"u3": 1}
    scheduler._sft_store.pending_sample_count = 1
    scheduler._training_task_store = _FakeTrainingTaskStore(task={"task_id": "task-2", "status": "pending", "user_id": ""})
    scheduler._trainer = _FakeSFTTrainer()

    async def _run():
        ok = await scheduler._poll_sft_training_task_once()
        assert ok is True
        await scheduler._reap_training_task(wait=True)

    asyncio.run(_run())
    assert scheduler._sft_store.fetched_samples == [("u1", 1), ("u2", 1), ("u3", 1)]
    assert scheduler._sft_store.fetched_raw == [("u3", 1)]
    assert scheduler._training_task_store.claimed == [(None, 3)]
    assert [call["user_id"] for call in scheduler._trainer.train_calls] == ["u1", "u2", "u3"]
    assert [call["user_id"] for call in scheduler._trainer.rollout_calls] == ["u3"]


def test_sft_api_trigger_honors_task_fetch_limit():
    from openjiuwen.agent_evolving.agent_rl.online.core.scheduler import (
        OnlineTrainingScheduler,
    )

    scheduler = OnlineTrainingScheduler(
        redis_url="",
        train_backend="SFT",
        min_samples_for_training=999,
        drain_pending_on_train=True,
        sft_dry_run=True,
    )
    scheduler._sft_store = _FakeSFTStore()
    scheduler._sft_store.sample_users = ["u1"]
    scheduler._sft_store.sample_user_limits = {"u1": 10}
    scheduler._training_task_store = _FakeTrainingTaskStore(
        task={
            "task_id": "task-sft-limit",
            "status": "pending",
            "user_id": "u1",
            "max_samples_per_run": 3,
        }
    )
    scheduler._trainer = _FakeSFTTrainer()

    async def _run():
        ok = await scheduler._poll_sft_training_task_once()
        assert ok is True
        await scheduler._reap_training_task(wait=True)

    asyncio.run(_run())
    assert scheduler._sft_store.fetched_samples == [("u1", 3)]
    assert scheduler._training_task_store.claimed == [("u1", 3)]


def test_scheduler_requests_stop_for_active_training_task():
    from openjiuwen.agent_evolving.agent_rl.online.core.scheduler import (
        OnlineTrainingScheduler,
    )

    scheduler = OnlineTrainingScheduler(redis_url="")
    scheduler._training_task_store = _FakeTrainingTaskStore(
        task={"task_id": "task-stop", "status": "stopping", "user_id": "u1"}
    )
    scheduler._trainer = _FakeTrainer()

    async def _run():
        active = asyncio.create_task(asyncio.sleep(60))
        scheduler._active_training_task = active
        scheduler._active_training_task_id = "task-stop"
        await scheduler._maybe_request_active_stop()
        active.cancel()
        with suppress(asyncio.CancelledError):
            await active

    asyncio.run(_run())
    assert scheduler._trainer.stop_calls == 1
    assert scheduler._active_stop_requested is True


def test_sft_training_sequence_stops_before_next_user_when_task_is_stopping():
    from openjiuwen.agent_evolving.agent_rl.online.core.scheduler import (
        OnlineTrainingScheduler,
    )

    scheduler = OnlineTrainingScheduler(redis_url="", train_backend="SFT", sft_dry_run=True)
    scheduler._sft_store = _FakeSFTStore()
    scheduler._sft_store.sample_users = ["u1", "u2"]
    scheduler._sft_store.sample_user_limits = {"u1": 1, "u2": 1}
    scheduler._training_task_store = _FakeTrainingTaskStore(
        task={"task_id": "task-stop", "status": "stopping", "user_id": ""}
    )
    scheduler._trainer = _FakeSFTTrainer()

    asyncio.run(scheduler._train_sft_users(user_ids=["u1", "u2"], task_id="task-stop"))

    assert scheduler._sft_store.fetched_samples == []
    assert scheduler._trainer.train_calls == []
    assert scheduler._training_task_store.updated == [("task-stop", "canceled")]

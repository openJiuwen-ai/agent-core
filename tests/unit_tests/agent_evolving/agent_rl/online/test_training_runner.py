from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from openjiuwen.agent_evolving.agent_rl.online.training_runner import (
    PolicySnapshot,
    RunStage,
    RunStatus,
    TrainingArtifact,
    TrainingRunner,
    TrainingRunRecord,
)
from openjiuwen.agent_evolving.agent_rl.storage.redis_trajectory_store import RedisTrajectoryStore
from openjiuwen.agent_evolving.agent_rl.storage.trajectory_store import InMemoryTrajectoryStore
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.common.exception.errors import ValidationError as JiuwenValidationError
from tests.unit_tests.agent_evolving.agent_rl.online.support import InMemoryRedis


@dataclass
class _FakePPO:
    release: asyncio.Event

    def __post_init__(self) -> None:
        self.calls: list[dict] = []

    async def train(self, **kwargs) -> TrainingArtifact:
        self.calls.append(kwargs)
        await self.release.wait()
        return TrainingArtifact(lora_name="model-1:v1", lora_path="/loras/model-1/v1")


class _FakeActivator:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def activate(self, **kwargs) -> None:
        self.calls.append(kwargs)


class _FailingPPO:
    async def train(self, **kwargs) -> TrainingArtifact:
        del kwargs
        raise RuntimeError("ppo failed")


class _FailingActivator(_FakeActivator):
    async def activate(self, **kwargs) -> None:
        await super().activate(**kwargs)
        raise RuntimeError("activation failed")


class _BrokenTrainedStore(InMemoryTrajectoryStore):
    async def mark_trained(self, sample_ids: list[str]) -> None:
        del sample_ids
        raise RuntimeError("trajectory store failed")


class _BlockingTrainedStore(InMemoryTrajectoryStore):
    def __init__(self) -> None:
        super().__init__()
        self.mark_started = asyncio.Event()
        self.release_mark = asyncio.Event()

    async def mark_trained(self, sample_ids: list[str]) -> None:
        self.mark_started.set()
        await self.release_mark.wait()
        await super().mark_trained(sample_ids)


class _InvalidArtifactPPO:
    async def train(self, **kwargs) -> TrainingArtifact:
        del kwargs
        return TrainingArtifact(lora_name="model-1:vnext", lora_path="/loras/model-1/vnext")


class _CancellablePPO:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancel_calls: list[str] = []

    async def train(self, **kwargs) -> TrainingArtifact:
        del kwargs
        self.started.set()
        await self.release.wait()
        raise RuntimeError("actor stopped")

    async def cancel(self, training_run_id: str) -> None:
        self.cancel_calls.append(training_run_id)
        self.release.set()


class _CompletingPPO:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def train(self, **kwargs) -> TrainingArtifact:
        del kwargs
        self.started.set()
        await self.release.wait()
        return TrainingArtifact(lora_name="model-1:v1", lora_path="/loras/model-1/v1")

    async def cancel(self, training_run_id: str) -> bool:
        del training_run_id
        self.release.set()
        return True


class _UnhealthyPPO(_FailingPPO):
    def check_health(self) -> None:
        raise RuntimeError("scheduler failed")


class _UnavailableRedis(InMemoryRedis):
    async def get(self, key: str):
        del key
        raise RedisConnectionError("redis unavailable")


async def _save_samples(store: InMemoryTrajectoryStore, count: int) -> None:
    for index in range(count):
        await store.save_sample(
            {
                "sample_id": f"sample-{index}",
                "policy_version": "base" if index % 2 == 0 else "model-1:v0",
                "trajectory": {"response_logprobs": [-0.1 - index]},
            },
            user_id="model-1",
        )


@pytest.mark.asyncio
async def test_start_requires_enough_pending_samples_without_creating_run() -> None:
    store = InMemoryTrajectoryStore()
    await _save_samples(store, 1)
    runner = TrainingRunner(
        redis=InMemoryRedis(),
        trajectory_store=store,
        ppo=_FakePPO(asyncio.Event()),
        activator=_FakeActivator(),
        model_id="model-1",
        base_model_path="/models/base",
        min_samples_for_training=2,
        max_samples_per_run=3,
    )

    with pytest.raises(JiuwenValidationError):
        await runner.start()

    assert await runner.get_active() is None
    assert (await store.stats())["pending_samples"] == 1


@pytest.mark.asyncio
async def test_run_store_wraps_redis_failure() -> None:
    runner = TrainingRunner(
        redis=_UnavailableRedis(),
        trajectory_store=InMemoryTrajectoryStore(),
        ppo=_FailingPPO(),
        activator=_FakeActivator(),
        model_id="model-1",
        base_model_path="/models/base",
        min_samples_for_training=2,
        max_samples_per_run=2,
    )

    with pytest.raises(BaseError) as exc_info:
        await runner.get("run-1")

    assert exc_info.value.status is StatusCode.AGENT_RL_TRAJECTORY_RUNTIME_ERROR
    assert isinstance(exc_info.value.cause, RedisConnectionError)


@pytest.mark.asyncio
async def test_run_store_wraps_invalid_persisted_record() -> None:
    redis = InMemoryRedis()
    await redis.set("rl:v1:training_run:run-1", "not-json")
    runner = TrainingRunner(
        redis=redis,
        trajectory_store=InMemoryTrajectoryStore(),
        ppo=_FailingPPO(),
        activator=_FakeActivator(),
        model_id="model-1",
        base_model_path="/models/base",
        min_samples_for_training=2,
        max_samples_per_run=2,
    )

    with pytest.raises(BaseError) as exc_info:
        await runner.get("run-1")

    assert exc_info.value.status is StatusCode.AGENT_RL_TRAJECTORY_RUNTIME_ERROR
    assert isinstance(exc_info.value.cause, ValueError)


@pytest.mark.asyncio
async def test_start_claims_fixed_batch_and_reuses_active_run() -> None:
    store = InMemoryTrajectoryStore()
    await _save_samples(store, 5)
    release = asyncio.Event()
    ppo = _FakePPO(release)
    runner = TrainingRunner(
        redis=InMemoryRedis(),
        trajectory_store=store,
        ppo=ppo,
        activator=_FakeActivator(),
        model_id="model-1",
        base_model_path="/models/base",
        min_samples_for_training=2,
        max_samples_per_run=3,
        active_policy=lambda: PolicySnapshot("base", ""),
    )

    first = await runner.start()
    second = await runner.start()
    for _ in range(20):
        if ppo.calls:
            break
        await asyncio.sleep(0)

    assert first.created is True
    assert second.created is False
    assert second.run.training_run_id == first.run.training_run_id
    assert first.run.sample_count == 3
    assert first.run.policy_versions == {"base": 2, "model-1:v0": 1}
    assert (await store.stats())["pending_samples"] == 2
    assert len(ppo.calls) == 1

    release.set()
    completed = await runner.wait(first.run.training_run_id)
    assert completed.status.value == "succeeded"


@pytest.mark.asyncio
async def test_redis_start_uses_atomic_run_and_sample_claim() -> None:
    redis = InMemoryRedis()
    store = RedisTrajectoryStore(redis)
    await _save_samples(store, 3)
    release = asyncio.Event()
    ppo = _FakePPO(release)

    async def reject_split_claim(user_id: str, limit: int):
        del user_id, limit
        raise AssertionError("Redis claims must be committed with the Run record")

    store.fetch_and_mark_training = reject_split_claim
    runner = TrainingRunner(
        redis=redis,
        trajectory_store=store,
        ppo=ppo,
        activator=_FakeActivator(),
        model_id="model-1",
        base_model_path="/models/base",
        min_samples_for_training=2,
        max_samples_per_run=2,
    )

    started = await runner.start()
    persisted = await runner.get(started.run.training_run_id)

    assert persisted is not None
    assert persisted.sample_ids == ("sample-0", "sample-1")
    assert persisted.sample_count == 2
    assert (await store.stats())["training_samples"] == 2

    release.set()
    await runner.wait(started.run.training_run_id)


@pytest.mark.asyncio
async def test_success_marks_samples_trained_before_activation() -> None:
    store = InMemoryTrajectoryStore()
    await _save_samples(store, 2)
    release = asyncio.Event()
    release.set()

    class _AssertingActivator(_FakeActivator):
        async def activate(self, **kwargs) -> None:
            assert (await store.stats())["trained_samples"] == 2
            await super().activate(**kwargs)

    activator = _AssertingActivator()
    runner = TrainingRunner(
        redis=InMemoryRedis(),
        trajectory_store=store,
        ppo=_FakePPO(release),
        activator=activator,
        model_id="model-1",
        base_model_path="/models/base",
        min_samples_for_training=2,
        max_samples_per_run=4,
        active_policy=lambda: PolicySnapshot("model-1:v0", "/loras/model-1/v0"),
    )

    started = await runner.start()
    completed = await runner.wait(started.run.training_run_id)

    assert completed.status.value == "succeeded"
    assert completed.stage.value == "activating"
    assert completed.lora_name == "model-1:v1"
    assert completed.lora_path == "/loras/model-1/v1"
    assert activator.calls == [
        {
            "training_run_id": completed.training_run_id,
            "model_id": "model-1",
            "base_model": "/models/base",
            "lora_name": "model-1:v1",
            "lora_path": "/loras/model-1/v1",
            "expected_lora_name": "model-1:v0",
        }
    ]


@pytest.mark.asyncio
async def test_ppo_failure_restores_claimed_samples() -> None:
    store = InMemoryTrajectoryStore()
    await _save_samples(store, 2)
    runner = TrainingRunner(
        redis=InMemoryRedis(),
        trajectory_store=store,
        ppo=_FailingPPO(),
        activator=_FakeActivator(),
        model_id="model-1",
        base_model_path="/models/base",
        min_samples_for_training=2,
        max_samples_per_run=2,
    )

    started = await runner.start()
    completed = await runner.wait(started.run.training_run_id)

    assert completed.status.value == "failed"
    assert completed.failure_reason == "ppo failed"
    assert (await store.stats())["pending_samples"] == 2


@pytest.mark.asyncio
async def test_invalid_artifact_version_fails_and_restores_claimed_samples() -> None:
    store = InMemoryTrajectoryStore()
    await _save_samples(store, 2)
    runner = TrainingRunner(
        redis=InMemoryRedis(),
        trajectory_store=store,
        ppo=_InvalidArtifactPPO(),
        activator=_FakeActivator(),
        model_id="model-1",
        base_model_path="/models/base",
        min_samples_for_training=2,
        max_samples_per_run=2,
    )

    started = await runner.start()
    completed = await runner.wait(started.run.training_run_id)

    assert completed.status is RunStatus.FAILED
    assert "PPO artifact must use '<model_id>:vN' name" in str(completed.failure_reason)
    assert (await store.stats())["pending_samples"] == 2


@pytest.mark.asyncio
async def test_activation_failure_keeps_artifact_and_trained_samples() -> None:
    store = InMemoryTrajectoryStore()
    await _save_samples(store, 2)
    release = asyncio.Event()
    release.set()
    runner = TrainingRunner(
        redis=InMemoryRedis(),
        trajectory_store=store,
        ppo=_FakePPO(release),
        activator=_FailingActivator(),
        model_id="model-1",
        base_model_path="/models/base",
        min_samples_for_training=2,
        max_samples_per_run=2,
    )

    started = await runner.start()
    completed = await runner.wait(started.run.training_run_id)

    assert completed.status.value == "failed"
    assert completed.stage.value == "activating"
    assert completed.lora_path == "/loras/model-1/v1"
    assert completed.failure_reason == "activation failed"
    assert (await store.stats())["trained_samples"] == 2


@pytest.mark.asyncio
async def test_unexpected_runner_failure_makes_health_fail() -> None:
    store = _BrokenTrainedStore()
    await _save_samples(store, 2)
    release = asyncio.Event()
    release.set()
    runner = TrainingRunner(
        redis=InMemoryRedis(),
        trajectory_store=store,
        ppo=_FakePPO(release),
        activator=_FakeActivator(),
        model_id="model-1",
        base_model_path="/models/base",
        min_samples_for_training=2,
        max_samples_per_run=2,
    )

    started = await runner.start()
    with pytest.raises(BaseError, match="trajectory store failed") as exc_info:
        await runner.wait(started.run.training_run_id)
    assert isinstance(exc_info.value.cause, RuntimeError)

    with pytest.raises(BaseError, match="training runner failed"):
        runner.check_health()


def test_scheduler_failure_makes_runner_health_fail() -> None:
    runner = TrainingRunner(
        redis=InMemoryRedis(),
        trajectory_store=InMemoryTrajectoryStore(),
        ppo=_UnhealthyPPO(),
        activator=_FakeActivator(),
        model_id="model-1",
        base_model_path="/models/base",
        min_samples_for_training=2,
        max_samples_per_run=2,
    )

    with pytest.raises(RuntimeError, match="scheduler failed"):
        runner.check_health()


@pytest.mark.asyncio
async def test_stop_during_ppo_cancels_and_restores_samples() -> None:
    store = InMemoryTrajectoryStore()
    await _save_samples(store, 2)
    ppo = _FakePPO(asyncio.Event())
    runner = TrainingRunner(
        redis=InMemoryRedis(),
        trajectory_store=store,
        ppo=ppo,
        activator=_FakeActivator(),
        model_id="model-1",
        base_model_path="/models/base",
        min_samples_for_training=2,
        max_samples_per_run=2,
    )
    started = await runner.start()
    for _ in range(20):
        if ppo.calls:
            break
        await asyncio.sleep(0)

    stopped = await runner.stop(started.run.training_run_id)

    assert stopped.status.value == "canceled"
    assert (await store.stats())["pending_samples"] == 2
    assert await runner.stop(started.run.training_run_id) == stopped


@pytest.mark.asyncio
async def test_stop_waits_for_cancellable_ppo_before_restoring_samples() -> None:
    store = InMemoryTrajectoryStore()
    await _save_samples(store, 2)
    ppo = _CancellablePPO()
    runner = TrainingRunner(
        redis=InMemoryRedis(),
        trajectory_store=store,
        ppo=ppo,
        activator=_FakeActivator(),
        model_id="model-1",
        base_model_path="/models/base",
        min_samples_for_training=2,
        max_samples_per_run=2,
    )
    started = await runner.start()
    await ppo.started.wait()

    stopped = await runner.stop(started.run.training_run_id)

    assert ppo.cancel_calls == [started.run.training_run_id]
    assert stopped.status is RunStatus.CANCELED
    assert (await store.stats())["pending_samples"] == 2


@pytest.mark.asyncio
async def test_stop_after_ppo_keeps_trained_samples_and_artifact() -> None:
    store = _BlockingTrainedStore()
    await _save_samples(store, 2)
    release_ppo = asyncio.Event()
    release_ppo.set()
    runner = TrainingRunner(
        redis=InMemoryRedis(),
        trajectory_store=store,
        ppo=_FakePPO(release_ppo),
        activator=_FakeActivator(),
        model_id="model-1",
        base_model_path="/models/base",
        min_samples_for_training=2,
        max_samples_per_run=2,
    )
    started = await runner.start()
    await store.mark_started.wait()

    stop_task = asyncio.create_task(runner.stop(started.run.training_run_id))
    await asyncio.sleep(0)
    store.release_mark.set()
    stopped = await stop_task

    assert stopped.status is RunStatus.CANCELED
    assert stopped.stage is RunStage.ACTIVATING
    assert stopped.lora_name == "model-1:v1"
    assert (await store.stats())["trained_samples"] == 2


@pytest.mark.asyncio
async def test_stop_as_ppo_completes_keeps_trained_samples_and_artifact() -> None:
    store = InMemoryTrajectoryStore()
    await _save_samples(store, 2)
    ppo = _CompletingPPO()
    runner = TrainingRunner(
        redis=InMemoryRedis(),
        trajectory_store=store,
        ppo=ppo,
        activator=_FakeActivator(),
        model_id="model-1",
        base_model_path="/models/base",
        min_samples_for_training=2,
        max_samples_per_run=2,
    )
    started = await runner.start()
    await ppo.started.wait()

    stopped = await runner.stop(started.run.training_run_id)

    assert stopped.status is RunStatus.CANCELED
    assert stopped.stage is RunStage.ACTIVATING
    assert stopped.lora_name == "model-1:v1"
    assert (await store.stats())["trained_samples"] == 2


@pytest.mark.asyncio
async def test_recover_training_fails_run_and_restores_samples() -> None:
    store = InMemoryTrajectoryStore()
    await _save_samples(store, 2)
    claimed = await store.fetch_and_mark_training("model-1", 2)
    redis = InMemoryRedis()
    persisted = TrainingRunRecord(
        training_run_id="run-recover",
        status=RunStatus.RUNNING,
        stage=RunStage.TRAINING,
        sample_count=2,
        policy_versions={"base": 2},
        created_at="2026-01-01T00:00:00+00:00",
        started_at="2026-01-01T00:00:01+00:00",
        sample_ids=tuple(sample["sample_id"] for sample in claimed),
    )
    await redis.set("rl:v1:training_run:run-recover", persisted.to_json())
    await redis.set("rl:v1:training_run:active", "run-recover")

    restarted = TrainingRunner(
        redis=redis,
        trajectory_store=store,
        ppo=_FailingPPO(),
        activator=_FakeActivator(),
        model_id="model-1",
        base_model_path="/models/base",
        min_samples_for_training=2,
        max_samples_per_run=2,
    )
    recovered = await restarted.recover()

    assert recovered is not None
    assert recovered.training_run_id == "run-recover"
    assert recovered.status.value == "failed"
    assert recovered.failure_reason == "service_restarted"
    assert (await store.stats())["pending_samples"] == 2


@pytest.mark.asyncio
async def test_recover_activating_retries_same_artifact_and_parent_cas() -> None:
    store = InMemoryTrajectoryStore()
    await _save_samples(store, 2)
    claimed = await store.fetch_and_mark_training("model-1", 2)
    await store.mark_trained([sample["sample_id"] for sample in claimed])
    redis = InMemoryRedis()
    persisted = TrainingRunRecord(
        training_run_id="run-activating",
        status=RunStatus.RUNNING,
        stage=RunStage.ACTIVATING,
        sample_count=2,
        policy_versions={"model-1:v2": 2},
        created_at="2026-01-01T00:00:00+00:00",
        started_at="2026-01-01T00:00:01+00:00",
        lora_name="model-1:v3",
        lora_path="/loras/model-1/v3",
        sample_ids=tuple(sample["sample_id"] for sample in claimed),
        parent_lora_name="model-1:v2",
        parent_lora_path="/loras/model-1/v2",
    )
    await redis.set("rl:v1:training_run:run-activating", persisted.to_json())
    await redis.set("rl:v1:training_run:active", "run-activating")
    activator = _FakeActivator()
    runner = TrainingRunner(
        redis=redis,
        trajectory_store=store,
        ppo=_FailingPPO(),
        activator=activator,
        model_id="model-1",
        base_model_path="/models/base",
        min_samples_for_training=2,
        max_samples_per_run=2,
    )

    recovered = await runner.recover()

    assert recovered is not None
    assert recovered.status.value == "succeeded"
    assert activator.calls == [
        {
            "training_run_id": "run-activating",
            "model_id": "model-1",
            "base_model": "/models/base",
            "lora_name": "model-1:v3",
            "lora_path": "/loras/model-1/v3",
            "expected_lora_name": "model-1:v2",
        }
    ]
    assert (await store.stats())["trained_samples"] == 2

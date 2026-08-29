from __future__ import annotations

import asyncio
import shutil
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError

from openjiuwen.agent_evolving.agent_rl.online.training_runner import TrainingRunner
from openjiuwen.agent_evolving.agent_rl.storage.redis_trajectory_store import RedisTrajectoryStore


class _SimulatedProcessCrash(BaseException):
    pass


class _CrashingPPO:
    async def train(self, **kwargs):
        del kwargs
        raise _SimulatedProcessCrash


class _UnusedActivator:
    async def activate(self, **kwargs) -> None:
        del kwargs
        raise AssertionError("activation must not run before crash recovery")


@pytest_asyncio.fixture
async def isolated_redis(tmp_path: Path) -> AsyncIterator[Redis]:
    redis_server = shutil.which("redis-server")
    if redis_server is None:
        pytest.skip("redis-server is required for the isolated Redis system test")

    socket_path = tmp_path / "redis.sock"
    process = subprocess.Popen(
        [
            redis_server,
            "--port",
            "0",
            "--unixsocket",
            str(socket_path),
            "--save",
            "",
            "--appendonly",
            "no",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    redis = Redis(unix_socket_path=str(socket_path))
    for _ in range(100):
        try:
            await redis.ping()
            break
        except RedisConnectionError:
            if process.poll() is not None:
                pytest.fail("isolated redis-server exited during startup")
            await asyncio.sleep(0.02)
    else:
        pytest.fail("isolated redis-server did not become ready")

    try:
        yield redis
    finally:
        await redis.aclose()
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def _runner(redis: Redis, store: RedisTrajectoryStore) -> TrainingRunner:
    return TrainingRunner(
        redis=redis,
        trajectory_store=store,
        ppo=_CrashingPPO(),
        activator=_UnusedActivator(),
        model_id="model-1",
        base_model_path="/models/base",
        min_samples_for_training=2,
        max_samples_per_run=2,
    )


@pytest.mark.asyncio
async def test_real_redis_claim_is_atomic_and_restart_restores_samples(isolated_redis: Redis) -> None:
    store = RedisTrajectoryStore(isolated_redis)
    for index in range(4):
        await store.save_sample(
            {
                "sample_id": f"sample-{index}",
                "policy_version": "base",
                "trajectory": {"response_logprobs": [-0.1]},
            },
            user_id="model-1",
        )

    first_runner = _runner(isolated_redis, store)
    second_runner = _runner(isolated_redis, store)
    starts = await asyncio.gather(first_runner.start(), second_runner.start())
    created = [result for result in starts if result.created]

    assert len(created) == 1
    assert starts[0].run.training_run_id == starts[1].run.training_run_id
    assert starts[0].run.sample_count == 2
    assert (await store.stats())["pending_samples"] == 2
    assert (await store.stats())["training_samples"] == 2

    owner = first_runner if starts[0].created else second_runner
    with pytest.raises(_SimulatedProcessCrash):
        await owner.wait(created[0].run.training_run_id)

    recovered = await _runner(isolated_redis, store).recover()

    assert recovered is not None
    assert recovered.training_run_id == created[0].run.training_run_id
    assert recovered.status.value == "failed"
    assert recovered.failure_reason == "service_restarted"
    assert (await store.stats())["pending_samples"] == 4
    assert (await store.stats())["training_samples"] == 0

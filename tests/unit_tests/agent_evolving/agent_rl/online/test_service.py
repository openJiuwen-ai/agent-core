from __future__ import annotations

import asyncio

import httpx
import pytest

from openjiuwen.agent_evolving.agent_rl.online.capture_pipeline import CapturePipeline
from openjiuwen.agent_evolving.agent_rl.online.service import build_rl_service_app
from openjiuwen.agent_evolving.agent_rl.online.task_registry import RewardMode, TaskRegistry, TaskSpec, TaskStatus
from openjiuwen.agent_evolving.agent_rl.storage.trajectory_store import InMemoryTrajectoryStore
from tests.unit_tests.agent_evolving.agent_rl.online.support import InMemoryRedis, openai_response


class _TrajectoryAPI:
    def __init__(self, store: InMemoryTrajectoryStore) -> None:
        self.store = store
        self.uploads: list[dict] = []
        self.rail_ingestor = self

    async def ingest_rail_batch(self, payload: dict) -> dict:
        self.uploads.append(payload)
        return {"accepted": 1, "rejected": 0}

    async def trajectory_management_stats(self, **kwargs) -> dict:
        del kwargs
        return {"total": (await self.store.stats())["total_samples"]}

    async def list_trajectories(self, **kwargs) -> dict:
        samples = await self.store.list_samples(limit=kwargs["limit"])
        return {"items": samples, "next_cursor": None}

    async def get_trajectory(self, trajectory_id: str) -> dict | None:
        return await self.store.get_sample(trajectory_id)


class _NoRuns:
    def __init__(self) -> None:
        self.health_error: Exception | None = None

    async def start(self):
        raise AssertionError("not used")

    async def recover(self):
        return []

    async def get_active(self):
        return None

    def check_health(self) -> None:
        if self.health_error is not None:
            raise self.health_error


def _app(*, training_runner=None, redis: InMemoryRedis | None = None):
    redis = InMemoryRedis() if redis is None else redis
    store = InMemoryTrajectoryStore()
    registry = TaskRegistry(redis=redis)
    pipeline = CapturePipeline(registry=registry, trajectory_store=store)
    trajectory_api = _TrajectoryAPI(store)
    return (
        build_rl_service_app(
            model_id="model-1",
            redis=redis,
            trajectory_store=store,
            task_registry=registry,
            capture_pipeline=pipeline,
            training_runner=training_runner or _NoRuns(),
            trajectory_api=trajectory_api,
        ),
        store,
        trajectory_api,
    )


@pytest.mark.asyncio
async def test_health_requires_redis_and_trajectory_store() -> None:
    app, _, _ = _app()

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://rl.local") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "model_id": "model-1"}


@pytest.mark.asyncio
async def test_health_fails_when_training_runner_cannot_continue() -> None:
    runs = _NoRuns()
    runs.health_error = RuntimeError("scheduler stopped")
    app, _, _ = _app(training_runner=runs)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://rl.local") as client:
        response = await client.get("/health")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "service_not_ready"


@pytest.mark.asyncio
async def test_shutdown_aborts_delayed_task_with_open_capture() -> None:
    redis = InMemoryRedis()
    store = InMemoryTrajectoryStore()
    registry = TaskRegistry(redis=redis)
    pipeline = CapturePipeline(registry=registry, trajectory_store=store)
    app = build_rl_service_app(
        model_id="model-1",
        redis=redis,
        trajectory_store=store,
        task_registry=registry,
        capture_pipeline=pipeline,
        training_runner=_NoRuns(),
        trajectory_api=_TrajectoryAPI(store),
    )

    async def run_lifespan() -> None:
        async with app.router.lifespan_context(app):
            await registry.start(
                TaskSpec("task-1", "session-1", "model-1", "model-1:v2", RewardMode.DELAYED_FEEDBACK),
            )
            await pipeline.before(
                "task-1",
                "capture-1",
                "turn-1",
                {"model": "model-1:v2", "messages": [{"role": "user", "content": "ping"}]},
            )

    await asyncio.wait_for(run_lifespan(), timeout=1.0)
    task = await registry.get("task-1")
    assert task is not None and task.status is TaskStatus.ABORTED
    assert await registry._open_capture_count("task-1") == 0


@pytest.mark.asyncio
async def test_training_run_routes_map_created_reused_conflict_and_not_found() -> None:
    class _Runs:
        def __init__(self) -> None:
            self.result = None

        async def start(self):
            if isinstance(self.result, Exception):
                raise self.result
            return self.result

        async def get(self, training_run_id: str):
            del training_run_id
            return None

        async def stop(self, training_run_id: str):
            raise KeyError(training_run_id)

        def check_health(self) -> None:
            return None

    from openjiuwen.agent_evolving.agent_rl.online.training_runner import (
        RunStage,
        RunStatus,
        TrainingRunRecord,
        TrainingRunStartResult,
    )
    from openjiuwen.core.common.exception.codes import StatusCode
    from openjiuwen.core.common.exception.errors import build_error

    run = TrainingRunRecord(
        training_run_id="run-1",
        status=RunStatus.PENDING,
        stage=RunStage.QUEUED,
        sample_count=2,
        policy_versions={"base": 2},
        created_at="2026-01-01T00:00:00+00:00",
    )
    runs = _Runs()
    app, _, _ = _app(training_runner=runs)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://rl.local") as client:
        runs.result = TrainingRunStartResult(run=run, created=True)
        created = await client.post("/v1/rl/training/runs")
        runs.result = TrainingRunStartResult(run=run, created=False)
        reused = await client.post("/v1/rl/training/runs", json={})
        runs.result = build_error(StatusCode.AGENT_RL_TRAINING_SAMPLES_INVALID, error_msg="not enough samples")
        conflict = await client.post("/v1/rl/training/runs")
        missing_get = await client.get("/v1/rl/training/runs/missing")
        missing_stop = await client.post("/v1/rl/training/runs/missing/stop")

    assert created.status_code == 201
    assert reused.status_code == 200
    assert reused.json()["training_run_id"] == "run-1"
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "insufficient_samples"
    assert missing_get.status_code == 404
    assert missing_stop.status_code == 404


@pytest.mark.asyncio
async def test_task_and_completion_routes_preserve_standard_payload_contract() -> None:
    app, store, _ = _app()
    request_body = {"model": "base/model-a", "messages": [{"role": "user", "content": "ping"}]}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://rl.local") as client:
        created = await client.post(
            "/v1/rl/tasks/start",
            headers={
                "X-Agent-Session-Id": "session-1",
                "X-AIGW-RL-Task-Id": "task-1",
                "X-AIGW-RL-Policy-Name": "base",
                "X-AIGW-RL-Policy-Model": "base/model-a",
            },
            json={"reward_mode": "terminal"},
        )
        task = created.json()
        before = await client.post(
            "/internal/v1/completions:before",
            json={
                "rl_task_id": task["rl_task_id"],
                "capture_id": "capture-1",
                "agent_turn_id": None,
                "request": request_body,
            },
        )
        injected_request = before.json()["request"]
        response_body = openai_response(model="base/model-a")
        response_body["object"] = "chat.completion"
        after = await client.post(
            "/internal/v1/completions:after",
            json={
                "rl_task_id": task["rl_task_id"],
                "capture_id": "capture-1",
                "agent_turn_id": None,
                "request": injected_request,
                "response": response_body,
            },
        )
        stopped = await client.post(f"/v1/rl/tasks/{task['rl_task_id']}/stop")
        rewarded = await client.post(
            f"/v1/rl/tasks/{task['rl_task_id']}/reward",
            json={"reward": 0.75},
        )
        fetched = await client.get(f"/v1/rl/tasks/{task['rl_task_id']}")

    assert created.status_code == 201
    assert task["policy_lora_name"] == "base"
    assert before.status_code == 200
    assert injected_request["logprobs"] is True
    assert injected_request["top_logprobs"] == 1
    assert injected_request["return_token_ids"] is True
    assert after.status_code == 204
    assert stopped.json()["status"] == "finalized"
    assert rewarded.json() == {"sample_count": 1}
    assert fetched.json()["status"] == "finalized"
    assert (await store.stats())["pending_samples"] == 1
    sample = await store.get_sample("capture-1")
    assert sample is not None
    assert sample["policy_version"] == "base"


@pytest.mark.asyncio
async def test_gateway_owns_task_identity_policy_discard_and_terminal_reason() -> None:
    app, _, _ = _app()
    request_body = {"model": "model-1:v7", "messages": [{"role": "user", "content": "ping"}]}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://rl.local") as client:
        created = await client.post(
            "/v1/rl/tasks/start",
            headers={
                "X-Agent-Session-Id": "session-owned",
                "X-AIGW-RL-Task-Id": "task-owned",
                "X-AIGW-RL-Policy-Name": "model-1:v7",
                "X-AIGW-RL-Policy-Model": "model-1:v7",
            },
            json={"reward_mode": "terminal"},
        )
        before = await client.post(
            "/internal/v1/completions:before",
            json={
                "rl_task_id": "task-owned",
                "capture_id": "capture-discarded",
                "agent_turn_id": None,
                "request": request_body,
            },
        )
        discarded = await client.post(
            "/internal/v1/completions:discard",
            json={
                "rl_task_id": "task-owned",
                "capture_id": "capture-discarded",
                "agent_turn_id": None,
            },
        )
        finished = await client.post(
            "/internal/v1/rl/tasks/task-owned:finish",
            json={"reason": "service_stopped"},
        )

    assert created.status_code == 201
    assert created.json()["rl_task_id"] == "task-owned"
    assert created.json()["policy_lora_name"] == "model-1:v7"
    assert before.status_code == 200
    assert discarded.status_code == 204
    assert finished.json()["status"] == "finalized"
    assert finished.json()["finish_reason"] == "service_stopped"


@pytest.mark.asyncio
async def test_gateway_task_headers_reject_blank_identity() -> None:
    app, _, _ = _app()

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://rl.local") as client:
        response = await client.post(
            "/v1/rl/tasks/start",
            headers={
                "X-Agent-Session-Id": "session-blank",
                "X-AIGW-RL-Task-Id": " ",
                "X-AIGW-RL-Policy-Name": " ",
                "X-AIGW-RL-Policy-Model": " ",
            },
            json={"reward_mode": "terminal"},
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_gateway_task"


@pytest.mark.asyncio
@pytest.mark.parametrize("task_id", ["task:invalid", "t" * 65])
async def test_gateway_task_id_rejects_unsafe_redis_key_components(task_id: str) -> None:
    app, _, _ = _app()

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://rl.local") as client:
        response = await client.post(
            "/v1/rl/tasks/start",
            headers={
                "X-Agent-Session-Id": "session-invalid-id",
                "X-AIGW-RL-Task-Id": task_id,
                "X-AIGW-RL-Policy-Name": "base",
                "X-AIGW-RL-Policy-Model": "base/model-1",
            },
            json={"reward_mode": "terminal"},
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_gateway_task"


@pytest.mark.asyncio
async def test_gateway_task_identity_conflict_returns_409() -> None:
    app, _, _ = _app()

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://rl.local") as client:
        await client.post(
            "/v1/rl/tasks/start",
            headers={
                "X-Agent-Session-Id": "session-first",
                "X-AIGW-RL-Task-Id": "task-conflict",
                "X-AIGW-RL-Policy-Name": "base",
                "X-AIGW-RL-Policy-Model": "base",
            },
            json={"reward_mode": "terminal"},
        )
        response = await client.post(
            "/v1/rl/tasks/start",
            headers={
                "X-Agent-Session-Id": "session-second",
                "X-AIGW-RL-Task-Id": "task-conflict",
                "X-AIGW-RL-Policy-Name": "base",
                "X-AIGW-RL-Policy-Model": "base",
            },
            json={"reward_mode": "terminal"},
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "task_conflict"


@pytest.mark.asyncio
async def test_internal_task_abort_discards_open_capture() -> None:
    redis = InMemoryRedis()
    app, _, _ = _app(redis=redis)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://rl.local") as client:
        await client.post(
            "/v1/rl/tasks/start",
            headers={
                "X-Agent-Session-Id": "session-abort",
                "X-AIGW-RL-Task-Id": "task-abort",
                "X-AIGW-RL-Policy-Name": "base",
                "X-AIGW-RL-Policy-Model": "base",
            },
            json={"reward_mode": "terminal"},
        )
        await client.post(
            "/internal/v1/completions:before",
            json={
                "rl_task_id": "task-abort",
                "capture_id": "capture-open",
                "agent_turn_id": None,
                "request": {"model": "base", "messages": []},
            },
        )
        aborted = await client.post(
            "/internal/v1/rl/tasks/task-abort:abort",
            json={"reason": "capture_failed"},
        )
        fetched = await client.get("/v1/rl/tasks/task-abort")

    assert aborted.status_code == 200
    assert aborted.json()["status"] == "aborted"
    assert aborted.json()["finish_reason"] == "capture_failed"
    assert fetched.json()["status"] == "aborted"
    assert await redis.get("rl:v1:task:task-abort:capture:capture-open") is None
    assert not await redis.sismember("rl:v1:task:task-abort:captures", "capture-open")
    assert not await redis.exists("rl:v1:task:task-abort:captures:open")


@pytest.mark.asyncio
async def test_unknown_task_completion_and_terminal_routes_return_404() -> None:
    app, _, _ = _app()
    calls = (
        (
            "/internal/v1/completions:before",
            {"rl_task_id": "missing", "capture_id": "capture-1", "agent_turn_id": None, "request": {}},
        ),
        (
            "/internal/v1/completions:after",
            {
                "rl_task_id": "missing",
                "capture_id": "capture-1",
                "agent_turn_id": None,
                "request": {},
                "response": {},
            },
        ),
        (
            "/internal/v1/completions:discard",
            {"rl_task_id": "missing", "capture_id": "capture-1", "agent_turn_id": None},
        ),
        ("/internal/v1/rl/tasks/missing:finish", {"reason": "service_stopped"}),
        ("/internal/v1/rl/tasks/missing:abort", {"reason": "capture_failed"}),
    )

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://rl.local") as client:
        responses = [await client.post(path, json=payload) for path, payload in calls]

    assert [response.status_code for response in responses] == [404] * len(calls)


@pytest.mark.asyncio
async def test_errors_use_stable_envelope_and_rail_query_routes_are_mapped() -> None:
    app, store, trajectory_api = _app()
    await store.save_sample({"sample_id": "sample-1"}, user_id="model-1")

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://rl.local") as client:
        invalid = await client.post("/v1/rl/tasks/start", json={"reward_mode": "terminal"})
        upload = await client.post(
            "/v1/gateway/upload/batch",
            json={"protocol_version": "rail-v1", "samples": []},
        )
        stats = await client.get("/v1/rl/trajectories/stats")
        listed = await client.get("/v1/rl/trajectories?limit=10")
        fetched = await client.get("/v1/rl/trajectories/sample-1")
        missing = await client.get("/v1/rl/trajectories/missing")

    assert invalid.status_code == 400
    assert invalid.json()["error"]["code"] == "missing_session_id"
    assert upload.status_code == 200
    assert trajectory_api.uploads[0]["protocol_version"] == "rail-v1"
    assert stats.json()["total"] == 1
    assert len(listed.json()["items"]) == 1
    assert fetched.json()["sample_id"] == "sample-1"
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "trajectory_not_found"


@pytest.mark.asyncio
async def test_unexpected_errors_use_stable_envelope() -> None:
    app, _, trajectory_api = _app()

    async def fail_stats(**kwargs):
        del kwargs
        raise RuntimeError("unexpected storage error")

    trajectory_api.trajectory_management_stats = fail_stats
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://rl.local") as client:
        response = await client.get("/v1/rl/trajectories/stats")

    assert response.status_code == 500
    assert response.json() == {"error": {"code": "internal_error", "message": "internal service error"}}

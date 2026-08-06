from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from openjiuwen.agent_evolving.agent_rl.online.gateway.app.bootstrap import build_app_from_config
from openjiuwen.agent_evolving.agent_rl.online.scheduler.online_training_scheduler import (
    OnlineTrainingScheduler,
)
from openjiuwen.agent_evolving.agent_rl.storage.local_store import LocalTrajectoryStore
from openjiuwen.agent_evolving.agent_rl.storage.store_factory import build_scheduler_store_bundle


class _FakeUpstreamClient:
    async def post_chat_completions(self, *, json_body: dict, headers: dict):
        del json_body, headers
        return SimpleNamespace(json=lambda: {"choices": []})

    async def request(self, *, method: str, url: str, params: dict, headers: dict, content: bytes):
        del method, url, params, headers, content
        return SimpleNamespace(status_code=200, text="ok", json=lambda: {})


class _FakeTrainer:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def train_batch(self, **kwargs):
        self.calls.append(dict(kwargs))
        return "/tmp/lora"


@pytest.mark.asyncio
async def test_local_store_gateway_to_scheduler_e2e(tmp_path):
    store_dir = tmp_path / "store"
    records_dir = tmp_path / "records"
    app = build_app_from_config(
        SimpleNamespace(
            gateway_api_key="gw-token",
            llm_url="http://vllm.local",
            llm_api_key="",
            request_timeout=120.0,
            upstream_max_retries=2,
            upstream_retry_backoff_sec=0.2,
            upstream_retry_max_backoff_sec=2.0,
            judge_url="",
            judge_model="",
            model_id="base-model",
            record_dir=str(records_dir),
            dump_token_ids=False,
            lora_repo_root="",
            lora_default_policy="disabled",
            redis_url="",
            trajectory_store_backend="local",
            local_trajectory_store_dir=str(store_dir),
            log_level="INFO",
        ),
        http_client=_FakeUpstreamClient(),
    )

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway.local") as client:
        traj_resp = await client.post(
            "/v1/rl/trajectories:batchCreate",
            headers={"Authorization": "Bearer gw-token"},
            json={
                "protocol_version": "agent-rollout-v1",
                "user_id": "u1",
                "trajectories": [
                    {
                        "sample_id": "traj-1",
                        "trajectory": {"input_ids": [1], "response_ids": [2]},
                        "user_id": "u1",
                        "model": "base-model",
                        "source": "manual",
                        "policy_version": "base",
                    }
                ],
            },
        )
        assert traj_resp.status_code == 200
        assert traj_resp.json()["accepted"] == 1

        task_resp = await client.post(
            "/v1/training/tasks",
            headers={"Authorization": "Bearer gw-token"},
            json={"task_id": "task-1", "user_id": "u1", "sample_count": 1},
        )
        assert task_resp.status_code == 200

        scheduler = OnlineTrainingScheduler(
            redis_url="",
            trajectory_store_backend="local",
            local_trajectory_store_dir=str(store_dir),
            record_dir=str(records_dir),
            min_samples_for_training=1,
        )
        scheduler._store_bundle = build_scheduler_store_bundle(
            backend="local",
            redis_url=None,
            local_store_dir=str(store_dir),
            record_dir=str(records_dir),
        )
        scheduler._trajectory_store = scheduler._store_bundle.trajectory_store
        scheduler._training_task_store = scheduler._store_bundle.training_task_store
        scheduler._trainer = _FakeTrainer()

        await scheduler._poll_once()
        await scheduler._reap_training_task(wait=True)

        stats_resp = await client.get(
            "/v1/rl/trajectories/stats",
            headers={"Authorization": "Bearer gw-token"},
        )
        task_get_resp = await client.get(
            "/v1/training/tasks/task-1",
            headers={"Authorization": "Bearer gw-token"},
        )

    assert stats_resp.status_code == 200
    assert stats_resp.json()["by_status"]["trained"] == 1
    assert task_get_resp.status_code == 200
    assert task_get_resp.json()["status"] == "succeeded"

    runtime_store = LocalTrajectoryStore(store_dir)
    sample = await runtime_store.get_sample("traj-1")
    assert sample is not None
    assert sample["_store_status"] == "trained"

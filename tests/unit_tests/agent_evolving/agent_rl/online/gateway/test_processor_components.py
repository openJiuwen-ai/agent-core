from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from openjiuwen.agent_evolving.agent_rl.online.gateway.app.server import build_gateway_app
from openjiuwen.agent_evolving.agent_rl.online.gateway.app.bootstrap import build_app_from_config
from openjiuwen.agent_evolving.agent_rl.online.gateway.app.completion_runtime import (
    GatewayCompletionRuntime,
    _inject_latest_lora,
)
from openjiuwen.agent_evolving.agent_rl.online.gateway.config import GatewayConfig
from openjiuwen.agent_evolving.agent_rl.online.gateway.trajectory.judge_dispatcher import JudgeDispatcher
from openjiuwen.agent_evolving.agent_rl.online.gateway.trajectory.sample_payloads import (
    build_sample,
)


class _FakeForwarder:
    def __init__(self) -> None:
        self.forward_calls: list[dict] = []

    async def forward(self, body: dict, headers: dict):
        self.forward_calls.append({"body": body, "headers": headers})
        return {"choices": [{"message": {"role": "assistant", "content": "pong"}}]}


class _FakeJudgeScorer:
    def __init__(self, score_result=None) -> None:
        self.calls: list[dict] = []
        self.closed = False
        self.score_result = score_result or {"score": 0.75, "votes": ["ok"], "details": {}}

    async def score(self, **kwargs):
        self.calls.append(kwargs)
        return dict(self.score_result)

    async def close(self) -> None:
        self.closed = True


class _FakePendingStore:
    def __init__(self, samples: list[dict]) -> None:
        self.samples = list(samples)

    async def pop_all(self, session_id: str) -> list[dict]:
        del session_id
        samples = list(self.samples)
        self.samples.clear()
        return samples


class _FakeRecorder:
    def __init__(self) -> None:
        self.samples: list[dict] = []

    async def record_sample(self, sample: dict) -> None:
        self.samples.append(sample)


class _FakeLoRARepo:
    def __init__(self, latest_by_user: dict[str, object]) -> None:
        self.latest_by_user = latest_by_user

    def get_latest(self, user_id: str) -> object | None:
        return self.latest_by_user.get(user_id)


class _FakeResponse:
    def __init__(self, status_code: int = 200, text: str = "ok", payload: dict | None = None) -> None:
        self.status_code = status_code
        self.text = text
        self._payload = payload or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(self.text)

    def json(self):
        return self._payload


class _FakeUpstreamClient:
    def __init__(self, models: list[dict] | None = None, load_response: _FakeResponse | None = None) -> None:
        self.models = models or []
        self.load_response = load_response or _FakeResponse()
        self.requests: list[dict] = []

    async def post_chat_completions(self, *, json_body: dict, headers: dict):
        del json_body, headers
        return _FakeResponse(payload={"choices": []})

    async def request(self, *, method: str, url: str, params: dict, headers: dict, content: bytes):
        call = {
            "method": method,
            "url": url,
            "params": params,
            "headers": headers,
            "content": content,
        }
        self.requests.append(call)
        if url.endswith("/v1/models"):
            return _FakeResponse(payload={"data": self.models})
        if url.endswith("/v1/load_lora_adapter"):
            return self.load_response
        return _FakeResponse(status_code=404, text="not found")


class _FakeTrajectoryRuntime:
    async def snapshot_stats(self):
        return {
            "total_samples": 0,
            "trajectory_store_total": 0,
            "trajectory_store_pending": 0,
        }


class _FakeTrainingTaskStore:
    def __init__(self) -> None:
        self.tasks: dict[str, dict] = {}
        self.active_task_id: str | None = None
        self.created_payloads: list[dict] = []
        self.updated_payloads: list[tuple[str, str]] = []

    async def create_task(self, payload: dict) -> dict:
        self.created_payloads.append(dict(payload))
        task_id = str(payload.get("task_id") or "task-1")
        task = {
            "task_id": task_id,
            "status": "pending",
            "user_id": str(payload.get("user_id") or ""),
            "sample_count": int(payload.get("sample_count") or 0),
            "training_count": int(payload.get("training_count") or 0),
            "metadata": payload.get("metadata") or {},
        }
        self.tasks[task_id] = task
        self.active_task_id = task_id
        return dict(task)

    async def list_tasks(self, *, limit: int = 100):
        return list(self.tasks.values())[:limit]

    async def get_task(self, task_id: str):
        task = self.tasks.get(task_id)
        return dict(task) if task is not None else None

    async def get_active_task(self):
        if self.active_task_id is None:
            return None
        return await self.get_task(self.active_task_id)

    async def update_task_status(self, task_id: str, *, status: str, error: str = ""):
        task = self.tasks.get(task_id)
        if task is None:
            return None
        task = {**task, "status": status, "error": error}
        self.tasks[task_id] = task
        self.updated_payloads.append((task_id, status))
        return dict(task)

    async def request_stop(self, task_id: str):
        return await self.update_task_status(task_id, status="stopping")


def _build_test_gateway(upstream_client: _FakeUpstreamClient, lora_repo: _FakeLoRARepo):
    async def _close_resources() -> None:
        return None

    return build_gateway_app(
        config=SimpleNamespace(
            gateway_api_key="gw-token",
            llm_url="http://vllm.local",
            llm_api_key="",
            lora_default_policy="latest_by_user",
            model_id="base-model",
        ),
        forwarder=_FakeForwarder(),
        upstream_client=upstream_client,
        trajectory_runtime=_FakeTrajectoryRuntime(),
        training_task_store=None,
        close_resources=_close_resources,
        lora_repo=lora_repo,
    )


def test_build_sample_builds_shared_masks():
    sample = build_sample(
        sample_id="sample-1",
        user_id="user-1",
        session_id="s1",
        turn_num=1,
        mode="judge_output",
        io_mode="string",
        model="m1",
        messages=[{"role": "user", "content": "hi"}],
        tools=[{"type": "function"}],
        assistant_message={"role": "assistant", "content": "pong"},
        usage={"total_tokens": 5},
        finish_reason="stop",
        prompt_text="prompt",
        prompt_ids=[1, 2, 3],
        response_text="pong",
        response_ids=[4, 5],
        response_logprobs=[-0.1, -0.2],
        tool_calls=[],
        request_extras={"temperature": 0.2},
        extra_fields={"rail_meta": {"protocol_version": "rail-v1"}},
    )

    assert sample["trajectory"]["input_ids"] == [1, 2, 3, 4, 5]
    assert sample["trajectory"]["attention_mask"] == [1, 1, 1, 1, 1]
    assert sample["trajectory"]["response_mask"] == [0, 0, 0, 1, 1]
    assert sample["request"]["temperature"] == 0.2
    assert sample["rail_meta"]["protocol_version"] == "rail-v1"


def test_inject_latest_lora_routes_by_model_name():
    body = {
        "model": "base-model",
        "messages": [{"role": "user", "content": "hi"}],
        "extra_body": {"lora_name": "stale", "return_token_ids": True},
    }
    repo = _FakeLoRARepo({
        "user-1": SimpleNamespace(version="v3", path="/tmp/lora/v3", base_model="base-model")
    })

    lora_info = _inject_latest_lora(
        body=body,
        user_id="user-1",
        lora_repo=repo,
        lora_default_policy="latest_by_user",
    )

    assert lora_info == {
        "model_id": "user-1",
        "lora_id": "user-1:v3",
        "version": "v3",
        "path": "/tmp/lora/v3",
        "base_model": "base-model",
        "parent_lora_id": "",
        "parent_lora_version": "",
        "availability_status": "pending",
        "training_source": "base_model",
        "default_policy": "latest_by_user",
    }
    assert body["model"] == "user-1"
    assert body["extra_body"] == {"return_token_ids": True}


def test_inject_latest_lora_is_disabled_by_default():
    body = {"model": "base-model", "messages": [{"role": "user", "content": "hi"}]}

    lora_info = _inject_latest_lora(
        body=body,
        user_id="user-1",
        lora_repo=_FakeLoRARepo({"user-1": object()}),
    )

    assert lora_info is None
    assert body["model"] == "base-model"


def test_inject_latest_lora_leaves_base_model_without_adapter():
    body = {"model": "base-model", "messages": [{"role": "user", "content": "hi"}]}

    lora_info = _inject_latest_lora(
        body=body,
        user_id="user-1",
        lora_repo=_FakeLoRARepo({}),
        lora_default_policy="latest_by_user",
    )

    assert lora_info is None
    assert body["model"] == "base-model"


def test_effective_lora_api_hot_loads_latest_adapter():
    upstream = _FakeUpstreamClient(models=[])
    app = _build_test_gateway(
        upstream,
        _FakeLoRARepo({
            "user-1": SimpleNamespace(
                user_id="user-1",
                version="v3",
                path="/tmp/lora/v3",
                base_model="base-model",
            )
        }),
    )

    with TestClient(app) as client:
        resp = client.post(
            "/v1/rl/lora/effective",
            headers={"Authorization": "Bearer gw-token"},
            json={"model_id": "user-1", "ensure_loaded": True},
        )

    assert resp.status_code == 200
    assert resp.json()["enabled"] is True
    assert resp.json()["model_id"] == "user-1"
    assert resp.json()["lora_id"] == "user-1:v3"
    load_calls = [call for call in upstream.requests if call["url"].endswith("/v1/load_lora_adapter")]
    assert len(load_calls) == 1
    assert json.loads(load_calls[0]["content"].decode("utf-8")) == {
        "lora_name": "user-1",
        "lora_path": "/tmp/lora/v3",
        "load_inplace": True,
    }


def test_effective_lora_api_skips_load_when_adapter_is_already_loaded():
    upstream = _FakeUpstreamClient(models=[{"id": "user-1", "root": "/tmp/lora/v3"}])
    app = _build_test_gateway(
        upstream,
        _FakeLoRARepo({
            "user-1": SimpleNamespace(
                user_id="user-1",
                version="v3",
                path="/tmp/lora/v3",
                base_model="base-model",
            )
        }),
    )

    with TestClient(app) as client:
        resp = client.post(
            "/v1/rl/lora/effective",
            headers={"Authorization": "Bearer gw-token"},
            json={"model_id": "user-1", "ensure_loaded": True},
        )

    assert resp.status_code == 200
    assert resp.json()["enabled"] is True
    assert resp.json()["load_status"] == "loaded"
    assert not [call for call in upstream.requests if call["url"].endswith("/v1/load_lora_adapter")]


def test_effective_lora_api_returns_disabled_when_hot_load_fails():
    upstream = _FakeUpstreamClient(models=[], load_response=_FakeResponse(status_code=500, text="boom"))
    app = _build_test_gateway(
        upstream,
        _FakeLoRARepo({
            "user-1": SimpleNamespace(
                user_id="user-1",
                version="v3",
                path="/tmp/lora/v3",
                base_model="base-model",
            )
        }),
    )

    with TestClient(app) as client:
        resp = client.post(
            "/v1/rl/lora/effective",
            headers={"Authorization": "Bearer gw-token"},
            json={"model_id": "user-1", "ensure_loaded": True},
        )

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["enabled"] is False
    assert payload["load_status"] == "load_failed"
    assert payload["reason"] == "boom"


def test_training_task_api_creates_and_stops_task():
    upstream = _FakeUpstreamClient(models=[])
    task_store = _FakeTrainingTaskStore()

    async def _close_resources() -> None:
        return None

    app = build_gateway_app(
        config=SimpleNamespace(
            gateway_api_key="gw-token",
            llm_url="http://vllm.local",
            llm_api_key="",
            lora_default_policy="disabled",
            model_id="base-model",
        ),
        forwarder=_FakeForwarder(),
        upstream_client=upstream,
        trajectory_runtime=_FakeTrajectoryRuntime(),
        training_task_store=task_store,
        close_resources=_close_resources,
        lora_repo=_FakeLoRARepo({}),
    )

    with TestClient(app) as client:
        create_resp = client.post(
            "/v1/training/tasks",
            headers={"Authorization": "Bearer gw-token"},
            json={"task_id": "task-1", "user_id": "user-1", "sample_count": 12},
        )
        assert create_resp.status_code == 200
        assert create_resp.json()["task_id"] == "task-1"
        stop_resp = client.patch(
            "/v1/training/tasks/task-1",
            headers={"Authorization": "Bearer gw-token"},
            json={"status": "stopping"},
        )
        assert stop_resp.status_code == 200
        assert stop_resp.json()["status"] == "stopping"
        list_resp = client.get(
            "/v1/training/tasks",
            headers={"Authorization": "Bearer gw-token"},
        )
        assert list_resp.status_code == 200
        assert list_resp.json()["items"][0]["task_id"] == "task-1"


def test_gateway_bootstrap_uses_local_store_without_redis(tmp_path):
    app = build_app_from_config(
        GatewayConfig(
            port=18080,
            llm_url="http://vllm.local",
            judge_url="",
            model_id="base-model",
            record_dir=str(tmp_path / "records"),
            trajectory_store_backend="local",
            local_trajectory_store_dir=str(tmp_path / "store"),
        ),
        http_client=_FakeUpstreamClient(),
    )

    with TestClient(app) as client:
        health_resp = client.get("/v1/rl/health")
        stats_resp = client.get("/v1/gateway/stats")

    assert health_resp.status_code == 200
    assert health_resp.json()["services"]["trajectory_manager"] == "LocalTrajectoryStore"
    assert stats_resp.status_code == 200
    assert stats_resp.json()["trajectory_store_backend"] == "LocalTrajectoryStore"


@pytest.mark.asyncio
async def test_processor_chat_completion_proxies_without_turn_or_sample_work():
    forwarder = _FakeForwarder()
    runtime = GatewayCompletionRuntime(
        config=SimpleNamespace(llm_api_key=""),
        forwarder=forwarder,
        collector=None,
    )

    request = SimpleNamespace(headers={"x-request-id": "trace-9", "x-user-id": "user-9"})
    result, wants_stream = await runtime.execute(
        request=request,
        body={"messages": [{"role": "user", "content": "hello"}]},
    )

    assert result["choices"][0]["message"]["content"] == "pong"
    assert wants_stream is False
    assert len(forwarder.forward_calls) == 1


@pytest.mark.asyncio
async def test_judge_dispatcher_scores_session_done_sample_without_followup_feedback():
    recorder = _FakeRecorder()
    scorer = _FakeJudgeScorer({"score": 0.25, "votes": [6.25], "details": {"overall": 6.25}})
    sample = {
        "sample_id": "sample-1",
        "user_id": "user-1",
        "session_id": "s1",
        "turn_num": 1,
        "request": {"messages": [{"role": "user", "content": "hello"}]},
        "trajectory": {"response_text": "pong"},
    }
    dispatcher = JudgeDispatcher(
        pending_store=_FakePendingStore([sample]),
        record_sample=recorder.record_sample,
        judge_scorer=scorer,
    )

    count = await dispatcher.on_session_done("s1")

    assert count == 1
    assert len(scorer.calls) == 1
    assert scorer.calls[0]["instruction_text"] == "hello"
    assert scorer.calls[0]["followup_user_feedback"] == ""
    assert recorder.samples[0]["judge"]["score"] == 0.25
    assert recorder.samples[0]["judge_feedback"]["tag"] == "session_done"

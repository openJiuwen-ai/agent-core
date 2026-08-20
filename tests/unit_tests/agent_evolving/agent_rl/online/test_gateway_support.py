from __future__ import annotations

import json
from pathlib import Path

import pytest

from openjiuwen.agent_evolving.trajectory.model import Trajectory
from openjiuwen.agent_evolving.trajectory.spans import attributes_from_map
from openjiuwen.extensions.observability import semconv
from tests.unit_tests.agent_evolving.agent_rl.online.support import InMemoryRedis


def _canonical_llm_trajectory(
    execution_id: str,
    span_attrs: dict[str, object],
    *,
    resource_attrs: dict[str, object] | None = None,
) -> Trajectory:
    resource = {
        "openjiuwen.trajectory_id": execution_id,
        semconv.AT_SESSION_ID: "session-1",
        "openjiuwen.trajectory.source": "online",
    }
    resource.update(resource_attrs or {})
    return Trajectory.from_otlp(
        {
            "resourceSpans": [
                {
                    "resource": {"attributes": attributes_from_map(resource)},
                    "scopeSpans": [
                        {
                            "spans": [
                                {
                                    "traceId": "trace-test",
                                    "spanId": "llm-1",
                                    "name": "llm.call",
                                    "attributes": attributes_from_map(
                                        {
                                            semconv.GEN_AI_REQUEST_MODEL: "m1",
                                            **span_attrs,
                                        }
                                    ),
                                }
                            ]
                        }
                    ],
                }
            ]
        }
    )


@pytest.fixture
def disable_sample_debug_dump(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep Redis pipeline unit tests free of debug-file I/O."""
    from openjiuwen.agent_evolving.agent_rl.online.gateway.trajectory import persistence
    from openjiuwen.agent_evolving.agent_rl.online.gateway.trajectory.sample_recorder import SampleRecorder

    async def skip_debug_dump(self, sample):
        del self, sample

    monkeypatch.setattr(SampleRecorder, "record_sample", skip_debug_dump)
    monkeypatch.setattr(persistence.os, "makedirs", lambda *args, **kwargs: None)


_FakeRedis = InMemoryRedis


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


class _FakeAsyncClient:
    def __init__(self, response: _FakeResponse | None = None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.closed = False
        self.response = response or _FakeResponse()

    async def post(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        return self.response

    async def aclose(self) -> None:
        self.closed = True


def test_judge_scorer_parse_scores_handles_multiple_code_blocks_and_aliases():
    from openjiuwen.agent_evolving.agent_rl.online.judge.judge_scorer import JudgeScorer

    content = """
前置说明
```text
ignored
```
```json
{"task_completion_score": 8, "response_quality": 7, "tool_usage_score": 9, "coherence": 6}
```
"""

    scores = JudgeScorer._parse_scores(content)
    assert scores["task_completion_score"] == 8
    assert scores["overall"] == pytest.approx(7.5)


@pytest.mark.asyncio
async def test_inference_notifier_uses_async_client():
    from openjiuwen.agent_evolving.agent_rl.online.inference.notifier import InferenceNotifier

    client = _FakeAsyncClient()
    notifier = InferenceNotifier("http://vllm.local", http_client=client)

    await notifier.notify_update("user1", "/tmp/lora")

    assert client.calls == [
        (
            "http://vllm.local/v1/load_lora_adapter",
            {
                "json": {
                    "lora_name": "user1",
                    "lora_path": "/tmp/lora",
                    "load_inplace": True,
                },
                "timeout": 120.0,
            },
        )
    ]
    await notifier.close()
    assert client.closed is False


@pytest.mark.asyncio
async def test_judge_scorer_retries_length_and_sanitizes_prompt():
    from openjiuwen.agent_evolving.agent_rl.online.judge.judge_scorer import JudgeScorer

    first = _FakeResponse(payload={
        "choices": [{
            "finish_reason": "length",
            "message": {"content": "<tag>bad</tag>"},
        }],
    })
    second = _FakeResponse(payload={
        "choices": [{
            "finish_reason": "stop",
            "message": {"content": '{"overall": 8, "reason": "ok"}'},
        }],
    })
    client = _FakeAsyncClient(response=first)
    client.response = None

    async def _post(url: str, **kwargs):
        client.calls.append((url, kwargs))
        return first if len(client.calls) == 1 else second

    client.post = _post  # type: ignore[method-assign]
    scorer = JudgeScorer(
        judge_url="http://judge.local",
        judge_model="judge-model",
        http_client=client,
    )

    result = await scorer.score(
        response_text="<tag>resp</tag>",
        instruction_text="<tool_call>plan</tool_call>",
        followup_user_feedback="next",
    )

    assert result["overall_raw"] == 8
    assert len(client.calls) == 2
    prompt = client.calls[0][1]["json"]["messages"][0]["content"]
    assert "[tool_call block]" in prompt
    assert "[tag]resp[/tag]" in prompt


@pytest.mark.asyncio
async def test_gateway_trajectory_runtime_fills_single_user_default_on_record(tmp_path: Path):
    from openjiuwen.agent_evolving.agent_rl.online.gateway.config import GatewayConfig
    from openjiuwen.agent_evolving.agent_rl.online.gateway.trajectory import GatewayTrajectoryRuntime
    from openjiuwen.agent_evolving.agent_rl.storage.local_store import (
        LocalPendingJudgeStore,
        LocalSFTStore,
        LocalTrajectoryStore,
    )

    store_dir = tmp_path / "local_store"
    runtime = GatewayTrajectoryRuntime(
        GatewayConfig(port=18080, model_id="dummy-model", record_dir=str(tmp_path)),
        trajectory_store=LocalTrajectoryStore(store_dir),
        sft_store=LocalSFTStore(store_dir),
        pending_judge_store=LocalPendingJudgeStore(store_dir),
    )

    await runtime.record_sample({"sample_id": "s1"})

    trajectory = await runtime.get_trajectory("s1")
    assert trajectory is not None
    assert trajectory["user_id"] == "jiuwenclaw-web"
    assert json.loads((tmp_path / "samples.jsonl").read_text(encoding="utf-8").strip())["user_id"] == "jiuwenclaw-web"


@pytest.mark.asyncio
async def test_gateway_pending_sample_survives_runtime_recreation(
    tmp_path: Path,
    disable_sample_debug_dump: None,
):
    del disable_sample_debug_dump
    from openjiuwen.agent_evolving.agent_rl.online.gateway.config import GatewayConfig
    from openjiuwen.agent_evolving.agent_rl.online.gateway.trajectory import GatewayTrajectoryRuntime
    from openjiuwen.agent_evolving.agent_rl.online.gateway.trajectory.pending_judge_store import PendingJudgeStore
    from openjiuwen.agent_evolving.agent_rl.storage.redis_trajectory_store import RedisTrajectoryStore

    class _Judge:
        async def score(self, **kwargs):
            del kwargs
            return {"score": 0.6, "votes": [8], "details": {}}

    redis = _FakeRedis()
    config = GatewayConfig(port=18080, model_id="model-1", record_dir=str(tmp_path))
    first_runtime = GatewayTrajectoryRuntime(
        config,
        trajectory_store=RedisTrajectoryStore(redis),
        pending_judge_store=PendingJudgeStore(redis=redis),
    )
    await first_runtime.stage_gateway_sample(
        {
            "sample_id": "gateway:session-restart:1:0",
            "user_id": "user-1",
            "session_id": "session-restart",
            "trajectory_id": "gateway:session-restart:1",
            "step_index": 0,
            "turn_num": 1,
            "request": {"messages": [{"role": "user", "content": "before restart"}]},
            "trajectory": {
                "prompt_ids": [11],
                "response_ids": [12],
                "response_logprobs": [-0.3],
                "response_text": "persisted response",
            },
        }
    )

    recreated_runtime = GatewayTrajectoryRuntime(
        config,
        trajectory_store=RedisTrajectoryStore(redis),
        pending_judge_store=PendingJudgeStore(redis=redis),
    )
    recreated_runtime.set_judge_scorer(_Judge())
    judged = await recreated_runtime.on_gateway_followup(
        "session-restart",
        [{"role": "user", "content": "after restart"}],
    )

    assert judged == 1
    stored = json.loads(await redis.hget("rl:traj:gateway:session-restart:1:0", "sample_json"))
    assert stored["trajectory"]["response_ids"] == [12]
    assert stored["judge"]["score"] == 0.6


@pytest.mark.asyncio
async def test_task_reward_projector_finalizes_pending_sample_once() -> None:
    from openjiuwen.agent_evolving.agent_rl.online.gateway.trajectory.pending_judge_store import PendingJudgeStore
    from openjiuwen.agent_evolving.agent_rl.online.gateway.trajectory.task_reward import TaskReward, TaskRewardProjector

    redis = InMemoryRedis()
    pending = PendingJudgeStore(redis=redis)
    await pending.put(
        {
            "sample_id": "sample-1",
            "session_id": "session-1",
            "trajectory_id": "trajectory-1",
            "step_index": 0,
        }
    )
    recorded = []

    async def record(samples):
        recorded.extend(samples)
        return {sample["sample_id"] for sample in samples}

    projector = TaskRewardProjector(redis=redis, pending_store=pending, record_samples_once=record)
    reward = TaskReward(
        reward_id="reward-1",
        attempt_id="attempt-1",
        task_id="task-1",
        training_key="train-1",
        score=1.0,
        passed=True,
    )

    assert await projector.project("session-1", reward) == 1
    assert await projector.project("session-1", reward) == 1
    assert len(recorded) == 1
    assert recorded[0]["user_id"] == "train-1"
    assert recorded[0]["judge"] == {"score": 1.0, "source": "benchmark_verifier", "reward_id": "reward-1"}
    assert await pending.get_by_session("session-1") == []


def test_online_trajectory_converter_reads_prompt_and_response_token_ids_from_response():
    from openjiuwen.agent_evolving.agent_rl.online.rail.converter import OnlineTrajectoryConverter

    trajectory = _canonical_llm_trajectory(
        "traj-1",
        {
            f"{semconv.GEN_AI_PROMPT}.0.role": "user",
            f"{semconv.GEN_AI_PROMPT}.0.content": "hello",
            f"{semconv.GEN_AI_COMPLETION}.0.role": "assistant",
            f"{semconv.GEN_AI_COMPLETION}.0.content": "pong",
            "provider_response_json": {
                "prompt_token_ids": [1, 2, 3],
                "choices": [{"token_ids": [4, 5], "logprobs": [-0.1, -0.2]}],
            },
        },
    )

    batch = OnlineTrajectoryConverter(tenant_id="user-1").convert(trajectory)

    assert len(batch.samples) == 1
    assert batch.samples[0].prompt_ids == [1, 2, 3]
    assert batch.samples[0].response_tokens == [4, 5]


@pytest.mark.asyncio
async def test_online_trajectory_converter_preserves_trace_plain_meta():
    from openjiuwen.agent_evolving.agent_rl.online.rail.converter import OnlineTrajectoryConverter

    trajectory = _canonical_llm_trajectory(
        "trace-meta-converter",
        {
            f"{semconv.GEN_AI_PROMPT}.0.role": "user",
            f"{semconv.GEN_AI_PROMPT}.0.content": "hello",
            f"{semconv.GEN_AI_COMPLETION}.0.role": "assistant",
            f"{semconv.GEN_AI_COMPLETION}.0.content": "pong",
        },
        resource_attrs={
            "openjiuwen.trajectory.source": "rl_online",
            "tenant_id": "tenant-1",
            "status": "ok",
            "started_at": 123.4,
            "custom": {"label": "keep"},
        },
    )
    batch = OnlineTrajectoryConverter(tenant_id="tenant-1").convert(trajectory)

    assert batch.trajectory_meta.status == "ok"
    assert batch.trajectory_meta.extra["tenant_id"] == "tenant-1"
    assert batch.trajectory_meta.extra["started_at"] == 123.4
    assert batch.trajectory_meta.extra["custom"] == {"label": "keep"}


def test_online_trajectory_converter_normalizes_streaming_logprobs_for_gateway():
    from openjiuwen.agent_evolving.agent_rl.online.gateway.trajectory.rail_ingest import RailBatchIngestor
    from openjiuwen.agent_evolving.agent_rl.online.rail.converter import OnlineTrajectoryConverter

    trajectory = _canonical_llm_trajectory(
        "traj-stream",
        {
            f"{semconv.GEN_AI_PROMPT}.0.role": "user",
            f"{semconv.GEN_AI_PROMPT}.0.content": "hello",
            f"{semconv.GEN_AI_COMPLETION}.0.role": "assistant",
            f"{semconv.GEN_AI_COMPLETION}.0.content": "pong",
            "evolution.rl.prompt_token_ids": [1, 2, 3],
            "evolution.rl.completion_token_ids": [4, 5],
            "evolution.rl.logprobs": {"content": [{"logprob": -0.1}, {"logprob": -0.2}]},
        },
    )

    batch = OnlineTrajectoryConverter(tenant_id="user-1").convert(trajectory).to_dict()
    normalized = RailBatchIngestor._normalize_rail_sample(batch, batch["samples"][0])

    assert normalized["sample_id"] == f"{normalized['trajectory_id']}:{normalized['step_index']}"
    assert normalized["trajectory"]["prompt_ids"] == [1, 2, 3]
    assert normalized["trajectory"]["response_ids"] == [4, 5]
    assert normalized["trajectory"]["response_logprobs"] == [-0.1, -0.2]


def test_online_trajectory_converter_reads_detached_messages():
    from openjiuwen.agent_evolving.agent_rl.online.rail.converter import OnlineTrajectoryConverter

    trajectory = _canonical_llm_trajectory(
        "traj-detached-message",
        {
            f"{semconv.GEN_AI_PROMPT}.0.role": "user",
            f"{semconv.GEN_AI_PROMPT}.0.content": "hello",
            f"{semconv.GEN_AI_PROMPT}.1.role": "assistant",
            f"{semconv.GEN_AI_PROMPT}.1.content": "previous turn",
            f"{semconv.GEN_AI_COMPLETION}.0.role": "assistant",
            f"{semconv.GEN_AI_COMPLETION}.0.content": "pong",
        },
    )

    batch = OnlineTrajectoryConverter(tenant_id="user-1").convert(trajectory)

    assert len(batch.samples) == 1
    assert batch.samples[0].messages[0] == {"role": "user", "content": "hello"}
    assert batch.samples[0].messages[1] == {"role": "assistant", "content": "previous turn"}


@pytest.mark.asyncio
async def test_stream_chat_response_preserves_runtime_token_fields():
    from openjiuwen.agent_evolving.agent_rl.online.gateway.app.http_helpers import stream_chat_response

    response_json = {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 123,
        "model": "m1",
        "rl_lora": {"model_id": "user-1", "version": "v3", "path": "/tmp/lora/v3"},
        "prompt_token_ids": [1, 2, 3],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        "choices": [{
            "index": 0,
            "finish_reason": "stop",
            "token_ids": [4, 5],
            "logprobs": {"content": [{"logprob": -0.1}, {"logprob": -0.2}]},
            "message": {"role": "assistant", "content": "pong"},
        }],
    }

    chunks = []
    async for item in stream_chat_response(response_json, model_id="m1"):
        chunks.append(item)

    assert len(chunks) == 3
    first = chunks[0]
    last = chunks[1]
    assert '"prompt_token_ids": [1, 2, 3]' in first
    assert '"rl_lora": {"model_id": "user-1", "version": "v3", "path": "/tmp/lora/v3"}' in first
    assert '"token_ids": [4, 5]' in first
    assert '"logprobs": {"content": [{"logprob": -0.1}, {"logprob": -0.2}]}' in first
    assert '"usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}' in last

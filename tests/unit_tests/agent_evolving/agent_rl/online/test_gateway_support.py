from __future__ import annotations

import json
from pathlib import Path

import pytest

from openjiuwen.agent_evolving.trajectory.model import Trajectory
from openjiuwen.agent_evolving.trajectory.spans import (
    attributes_from_map,
    write_llm_exchange,
)
from openjiuwen.extensions.observability import semconv


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

    score = await scorer.score(
        {
            "model": "policy",
            "messages": [
                {"role": "user", "content": "<tool_call>plan</tool_call>"},
                {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "secret"}}]},
            ],
            "tools": [{"type": "function", "function": {"name": "lookup", "parameters": {}}}],
        },
        {"choices": [{"message": {"role": "assistant", "content": "<tag>resp</tag>"}}]},
        "next",
    )

    assert score == 0.8
    assert len(client.calls) == 2
    prompt = client.calls[0][1]["json"]["messages"][0]["content"]
    assert "[tool_call block]" in prompt
    assert "[tag]resp[/tag]" in prompt
    assert "[image]" in prompt
    assert "lookup" in prompt


@pytest.mark.asyncio
async def test_judge_scorer_rejects_unparsable_vote() -> None:
    from openjiuwen.agent_evolving.agent_rl.online.judge.judge_scorer import JudgeScorer

    client = _FakeAsyncClient(response=_FakeResponse(payload={"choices": [{"message": {"content": "invalid"}}]}))
    scorer = JudgeScorer(
        judge_url="http://judge.local",
        judge_model="judge-model",
        http_client=client,
    )

    with pytest.raises(ValueError, match="unparsable"):
        await scorer.score(
            {"model": "policy", "messages": [{"role": "user", "content": "question"}]},
            {"choices": [{"message": {"role": "assistant", "content": "answer"}}]},
            "feedback",
        )


@pytest.mark.asyncio
async def test_judge_scorer_rejects_json_without_scores() -> None:
    from openjiuwen.agent_evolving.agent_rl.online.judge.judge_scorer import JudgeScorer

    client = _FakeAsyncClient(response=_FakeResponse(payload={"choices": [{"message": {"content": "{}"}}]}))
    scorer = JudgeScorer(
        judge_url="http://judge.local",
        judge_model="judge-model",
        http_client=client,
    )

    with pytest.raises(ValueError, match="unparsable"):
        await scorer.score(
            {"model": "policy", "messages": [{"role": "user", "content": "question"}]},
            {"choices": [{"message": {"role": "assistant", "content": "answer"}}]},
            "feedback",
        )


@pytest.mark.asyncio
async def test_gateway_trajectory_runtime_fills_single_user_default_on_record(tmp_path: Path):
    from openjiuwen.agent_evolving.agent_rl.online.gateway.trajectory import GatewayTrajectoryRuntime
    from openjiuwen.agent_evolving.agent_rl.storage.local_store import (
        LocalPendingJudgeStore,
        LocalSFTStore,
        LocalTrajectoryStore,
    )

    store_dir = tmp_path / "local_store"
    runtime = GatewayTrajectoryRuntime(
        type(
            "Config",
            (),
            {"record_dir": str(tmp_path), "dump_token_ids": False, "single_user_default": True},
        )(),
        trajectory_store=LocalTrajectoryStore(store_dir),
        sft_store=LocalSFTStore(store_dir),
        pending_judge_store=LocalPendingJudgeStore(store_dir),
    )

    await runtime.record_sample({"sample_id": "s1"})

    trajectory = await runtime.get_trajectory("s1")
    assert trajectory is not None
    assert trajectory["user_id"] == "jiuwenclaw-web"
    assert json.loads((tmp_path / "samples.jsonl").read_text(encoding="utf-8").strip())["user_id"] == "jiuwenclaw-web"


def test_online_trajectory_converter_reads_prompt_and_response_token_ids_from_response():
    from openjiuwen.agent_evolving.agent_rl.online.rail.converter import OnlineTrajectoryConverter

    trajectory = _canonical_llm_trajectory(
        "traj-1",
        {
            **write_llm_exchange(
                [{"role": "user", "content": "hello"}],
                [{"role": "assistant", "content": "pong"}],
            ),
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
            **write_llm_exchange(
                [{"role": "user", "content": "hello"}],
                [{"role": "assistant", "content": "pong"}],
            ),
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
            **write_llm_exchange(
                [{"role": "user", "content": "hello"}],
                [{"role": "assistant", "content": "pong"}],
            ),
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


def test_rail_normalization_for_fixed_model_ignores_external_tenant() -> None:
    from openjiuwen.agent_evolving.agent_rl.online.gateway.trajectory.rail_ingest import RailBatchIngestor

    batch = {
        "session_id": "session-1",
        "trajectory_id": "trajectory-1",
        "user_id": "external-tenant",
        "model_id": "external-model",
        "samples": [
            {
                "messages": [{"role": "user", "content": "hello"}],
                "prompt_ids": [1],
                "response_tokens": [2],
                "logprobs": [-0.1],
                "response": {"content": "world"},
            }
        ],
    }

    normalized = RailBatchIngestor._normalize_rail_sample(
        batch,
        batch["samples"][0],
        fixed_user_id="model-1",
        fixed_model_id="model-1",
    )

    assert normalized["user_id"] == "model-1"
    assert normalized["model"] == "model-1"


def test_online_trajectory_converter_reads_detached_messages():
    from openjiuwen.agent_evolving.agent_rl.online.rail.converter import OnlineTrajectoryConverter

    trajectory = _canonical_llm_trajectory(
        "traj-detached-message",
        {
            **write_llm_exchange(
                [
                    {"role": "user", "content": "hello"},
                    {"role": "assistant", "content": "previous turn"},
                ],
                [{"role": "assistant", "content": "pong"}],
            ),
        },
    )

    batch = OnlineTrajectoryConverter(tenant_id="user-1").convert(trajectory)

    assert len(batch.samples) == 1
    assert batch.samples[0].messages[0] == {"role": "user", "content": "hello"}
    assert batch.samples[0].messages[1] == {"role": "assistant", "content": "previous turn"}

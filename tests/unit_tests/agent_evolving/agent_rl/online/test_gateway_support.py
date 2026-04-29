from __future__ import annotations

from pathlib import Path

import pytest

from openjiuwen.agent_evolving.trajectory import LLMCallDetail, Trajectory, TrajectoryStep


class _FakeRedisPipeline:
    def __init__(self, redis) -> None:
        self._redis = redis
        self._ops: list[tuple[str, tuple]] = []

    def delete(self, key: str):
        self._ops.append(("delete", (key,)))
        return self

    def zrem(self, key: str, member: str):
        self._ops.append(("zrem", (key, member)))
        return self

    def hset(self, key: str, mapping: dict[str, str]):
        self._ops.append(("hset", (key, mapping)))
        return self

    def zadd(self, key: str, mapping: dict[str, float]):
        self._ops.append(("zadd", (key, mapping)))
        return self

    def sadd(self, key: str, *members: str):
        self._ops.append(("sadd", (key, *members)))
        return self

    def zcard(self, key: str):
        self._ops.append(("zcard", (key,)))
        return self

    def hmget(self, key: str, fields: list[str]):
        self._ops.append(("hmget", (key, fields)))
        return self

    async def execute(self):
        out = []
        for name, args in self._ops:
            out.append(await getattr(self._redis, name)(*args))
        return out


class _FakeRedis:
    def __init__(self) -> None:
        self._kv: dict[str, str] = {}
        self._zsets: dict[str, dict[str, float]] = {}
        self._hashes: dict[str, dict[str, str]] = {}
        self._sets: dict[str, set[str]] = {}

    def register_script(self, script: str):
        del script

        async def _run(*, keys: list[str], args: list[object]):
            pending_key, training_key = keys
            limit = max(1, int(args[0]))
            now_score = float(args[1])
            new_status = str(args[2])
            traj_prefix = str(args[3])
            ids = await self.zrange(pending_key, 0, limit - 1)
            if not ids:
                return []
            for sample_id in ids:
                await self.zrem(pending_key, sample_id)
                await self.zadd(training_key, {sample_id: now_score})
                await self.hset(f"{traj_prefix}{sample_id}", {"status": new_status})
            return ids

        return _run

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        del ex
        self._kv[key] = value

    async def zadd(self, key: str, mapping: dict[str, float]) -> None:
        bucket = self._zsets.setdefault(key, {})
        bucket.update(mapping)

    async def expire(self, key: str, ttl: int) -> None:
        del key, ttl

    async def zrange(self, key: str, start: int, end: int) -> list[str]:
        bucket = self._zsets.get(key, {})
        members = [member for member, _ in sorted(bucket.items(), key=lambda item: item[1])]
        if end == -1:
            end = len(members) - 1
        return members[start:end + 1]

    async def mget(self, keys: list[str]) -> list[str | None]:
        return [self._kv.get(key) for key in keys]

    async def get(self, key: str) -> str | None:
        return self._kv.get(key)

    async def hmget(self, key: str, fields: list[str]) -> list[str | None]:
        row = self._hashes.get(key, {})
        return [row.get(field) for field in fields]

    async def hset(self, key: str, mapping: dict[str, str]) -> None:
        row = self._hashes.setdefault(key, {})
        row.update(mapping)

    async def zcard(self, key: str) -> int:
        return len(self._zsets.get(key, {}))

    async def sadd(self, key: str, *members: str) -> None:
        self._sets.setdefault(key, set()).update(members)

    async def smembers(self, key: str) -> set[str]:
        return set(self._sets.get(key, set()))

    async def srem(self, key: str, *members: str) -> None:
        bucket = self._sets.setdefault(key, set())
        for member in members:
            bucket.discard(member)

    async def delete(self, key: str) -> None:
        self._kv.pop(key, None)
        self._hashes.pop(key, None)

    async def zrem(self, key: str, member: str) -> None:
        self._zsets.get(key, {}).pop(member, None)

    def pipeline(self) -> _FakeRedisPipeline:
        return _FakeRedisPipeline(self)


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
async def test_gateway_trajectory_runtime_rejects_missing_user_id_on_record(tmp_path: Path):
    from openjiuwen.agent_evolving.agent_rl.online.gateway.config import GatewayConfig
    from openjiuwen.agent_evolving.agent_rl.online.gateway.trajectory import GatewayTrajectoryRuntime

    runtime = GatewayTrajectoryRuntime(
        GatewayConfig(port=18080, model_id="dummy-model", record_dir=str(tmp_path)),
        redis=_FakeRedis(),
    )

    with pytest.raises(ValueError, match="missing user_id"):
        await runtime.record_sample({"sample_id": "s1"})


def test_online_trajectory_converter_reads_prompt_and_response_token_ids_from_response():
    from openjiuwen.agent_evolving.agent_rl.online.rail.converter import OnlineTrajectoryConverter

    trajectory = Trajectory(
        execution_id="traj-1",
        session_id="session-1",
        source="online",
        steps=[
            TrajectoryStep(
                kind="llm",
                detail=LLMCallDetail(
                    model="m1",
                    messages=[{"role": "user", "content": "hello"}],
                    response={
                        "role": "assistant",
                        "content": "pong",
                    },
                    meta={
                        "provider_response_json": {
                            "prompt_token_ids": [1, 2, 3],
                            "choices": [{
                                "token_ids": [4, 5],
                                "logprobs": [-0.1, -0.2],
                            }],
                        },
                    },
                ),
            ),
        ],
    )

    batch = OnlineTrajectoryConverter(tenant_id="user-1").convert(trajectory)

    assert len(batch.samples) == 1
    assert batch.samples[0].prompt_ids == [1, 2, 3]
    assert batch.samples[0].response_tokens == [4, 5]


def test_online_trajectory_converter_normalizes_streaming_logprobs_for_gateway():
    from openjiuwen.agent_evolving.agent_rl.online.gateway.trajectory.rail_ingest import RailBatchIngestor
    from openjiuwen.agent_evolving.agent_rl.online.rail.converter import OnlineTrajectoryConverter

    trajectory = Trajectory(
        execution_id="traj-stream",
        session_id="session-1",
        source="online",
        steps=[
            TrajectoryStep(
                kind="llm",
                detail=LLMCallDetail(
                    model="m1",
                    messages=[{"role": "user", "content": "hello"}],
                    response={"role": "assistant", "content": "pong"},
                ),
                prompt_token_ids=[1, 2, 3],
                completion_token_ids=[4, 5],
                logprobs={"content": [{"logprob": -0.1}, {"logprob": -0.2}]},
            ),
        ],
    )

    batch = OnlineTrajectoryConverter(tenant_id="user-1").convert(trajectory).to_dict()
    normalized = RailBatchIngestor._normalize_rail_sample(batch, batch["samples"][0])

    assert normalized["trajectory"]["prompt_ids"] == [1, 2, 3]
    assert normalized["trajectory"]["response_ids"] == [4, 5]
    assert normalized["trajectory"]["response_logprobs"] == [-0.1, -0.2]


def test_online_trajectory_converter_tolerates_message_model_dump_failure():
    from openjiuwen.agent_evolving.agent_rl.online.rail.converter import OnlineTrajectoryConverter

    class _BrokenMessage:
        role = "assistant"
        content = "previous turn"

        def model_dump(self):
            raise TypeError("MockValSer")

    trajectory = Trajectory(
        execution_id="traj-broken-message",
        session_id="session-1",
        source="online",
        steps=[
            TrajectoryStep(
                kind="llm",
                detail=LLMCallDetail(
                    model="m1",
                    messages=[
                        {"role": "user", "content": "hello"},
                        _BrokenMessage(),
                    ],
                    response={
                        "role": "assistant",
                        "content": "pong",
                    },
                ),
            ),
        ],
    )

    batch = OnlineTrajectoryConverter(tenant_id="user-1").convert(trajectory)

    assert len(batch.samples) == 1
    assert batch.samples[0].messages[1] == {
        "role": "assistant",
        "content": "previous turn",
    }


@pytest.mark.asyncio
async def test_stream_chat_response_preserves_runtime_token_fields():
    from openjiuwen.agent_evolving.agent_rl.online.gateway.app.http_helpers import stream_chat_response

    response_json = {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 123,
        "model": "m1",
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
    assert '"token_ids": [4, 5]' in first
    assert '"logprobs": {"content": [{"logprob": -0.1}, {"logprob": -0.2}]}' in first
    assert '"usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}' in last

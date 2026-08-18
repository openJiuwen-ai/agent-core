from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import httpx
import pytest

from openjiuwen.agent_evolving.agent_rl.online.gateway.collector.response import parse_vllm_response
from openjiuwen.agent_evolving.agent_rl.online.gateway.collector.runtime import GatewayTrajectoryCollector
from openjiuwen.agent_evolving.agent_rl.online.gateway.collector.types import (
    CollectionSessionSpec,
    CollectionSessionError,
    CollectionSessionErrorCode,
    CollectionSessionRecord,
    CollectionSessionStatus,
)
from openjiuwen.agent_evolving.agent_rl.online.gateway.trajectory.task_reward import TaskReward
from tests.unit_tests.agent_evolving.agent_rl.online.support import (
    InMemoryRedis,
    collection_spec,
    gateway_test_app,
    openai_response,
)


class _SamplePipeline:
    def __init__(self) -> None:
        self.followups: list[tuple[str, list[dict[str, Any]]]] = []
        self.samples: list[dict[str, Any]] = []
        self.flushed: list[str] = []
        self.discarded: list[str] = []
        self.rewards: list[tuple[str, TaskReward]] = []
        self.flush_failures = 0

    async def on_gateway_followup(self, session_id: str, messages: list[dict[str, Any]]) -> int:
        self.followups.append((session_id, messages))
        return 0

    async def stage_gateway_sample(self, sample: dict[str, Any]) -> None:
        self.samples.append(sample)

    async def flush_gateway_session(self, session_id: str) -> int:
        self.flushed.append(session_id)
        if self.flush_failures:
            self.flush_failures -= 1
            raise RuntimeError("flush failed")
        return len(self.samples)

    async def discard_gateway_session(self, session_id: str) -> int:
        self.discarded.append(session_id)
        return len(self.samples)

    async def submit_task_reward(self, session_id: str, reward: TaskReward) -> int:
        self.rewards.append((session_id, reward))
        return len(self.samples)


def _collector(redis: InMemoryRedis, pipeline: _SamplePipeline | None = None) -> GatewayTrajectoryCollector:
    return GatewayTrajectoryCollector(
        redis=redis,
        sample_pipeline=pipeline or _SamplePipeline(),
    )


def test_collection_session_spec_rejects_rail_mode() -> None:
    with pytest.raises(ValueError, match="collection_mode=gateway"):
        CollectionSessionSpec(
            session_id="rail-session",
            collection_mode="rail",
            model_id="model-1",
            tokenizer_revision="tokenizer-r1",
            template_revision="template-r1",
        )


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (
            {
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "pong"},
                        "finish_reason": "stop",
                        "token_ids": [201],
                        "logprobs": {"content": [{"logprob": -0.1}]},
                    }
                ]
            },
            "prompt token IDs",
        ),
        (
            {
                "prompt_token_ids": [101],
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "pong"},
                        "finish_reason": "stop",
                        "token_ids": [201],
                        "logprobs": {"content": [{"logprob": float("nan")}]},
                    }
                ],
            },
            "token log-probabilities",
        ),
        (
            {
                "prompt_token_ids": [101],
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "pong"},
                        "finish_reason": "stop",
                        "token_ids": [201, 202],
                        "logprobs": {"content": [{"logprob": -0.1}]},
                    }
                ],
            },
            "align",
        ),
    ],
)
def test_response_adapter_rejects_missing_or_malformed_rollout_truth(
    response: dict[str, Any],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        parse_vllm_response(response)


@pytest.mark.asyncio
async def test_collector_builds_existing_sample_from_upstream_truth() -> None:
    redis = InMemoryRedis()
    pipeline = _SamplePipeline()
    collector = _collector(redis, pipeline)
    spec = collection_spec(
        session_id="session-9",
        model_id="model-9",
        tokenizer_revision="tokenizer-r9",
        template_revision="template-r9",
    )
    await collector.create_session(spec)
    request = {
        "user_id": "user-9",
        "messages": [{"role": "user", "content": "ping"}],
        "tools": [{"type": "function", "function": {"name": "lookup"}}],
    }
    response = openai_response(prompt_ids=[101, 102], token_ids=[201, 202], logprobs=[-0.1, -0.2])
    response["choices"][0].update(
        message={
            "role": "assistant",
            "content": "pong",
            "tool_calls": [{"function": {"name": "lookup", "arguments": {"q": "x"}}}],
        },
        routed_experts=[[1, 2]],
        routing_metadata={"route": {"layers": [1, 2]}},
    )

    capture = await collector.capture(spec.session_id, request)
    assert capture is not None
    sample = await capture.commit(response)

    assert pipeline.followups == [(spec.session_id, request["messages"])]
    assert pipeline.samples == [sample]
    assert (sample["user_id"], sample["session_id"], sample["model"]) == ("user-9", "session-9", "model-9")
    assert sample["trajectory"]["prompt_ids"] == [101, 102]
    assert sample["trajectory"]["response_ids"] == [201, 202]
    assert sample["trajectory"]["response_logprobs"] == [-0.1, -0.2]
    assert sample["trajectory"]["routed_experts"] == [[1, 2]]
    assert sample["trajectory"]["routing_metadata"] == {"route": {"layers": [1, 2]}}
    assert sample["collection_identity"]["tokenizer_revision"] == "tokenizer-r9"


@pytest.mark.asyncio
async def test_session_lifecycle_is_durable_and_finalize_can_resume() -> None:
    redis = InMemoryRedis()
    first_pipeline = _SamplePipeline()
    first_pipeline.flush_failures = 1
    first = _collector(redis, first_pipeline)
    spec = collection_spec()
    created = await first.create_session(spec)
    assert created.status is CollectionSessionStatus.ACTIVE

    restarted = _collector(redis, _SamplePipeline())
    restarted_record = await restarted.get_session(spec.session_id)
    assert restarted_record is not None and restarted_record.status is CollectionSessionStatus.ACTIVE
    with pytest.raises(CollectionSessionError) as duplicate:
        await restarted.create_session(spec)
    assert duplicate.value.code is CollectionSessionErrorCode.DUPLICATE_CREATE

    with pytest.raises(RuntimeError, match="flush failed"):
        await first.finalize_session(spec.session_id)
    pending = await first.get_session(spec.session_id)
    assert pending is not None and pending.status is CollectionSessionStatus.FINALIZING

    second_pipeline = _SamplePipeline()
    completed = await _collector(redis, second_pipeline).finalize_session(spec.session_id)
    assert completed.status is CollectionSessionStatus.FINALIZED
    assert second_pipeline.flushed == [spec.session_id]


@pytest.mark.asyncio
async def test_terminal_task_skips_flush_and_abort_discards_pending() -> None:
    redis = InMemoryRedis()
    pipeline = _SamplePipeline()
    collector = _collector(redis, pipeline)
    terminal = collection_spec(session_id="terminal", reward_mode="terminal_task")
    aborted = collection_spec(session_id="aborted")
    await collector.create_session(terminal)
    await collector.create_session(aborted)

    finalized_record = await collector.finalize_session(terminal.session_id)
    aborted_record = await collector.abort_session(aborted.session_id)

    assert finalized_record.status is CollectionSessionStatus.FINALIZED
    assert aborted_record.status is CollectionSessionStatus.ABORTED
    assert pipeline.flushed == []
    assert pipeline.discarded == [aborted.session_id]


def test_session_record_reads_legacy_persisted_states() -> None:
    base = {
        "session_id": "legacy-session",
        "collection_mode": "gateway",
        "model_id": "model-1",
        "tokenizer_revision": "tokenizer-r1",
        "template_revision": "template-r1",
        "reward_mode": "delayed_feedback",
        "phase": "terminal",
        "terminal_condition": "finalized",
    }

    finalizing = CollectionSessionRecord.from_json(
        json.dumps({**base, "terminal_effects_completed": False}),
    )
    finalized = CollectionSessionRecord.from_json(
        json.dumps({**base, "terminal_effects_completed": True}),
    )

    assert finalizing.status is CollectionSessionStatus.FINALIZING
    assert finalized.status is CollectionSessionStatus.FINALIZED


def test_session_record_persists_one_state_and_derives_legacy_transport_fields() -> None:
    record = CollectionSessionRecord(spec=collection_spec())

    stored = json.loads(record.to_storage_json())
    transported = json.loads(record.to_json())

    assert stored["status"] == "active"
    assert "phase" not in stored
    assert "terminal_condition" not in stored
    assert "terminal_effects_completed" not in stored
    assert transported["phase"] == "active"
    assert transported["terminal_condition"] is None
    assert transported["terminal_effects_completed"] is False


def test_session_record_rejects_impossible_reward_status_combination() -> None:
    with pytest.raises(ValueError, match="only delayed-feedback sessions"):
        CollectionSessionRecord(
            spec=collection_spec(reward_mode="terminal_task"),
            status=CollectionSessionStatus.FINALIZING,
        )


@pytest.mark.asyncio
async def test_collector_owns_terminal_reward_validation_and_submission() -> None:
    redis = InMemoryRedis()
    pipeline = _SamplePipeline()
    collector = _collector(redis, pipeline)
    delayed = collection_spec(session_id="delayed")
    terminal = collection_spec(session_id="terminal-reward", reward_mode="terminal_task")
    await collector.create_session(delayed)
    await collector.create_session(terminal)
    reward = TaskReward(
        reward_id="reward-1",
        attempt_id="attempt-1",
        task_id="task-1",
        training_key="train-1",
        score=1.0,
        passed=True,
    )

    with pytest.raises(CollectionSessionError) as wrong_mode:
        await collector.submit_task_reward(delayed.session_id, reward)
    assert wrong_mode.value.code is CollectionSessionErrorCode.INVALID_REWARD_MODE

    with pytest.raises(CollectionSessionError) as not_finalized:
        await collector.submit_task_reward(terminal.session_id, reward)
    assert not_finalized.value.code is CollectionSessionErrorCode.SESSION_NOT_FINALIZED

    await collector.finalize_session(terminal.session_id)

    assert await collector.submit_task_reward(terminal.session_id, reward) == 0
    assert pipeline.rewards == [(terminal.session_id, reward)]


@pytest.mark.asyncio
async def test_task_reward_route_delegates_session_rules_to_manager() -> None:
    redis = InMemoryRedis()
    pipeline = _SamplePipeline()
    collector = _collector(redis, pipeline)
    spec = collection_spec(session_id="route-reward", reward_mode="terminal_task")
    await collector.create_session(spec)
    app = gateway_test_app(forwarder=_Forwarder([], openai_response()), collector=collector)
    payload = {
        "reward_id": "reward-1",
        "attempt_id": "attempt-1",
        "task_id": "task-1",
        "training_key": "train-1",
        "score": 1.0,
        "passed": True,
    }

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway") as client:
        unknown = await client.post("/v1/gateway/collection/sessions/unknown/task-reward", json=payload)
        active = await client.post(f"/v1/gateway/collection/sessions/{spec.session_id}/task-reward", json=payload)
        await collector.finalize_session(spec.session_id)
        finalized = await client.post(f"/v1/gateway/collection/sessions/{spec.session_id}/task-reward", json=payload)

    assert unknown.status_code == 404
    assert active.status_code == 409
    assert finalized.status_code == 200
    assert finalized.json()["projected_samples"] == 0


@pytest.mark.asyncio
async def test_capture_cannot_commit_after_session_finalization() -> None:
    redis = InMemoryRedis()
    pipeline = _SamplePipeline()
    collector = _collector(redis, pipeline)
    spec = collection_spec(reward_mode="terminal_task")
    await collector.create_session(spec)
    capture = await collector.capture(spec.session_id, {"messages": [{"role": "user", "content": "ping"}]})
    assert capture is not None

    await collector.finalize_session(spec.session_id)

    with pytest.raises(CollectionSessionError) as terminal:
        await capture.commit(openai_response())
    assert terminal.value.code is CollectionSessionErrorCode.SESSION_TERMINAL
    assert pipeline.samples == []


class _Capture:
    def __init__(self, events: list[str], *, failure_code: str | None = None) -> None:
        self._events = events
        self._failure_code = failure_code

    async def commit(self, response: Mapping[str, Any]) -> object:
        del response
        self._events.append("capture-commit")
        if self._failure_code:
            error = RuntimeError(self._failure_code)
            error.code = self._failure_code  # type: ignore[attr-defined]
            raise error
        return {"sample_id": "sample-9"}


class _Collector:
    def __init__(self, events: list[str], *, prepare_failure: bool = False, commit_failure: str | None = None) -> None:
        self._events = events
        self._prepare_failure = prepare_failure
        self._commit_failure = commit_failure

    async def capture(self, session_id: str, request: Mapping[str, Any]) -> _Capture:
        del session_id, request
        self._events.append("capture-prepare")
        if self._prepare_failure:
            raise RuntimeError("preparation failed")
        return _Capture(self._events, failure_code=self._commit_failure)


class _Forwarder:
    def __init__(self, events: list[str], response: dict[str, Any]) -> None:
        self._events = events
        self._response = response
        self.bodies: list[dict[str, Any]] = []

    async def forward(self, body: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        del headers
        self._events.append("forward")
        self.bodies.append(dict(body))
        return self._response


async def _post(collector: Any, forwarder: Any) -> tuple[httpx.Response, dict[str, int]]:
    app = gateway_test_app(forwarder=forwarder, collector=collector, model_id="model-10")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway") as client:
        response = await client.post(
            "/v1/chat/completions",
            headers={"x-user-id": "user-10"},
            json={"session_id": "session-9", "messages": [{"role": "user", "content": "ping"}]},
        )
        stats = await client.get("/v1/gateway/stats")
    return response, stats.json()["collection"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("prepare_failure", "commit_failure", "expected_events", "counter"),
    [
        (True, None, ["capture-prepare", "forward"], "unexpected_failures"),
        (False, "logprob_length_mismatch", ["capture-prepare", "forward", "capture-commit"], "logprob_mismatch"),
    ],
)
async def test_gateway_capture_failures_do_not_fail_inference(
    prepare_failure: bool,
    commit_failure: str | None,
    expected_events: list[str],
    counter: str,
) -> None:
    events: list[str] = []
    response_body = {"id": "chatcmpl-10", "choices": [{"message": {"content": "pong"}}]}
    collector = _Collector(events, prepare_failure=prepare_failure, commit_failure=commit_failure)
    forwarder = _Forwarder(events, response_body)

    response, snapshot = await _post(collector, forwarder)

    assert response.status_code == 200
    assert response.json() == response_body
    assert events == expected_events
    assert (snapshot["attempts"], snapshot["dropped_samples"], snapshot[counter]) == (1, 1, 1)
    assert ("logprobs" in forwarder.bodies[0]) is (not prepare_failure)

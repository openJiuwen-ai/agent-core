from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import pytest

from openjiuwen.agent_evolving.agent_rl.online.capture_pipeline import CapturePipeline
from openjiuwen.agent_evolving.agent_rl.online.task_registry import (
    FinishReason,
    RewardMode,
    TaskConflictError,
    TaskRegistry,
    TaskSpec,
    TaskStatus,
    TurnClosedError,
)
from openjiuwen.agent_evolving.agent_rl.storage.trajectory_store import InMemoryTrajectoryStore
from tests.unit_tests.agent_evolving.agent_rl.online.support import InMemoryRedis, openai_response


def _request(*, model: str = "model-1:v2", message: str = "ping") -> dict:
    return {
        "model": model,
        "messages": [{"role": "user", "content": message}],
        "n": 1,
    }


def _response(*, model: str = "model-1:v2", text: str = "pong") -> dict:
    response = openai_response(model=model, text=text)
    response["object"] = "chat.completion"
    return response


class _Judge:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[tuple[Mapping[str, Any], Mapping[str, Any], str]] = []
        self.fail = fail

    async def score(
        self,
        request: Mapping[str, Any],
        response: Mapping[str, Any],
        followup_user_message: str,
    ) -> float:
        self.calls.append((request, response, followup_user_message))
        if self.fail:
            raise RuntimeError("judge unavailable")
        return 0.75


class _BlockingStore(InMemoryTrajectoryStore):
    def __init__(self) -> None:
        super().__init__()
        self.saved = asyncio.Event()
        self.release = asyncio.Event()

    async def save_samples_once(self, samples, *, user_id: str = "online") -> set[str]:
        saved_ids = await super().save_samples_once(samples, user_id=user_id)
        self.saved.set()
        await self.release.wait()
        return saved_ids


@pytest.mark.asyncio
async def test_terminal_capture_is_idempotent_and_published_only_after_reward() -> None:
    redis = InMemoryRedis()
    registry = TaskRegistry(redis=redis)
    store = InMemoryTrajectoryStore()
    pipeline = CapturePipeline(registry=registry, trajectory_store=store)
    await registry.start(
        TaskSpec("task-1", "session-1", "model-1", "model-1:v2", RewardMode.TERMINAL),
    )
    request = _request()
    response = _response()

    injected = await pipeline.before("task-1", "capture-1", None, request)
    assert injected == {
        **request,
        "logprobs": True,
        "top_logprobs": 1,
        "return_token_ids": True,
    }
    assert await pipeline.before("task-1", "capture-1", None, request) == injected
    assert "logprobs" not in request
    await pipeline.after("task-1", "capture-1", None, request, response)
    await pipeline.after("task-1", "capture-1", None, request, response)

    assert await store.get_sample("capture-1") is None
    task = await pipeline.finish("task-1", FinishReason.USER_STOPPED)
    assert task.status is TaskStatus.FINALIZED
    await pipeline.after("task-1", "capture-1", None, request, response)
    assert await pipeline.abort("task-1", FinishReason.CAPTURE_FAILED) == task
    assert await pipeline.submit_reward("task-1", 1.0) == 1
    assert await pipeline.submit_reward("task-1", 1.0) == 1

    sample = await store.get_sample("capture-1")
    assert sample is not None
    assert sample["sample_id"] == sample["trajectory_id"] == "capture-1"
    assert sample["session_id"] == "session-1"
    assert sample["model"] == "model-1"
    assert sample["policy_version"] == "model-1:v2"
    assert sample["judge"] == {"score": 1.0, "source": "terminal"}
    assert sample["trajectory"]["prompt_ids"] == [101]
    assert sample["trajectory"]["response_ids"] == [201]


@pytest.mark.asyncio
async def test_discard_releases_abandoned_capture_without_aborting_task() -> None:
    registry = TaskRegistry(redis=InMemoryRedis())
    pipeline = CapturePipeline(registry=registry, trajectory_store=InMemoryTrajectoryStore())
    await registry.start(
        TaskSpec("task-1", "session-1", "model-1", "model-1:v2", RewardMode.TERMINAL),
    )
    request = _request()
    await pipeline.before("task-1", "capture-1", None, request)

    await pipeline.discard("task-1", "capture-1", None)
    await pipeline.discard("task-1", "capture-1", None)

    task = await pipeline.finish("task-1", FinishReason.USER_STOPPED)
    assert task.status is TaskStatus.FINALIZED
    with pytest.raises(TaskConflictError, match="at least one capture"):
        await pipeline.submit_reward("task-1", 1.0)


@pytest.mark.asyncio
async def test_delayed_turn_waits_for_captures_and_publishes_atomically() -> None:
    redis = InMemoryRedis()
    registry = TaskRegistry(redis=redis)
    store = InMemoryTrajectoryStore()
    judge = _Judge()
    pipeline = CapturePipeline(registry=registry, trajectory_store=store, judge=judge)
    await registry.start(
        TaskSpec("task-1", "session-1", "model-1", "model-1:v2", RewardMode.DELAYED_FEEDBACK),
    )
    first_request = _request(message="first")
    await asyncio.gather(
        pipeline.before("task-1", "capture-1", "turn-1", first_request),
        pipeline.before("task-1", "capture-2", "turn-1", first_request),
    )

    next_request = _request(message="feedback for first")
    next_before = asyncio.create_task(
        pipeline.before("task-1", "capture-3", "turn-2", next_request),
    )
    await asyncio.sleep(0.02)
    assert next_before.done() is False

    await asyncio.gather(
        pipeline.after("task-1", "capture-1", "turn-1", first_request, _response(text="one")),
        pipeline.after("task-1", "capture-2", "turn-1", first_request, _response(text="two")),
    )
    assert await next_before == {
        **next_request,
        "logprobs": True,
        "top_logprobs": 1,
        "return_token_ids": True,
    }

    first_turn = await store.list_samples(user_id="model-1", status="pending")
    assert {sample["sample_id"] for sample in first_turn} == {"capture-1", "capture-2"}
    assert all(sample["judge"] == {"score": 0.75, "source": "judge", "tag": "feedback"} for sample in first_turn)
    assert [call[2] for call in judge.calls] == ["feedback for first", "feedback for first"]

    await pipeline.after("task-1", "capture-3", "turn-2", next_request, _response(text="three"))
    task = await pipeline.finish("task-1", FinishReason.USER_STOPPED)

    assert task.status is TaskStatus.FINALIZED
    assert judge.calls[-1][2] == ""
    final_sample = await store.get_sample("capture-3")
    assert final_sample is not None
    assert final_sample["judge"]["tag"] == "session_done"


@pytest.mark.asyncio
async def test_finish_rejects_open_delayed_capture_without_waiting() -> None:
    registry = TaskRegistry(redis=InMemoryRedis())
    pipeline = CapturePipeline(registry=registry, trajectory_store=InMemoryTrajectoryStore())
    await registry.start(
        TaskSpec("task-1", "session-1", "model-1", "model-1:v2", RewardMode.DELAYED_FEEDBACK),
    )
    await pipeline.before("task-1", "capture-1", "turn-1", _request())

    with pytest.raises(TaskConflictError, match="open captures"):
        await asyncio.wait_for(pipeline.finish("task-1", FinishReason.USER_STOPPED), timeout=1.0)

    task = await registry.get("task-1")
    assert task is not None and task.status is TaskStatus.ACTIVE


@pytest.mark.asyncio
async def test_before_input_errors_do_not_abort_task_but_bad_after_does() -> None:
    registry = TaskRegistry(redis=InMemoryRedis())
    pipeline = CapturePipeline(registry=registry, trajectory_store=InMemoryTrajectoryStore())
    await registry.start(
        TaskSpec("task-1", "session-1", "model-1", "model-1:v2", RewardMode.DELAYED_FEEDBACK),
    )

    with pytest.raises(ValueError, match="agent_turn_id"):
        await pipeline.before("task-1", "capture-1", None, _request())
    with pytest.raises(TaskConflictError, match="model"):
        await pipeline.before("task-1", "capture-1", "turn-1", _request(model="wrong"))
    assert (await registry.get("task-1")).status is TaskStatus.ACTIVE  # type: ignore[union-attr]

    request = _request()
    await pipeline.before("task-1", "capture-1", "turn-1", request)
    response = _response()
    response["usage"]["completion_tokens"] = 99
    with pytest.raises(ValueError, match="usage completion_tokens"):
        await pipeline.after("task-1", "capture-1", "turn-1", request, response)

    aborted = await registry.get("task-1")
    assert aborted is not None
    assert aborted.status is TaskStatus.ABORTED
    assert aborted.finish_reason is FinishReason.CAPTURE_FAILED


@pytest.mark.asyncio
async def test_judge_failure_aborts_without_publishing_turn() -> None:
    registry = TaskRegistry(redis=InMemoryRedis())
    store = InMemoryTrajectoryStore()
    pipeline = CapturePipeline(registry=registry, trajectory_store=store, judge=_Judge(fail=True))
    await registry.start(
        TaskSpec("task-1", "session-1", "model-1", "model-1:v2", RewardMode.DELAYED_FEEDBACK),
    )
    request = _request()
    for capture_id in ("capture-1", "capture-2"):
        await pipeline.before("task-1", capture_id, "turn-1", request)
        await pipeline.after("task-1", capture_id, "turn-1", request, _response())

    with pytest.raises(RuntimeError, match="judge unavailable"):
        await pipeline.before("task-1", "capture-3", "turn-2", _request(message="feedback"))

    assert await store.list_samples(user_id="model-1") == []
    task = await registry.get("task-1")
    assert task is not None and task.status is TaskStatus.ABORTED


@pytest.mark.asyncio
async def test_late_closed_turn_aborts_but_keeps_published_trajectory() -> None:
    registry = TaskRegistry(redis=InMemoryRedis())
    store = InMemoryTrajectoryStore()
    pipeline = CapturePipeline(registry=registry, trajectory_store=store, judge=_Judge())
    await registry.start(
        TaskSpec("task-1", "session-1", "model-1", "model-1:v2", RewardMode.DELAYED_FEEDBACK),
    )
    request = _request()
    await pipeline.before("task-1", "capture-1", "turn-1", request)
    await pipeline.after("task-1", "capture-1", "turn-1", request, _response())
    await pipeline.before("task-1", "capture-2", "turn-2", _request(message="next"))

    with pytest.raises(TurnClosedError, match="closed"):
        await pipeline.before("task-1", "late", "turn-1", request)

    task = await registry.get("task-1")
    assert task is not None and task.status is TaskStatus.ABORTED
    assert await store.get_sample("capture-1") is not None
    assert await store.get_sample("capture-2") is None


@pytest.mark.asyncio
async def test_terminal_reward_rejects_state_mode_zero_samples_and_conflicts() -> None:
    registry = TaskRegistry(redis=InMemoryRedis())
    store = InMemoryTrajectoryStore()
    pipeline = CapturePipeline(registry=registry, trajectory_store=store)
    await registry.start(TaskSpec("terminal", "s1", "m", "base", RewardMode.TERMINAL))

    with pytest.raises(TaskConflictError, match="finalized"):
        await pipeline.submit_reward("terminal", 0.5)
    await pipeline.finish("terminal", FinishReason.USER_STOPPED)
    with pytest.raises(TaskConflictError, match="at least one capture"):
        await pipeline.submit_reward("terminal", 0.5)

    await registry.start(TaskSpec("delayed", "s2", "m", "base", RewardMode.DELAYED_FEEDBACK))
    await pipeline.finish("delayed", FinishReason.USER_STOPPED)
    with pytest.raises(TaskConflictError, match="delayed_feedback"):
        await pipeline.submit_reward("delayed", 0.5)

    await registry.start(TaskSpec("rewarded", "s3", "m", "base", RewardMode.TERMINAL))
    request = _request(model="base")
    response = _response(model="base")
    await pipeline.before("rewarded", "capture", None, request)
    await pipeline.after("rewarded", "capture", None, request, response)
    await pipeline.finish("rewarded", FinishReason.USER_STOPPED)
    assert await pipeline.submit_reward("rewarded", 0.25) == 1
    with pytest.raises(TaskConflictError, match="different terminal reward"):
        await pipeline.submit_reward("rewarded", 0.75)


@pytest.mark.asyncio
async def test_finalize_cannot_race_an_open_capture() -> None:
    registry = TaskRegistry(redis=InMemoryRedis())
    pipeline = CapturePipeline(registry=registry, trajectory_store=InMemoryTrajectoryStore())
    await registry.start(TaskSpec("task-1", "session-1", "model-1", "base", RewardMode.TERMINAL))
    request = _request(model="base")
    await pipeline.before("task-1", "capture-1", None, request)

    with pytest.raises(TaskConflictError, match="open captures"):
        await registry.finalize("task-1", FinishReason.USER_STOPPED)

    assert (await registry.get("task-1")).status is TaskStatus.ACTIVE  # type: ignore[union-attr]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda response: response.pop("prompt_token_ids"), "prompt token IDs"),
        (lambda response: response["choices"][0].pop("token_ids"), "completion token IDs"),
        (lambda response: response.__setitem__("prompt_token_ids", [-1]), "prompt token IDs"),
        (lambda response: response["choices"][0].pop("logprobs"), "log-probabilities"),
        (
            lambda response: response["choices"][0]["logprobs"]["content"][0].__setitem__("logprob", 0.1),
            "log-probabilities",
        ),
        (
            lambda response: response["choices"][0]["logprobs"]["content"].append({"logprob": -0.2}),
            "must align",
        ),
        (lambda response: response["choices"][0].pop("finish_reason"), "finish reason"),
        (lambda response: response.pop("usage"), "usage"),
    ],
)
async def test_after_rejects_incomplete_training_truth_and_aborts(mutate, message: str) -> None:
    registry = TaskRegistry(redis=InMemoryRedis())
    pipeline = CapturePipeline(registry=registry, trajectory_store=InMemoryTrajectoryStore())
    await registry.start(TaskSpec("task-1", "session-1", "model-1", "base", RewardMode.TERMINAL))
    request = _request(model="base")
    response = _response(model="base")
    await pipeline.before("task-1", "capture-1", None, request)
    mutate(response)

    with pytest.raises(ValueError, match=message):
        await pipeline.after("task-1", "capture-1", None, request, response)

    task = await registry.get("task-1")
    assert task is not None and task.status is TaskStatus.ABORTED


@pytest.mark.asyncio
async def test_abort_during_turn_publish_rolls_back_new_samples() -> None:
    registry = TaskRegistry(redis=InMemoryRedis())
    store = _BlockingStore()
    pipeline = CapturePipeline(registry=registry, trajectory_store=store, judge=_Judge())
    await registry.start(
        TaskSpec("task-1", "session-1", "model-1", "model-1:v2", RewardMode.DELAYED_FEEDBACK),
    )
    request = _request()
    await pipeline.before("task-1", "capture-1", "turn-1", request)
    await pipeline.after("task-1", "capture-1", "turn-1", request, _response())
    publishing = asyncio.create_task(
        pipeline.before("task-1", "capture-2", "turn-2", _request(message="feedback")),
    )
    await store.saved.wait()

    await pipeline.abort("task-1", FinishReason.CAPTURE_FAILED)
    store.release.set()

    with pytest.raises(TaskConflictError, match="not active"):
        await publishing
    assert await store.get_sample("capture-1") is None

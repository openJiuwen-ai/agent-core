# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Real callback-stack tests with deterministic provider responses (no billing)."""

import asyncio
import json
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from openjiuwen.core.foundation.llm.call_scope import LlmCallScope
from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig, ModelRequestConfig
from openjiuwen.core.foundation.llm.schema.message import AssistantMessage, UsageMetadata
from openjiuwen.core.foundation.llm.schema.message_chunk import AssistantMessageChunk
from openjiuwen.core.runner.callback.events import LLMCallEvents
from openjiuwen.core.runner.runner import Runner
from openjiuwen.rsi.harness_rsi.single_harness.events_translate import progress_event
from openjiuwen.rsi.schema import RsiUsageTokens
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.extensions.rails.observability_rail import (
    _token_usage,
)
from openjiuwen.rsi.usage import (
    ModelUsageObserver,
    model_usage_stage,
    record_model_usage,
    set_usage_node,
    usage_tokens,
)


class _Client:
    async def invoke(self, **kwargs):
        usage = UsageMetadata(input_tokens=100, output_tokens=30, cache_read_tokens=20, reasoning_tokens=10)
        await Runner.callback_framework.trigger(
            LLMCallEvents.LLM_OUTPUT,
            model_name="provider-model",
            usage=usage,
            response="private response",
            api_key="private credential",
        )
        return AssistantMessage(content="reply", usage_metadata=usage)

    async def stream(self, **kwargs):
        # The chunk callbacks are not billable calls; totals land only once.
        yield AssistantMessageChunk(content="first")
        yield AssistantMessageChunk(content="second")
        usage = UsageMetadata(input_tokens=80, output_tokens=12, cache_read_tokens=0)
        yield AssistantMessageChunk(content="", usage_metadata=usage)
        await Runner.callback_framework.trigger(LLMCallEvents.LLM_OUTPUT, model_name="stream-model", usage=usage)


def _model(monkeypatch):
    monkeypatch.setattr("openjiuwen.core.foundation.llm.model.create_model_client", lambda **kwargs: _Client())
    return Model(SimpleNamespace(), SimpleNamespace(model_name="configured-model"))


@pytest.mark.asyncio
async def test_invoke_stream_once_each_with_public_envelope_and_no_private_content(tmp_path, monkeypatch):
    model = _model(monkeypatch)
    events = []

    async def sink(event):
        events.append(event)

    @model_usage_stage("analyze")
    async def call():
        return await model.invoke("private prompt")

    state = {"task_id": "task-1"}
    async with ModelUsageObserver(sink).observe() as observer:
        await observer.bind(state, tmp_path)
        set_usage_node("epoch-002")
        assert (await call()).content == "reply"
        assert len([chunk async for chunk in model.stream("private prompt")]) == 3
        assert not observer.pending
    assert len(events) == 2
    first = asdict(events[0])
    assert first["family"] == "progress" and first["kind"] == "usage"
    assert first["model_call"]["tokens"] == {"input": 100, "output": 30, "cache_hit": 20}
    assert first["model_call"]["model"] == "provider-model"
    assert first["node_ref"] == "epoch-002" and first["stage_ref"] == "analyze"
    assert first["task_id"] == "task-1" and first["model_call"]["duration_ms"] >= 0
    assert [event.event_id for event in events] == [1, 2]
    projected = progress_event(state, total_iterations=5)
    assert projected.usage.call_count == 2
    assert projected.usage.tokens == RsiUsageTokens(180, 42, 20)
    assert projected.usage.cost_estimate is None
    journal = (tmp_path / "model_calls.jsonl").read_text(encoding="utf-8")
    assert "private" not in journal and "reasoning_tokens" not in journal


@pytest.mark.asyncio
async def test_concurrent_runs_and_stages_do_not_cross_charge(tmp_path, monkeypatch):
    model = _model(monkeypatch)

    async def run(name):
        directory = tmp_path / name
        directory.mkdir()
        state = {"task_id": name}
        events = []

        async def sink(event):
            events.append(event)

        @model_usage_stage(name)
        async def call():
            await asyncio.sleep(0)
            await model.invoke("query")

        async with ModelUsageObserver(sink).observe() as observer:
            await observer.bind(state, directory)
            set_usage_node(name)
            await asyncio.gather(call(), call())
        assert state["usage"]["call_count"] == 2
        assert all(event.task_id == event.stage_ref == event.node_ref == name for event in events)
        return {event.call_id for event in events}

    a, b = await asyncio.gather(run("a"), run("b"))
    assert not a & b


@pytest.mark.asyncio
async def test_failed_attempt_and_retry_both_count_without_guessing_failed_tokens(tmp_path, monkeypatch):
    model = _model(monkeypatch)
    state = {"task_id": "retry"}
    async with ModelUsageObserver(None).observe() as observer:
        await observer.bind(state, tmp_path)
        with LlmCallScope("failure"):
            await Runner.callback_framework.trigger(
                LLMCallEvents.LLM_CALL_ERROR, model_name="provider-model", error=TimeoutError("secret endpoint")
            )
            # A wrapper can report the same failure again.
            await Runner.callback_framework.trigger(
                LLMCallEvents.LLM_CALL_ERROR, model_name="provider-model", error=TimeoutError()
            )
        await model.invoke("retry")
    assert state["usage"]["call_count"] == 2
    assert state["usage"]["tokens"] == {"input": None, "output": None, "cache_hit": None}
    lines = [json.loads(line) for line in (tmp_path / "model_calls.jsonl").read_text().splitlines()]
    assert [line["model_call"]["status"] for line in lines] == ["failed", "succeeded"]
    assert lines[1]["model_call"]["tokens"]["input"] == 100
    assert "secret" not in json.dumps(lines)


@pytest.mark.asyncio
async def test_resume_restores_usage_without_reemitting_or_recounting_external_calls(tmp_path):
    events = []

    async def sink(event):
        events.append(event)

    async def external(call_id):
        await record_model_usage(
            model="external-judge",
            call_id=call_id,
            stage_ref="judge",
            usage={"prompt_tokens": 7, "completion_tokens": 3, "prompt_tokens_details": {"cached_tokens": 0}},
        )

    for iteration in range(2):
        state = {"task_id": "resumable"}
        async with ModelUsageObserver(sink).observe() as observer:
            await observer.bind(state, tmp_path)
            await external("call-1")
            if iteration:
                assert len(events) == 1
                await external("call-2")
    assert len(events) == 2
    assert events[1].event_id == 2
    assert events[1].stage_ref == "judge"
    assert state["usage"]["call_count"] == 2
    assert state["usage"]["tokens"]["input"] == 14


@pytest.mark.asyncio
async def test_subscriptions_removed_on_cancellation_and_other_calls_ignored(tmp_path, monkeypatch):
    model = _model(monkeypatch)
    state = {"task_id": "cancelled"}
    before = sum(len(items) for items in Runner.callback_framework.callbacks.values())
    with pytest.raises(asyncio.CancelledError):
        async with ModelUsageObserver(None).observe() as observer:
            await observer.bind(state, tmp_path)
            with LlmCallScope("cancel"):
                await Runner.callback_framework.trigger(LLMCallEvents.LLM_INVOKE_INPUT, model="model")
            await observer.finish_pending()
            raise asyncio.CancelledError()
    assert sum(len(items) for items in Runner.callback_framework.callbacks.values()) == before
    await model.invoke("outside run")
    assert state["usage"]["call_count"] == 1
    payload = json.loads((tmp_path / "model_calls.jsonl").read_text())
    assert payload["model_call"]["status"] == "incomplete"


@pytest.mark.asyncio
async def test_sink_failure_is_persisted_and_does_not_trigger_model_retry(tmp_path, monkeypatch):
    model = _model(monkeypatch)

    async def sink(event):
        raise RuntimeError("delivery unavailable")

    with pytest.raises(RuntimeError, match="usage delivery failed"):
        async with ModelUsageObserver(sink).observe() as observer:
            await observer.bind({"task_id": "sink-failure"}, tmp_path)
            assert (await model.invoke("query")).content == "reply"
    assert len((tmp_path / "model_calls.jsonl").read_text().splitlines()) == 1


@pytest.mark.asyncio
async def test_sink_model_work_is_not_charged_to_engine_or_deadlocked(tmp_path, monkeypatch):
    model = _model(monkeypatch)

    async def sink(event):
        await model.invoke("service-side work")

    state = {"task_id": "sink-excluded"}
    async with ModelUsageObserver(sink).observe() as observer:
        await observer.bind(state, tmp_path)
        await asyncio.wait_for(model.invoke("engine work"), timeout=2)
    assert state["usage"]["call_count"] == 1


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, RsiUsageTokens(None, None, None)),
        ({"prompt_tokens": 0, "completion_tokens": 0}, RsiUsageTokens(0, 0, None)),
        ({"input_tokens": True, "output_tokens": -2}, RsiUsageTokens(None, None, None)),
        (UsageMetadata(), RsiUsageTokens(None, None, None)),
        (UsageMetadata(input_tokens=2, output_tokens=1, cache_tokens=9), RsiUsageTokens(2, 1, None)),
        ({"input": 2, "output": 1, "cache_hit": 0}, RsiUsageTokens(2, 1, 0)),
    ],
)
def test_unknown_counters_and_cache_writes_are_not_fabricated_as_hits(raw, expected):
    assert usage_tokens(raw) == expected


def test_paper_trace_reads_standard_usage_metadata():
    message = AssistantMessage(
        content="reply",
        usage_metadata=UsageMetadata(
            input_tokens=11,
            output_tokens=5,
            total_tokens=16,
            cache_read_tokens=2,
        ),
    )

    assert _token_usage(SimpleNamespace(response=message)) == {
        "input_tokens": 11,
        "output_tokens": 5,
        "total_tokens": 16,
        "cache_read_tokens": 2,
    }


@pytest.mark.asyncio
async def test_ledger_rejects_other_task_identity_without_binding_state(tmp_path):
    async with ModelUsageObserver(None).observe() as observer:
        await observer.bind({"task_id": "one"}, tmp_path)
        await record_model_usage(model="m", call_id="id", usage=None)
    async with ModelUsageObserver(None).observe() as observer:
        with pytest.raises(ValueError, match="task_id"):
            await observer.bind({"task_id": "two"}, tmp_path)
        assert observer.state is None


@pytest.mark.asyncio
async def test_native_openai_client_parser_and_trailing_usage_chunk(tmp_path):
    from openai.types.chat import ChatCompletion, ChatCompletionChunk

    model = Model(
        ModelClientConfig(client_provider="OpenAI", api_key="test-only", api_base="https://example.invalid/v1"),
        ModelRequestConfig(model="test-model"),
    )
    usage = {
        "prompt_tokens": 9,
        "completion_tokens": 4,
        "total_tokens": 13,
        "prompt_tokens_details": {"cached_tokens": 3},
    }
    response = ChatCompletion.model_validate(
        {
            "id": "invoke-id",
            "object": "chat.completion",
            "created": 1,
            "model": "test-model",
            "usage": usage,
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "ok"}}],
        }
    )

    async def chunks():
        for choices, counters in [
            ([{"index": 0, "delta": {"content": "ok"}, "finish_reason": "stop"}], None),
            ([], usage),
        ]:
            yield ChatCompletionChunk.model_validate(
                {
                    "id": "stream-id",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "test-model",
                    "choices": choices,
                    "usage": counters,
                }
            )

    client = AsyncMock()
    client.chat.completions.create = AsyncMock(side_effect=[response, chunks()])
    state = {"task_id": "native"}
    async with ModelUsageObserver(None).observe() as observer:
        await observer.bind(state, tmp_path)
        with patch.object(model._client, "_create_async_openai_client", return_value=client):
            assert (await model.invoke("test")).content == "ok"
            assert [chunk async for chunk in model.stream("test")]
    assert state["usage"]["call_count"] == 2
    assert state["usage"]["tokens"] == {"input": 18, "output": 8, "cache_hit": 6}

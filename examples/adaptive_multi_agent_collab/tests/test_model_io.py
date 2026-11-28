"""Exercise the real public Model.stream with a local injected SDK client."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from openjiuwen.core.foundation.llm import Model
from openjiuwen.core.foundation.llm.output_parsers.json_output_parser import JsonOutputParser
from openjiuwen.core.foundation.llm.schema.message import UsageMetadata
from openjiuwen.core.foundation.llm.schema.message_chunk import AssistantMessageChunk

from examples.adaptive_multi_agent_collab.providers.model_io import invoke_model


class StreamClient:
    def __init__(self, chunks: list[Any]) -> None:
        self.chunks = chunks
        self.calls: list[dict[str, Any]] = []
        self.exhausted = False
        self.closed = False

    async def stream(self, **kwargs: Any):
        self.calls.append(kwargs)
        try:
            for chunk in self.chunks:
                if isinstance(chunk, BaseException):
                    raise chunk
                yield chunk
            self.exhausted = True
        finally:
            self.closed = True

    async def invoke(self, **kwargs: Any):
        raise AssertionError("real public Model must use the stream boundary")


def public_model(chunks: list[Any], *, provider: str = "OpenAI") -> tuple[Model, StreamClient]:
    # Constructing a provider client would create external configuration. Inject
    # a local client solely in this test; the production helper uses public APIs.
    model = Model.__new__(Model)
    client = StreamClient(chunks)
    setattr(model, "_client", client)
    setattr(model, "model_config", None)
    setattr(model, "model_client_config", SimpleNamespace(client_provider=provider))
    return model, client


@pytest.mark.parametrize("finish_reason", ["length", "max_tokens", "max_output_tokens", "stop"])
def test_complete_public_stream_retains_finish_reason_after_usage_only_tail(finish_reason: str) -> None:
    model, client = public_model(
        [
            AssistantMessageChunk(content='{"answer":"y', reasoning_content="first reasoning"),
            AssistantMessageChunk(content='es","explanation":""}', finish_reason=finish_reason),
            AssistantMessageChunk(content="", usage_metadata=UsageMetadata(input_tokens=29, output_tokens=8)),
        ]
    )

    class Parser(JsonOutputParser):
        calls: list[str] = []

        async def parse(self, text):
            assert client.exhausted and client.closed
            self.calls.append(text)
            return await super().parse(text)

    parser = Parser()
    result = asyncio.run(
        invoke_model(
            model,
            messages=[{"role": "user", "content": "question"}],
            output_parser=parser,
            seed=23,
            max_tokens=256,
            timeout=120,
        )
    )
    assert result.content == '{"answer":"yes","explanation":""}'
    assert result.finish_reason == finish_reason
    assert result.parser_content == {"answer": "yes", "explanation": ""}
    assert len(parser.calls) == 1 and result.reasoning_content == "first reasoning"
    assert result.usage_metadata == {"input_tokens": 29, "output_tokens": 8}
    assert result.sdk_usage_limited is True and len(result.sdk_usage_snapshots) == 1
    call = client.calls[0]
    assert call["output_parser"] is None and call["seed"] == 23 and call["max_tokens"] == 256


def test_partial_usage_snapshots_preserve_known_positive_counts_without_summing() -> None:
    model, _ = public_model(
        [
            AssistantMessageChunk(usage_metadata=UsageMetadata(input_tokens=37, cache_tokens=7)),
            AssistantMessageChunk(content="complete text"),
            AssistantMessageChunk(
                usage_metadata=UsageMetadata(input_tokens=0, output_tokens=9, cache_tokens=0), finish_reason="stop"
            ),
            AssistantMessageChunk(usage_metadata=UsageMetadata()),
        ]
    )
    result = asyncio.run(invoke_model(model, messages=[]))
    assert result.usage_metadata == {"input_tokens": 37, "cache_tokens": 7, "output_tokens": 9}
    assert "total_cost" not in result.usage_metadata and "total_tokens" not in result.usage_metadata
    assert result.sdk_usage_snapshots == (
        {"input_tokens": 37, "cache_tokens": 7},
        {"input_tokens": 0, "output_tokens": 9, "cache_tokens": 0},
        {},
    )


def test_missing_usage_remains_missing() -> None:
    model, _ = public_model([AssistantMessageChunk(content="answer", finish_reason="stop")])
    result = asyncio.run(invoke_model(model, messages=[]))
    assert result.usage_metadata is None and result.finish_reason == "stop"
    assert result.sdk_usage_snapshots == ()


def test_text_blocks_are_assembled_completely_without_silent_filtering() -> None:
    model, _ = public_model(
        [
            AssistantMessageChunk(content=["first ", {"type": "text", "text": "second "}]),
            AssistantMessageChunk(content="third", finish_reason="stop"),
        ]
    )
    assert asyncio.run(invoke_model(model, messages=[])).content == "first second third"
    unsupported, _ = public_model([AssistantMessageChunk(content=[{"type": "image", "url": "unused"}])])
    with pytest.raises(TypeError, match="nontext"):
        asyncio.run(invoke_model(unsupported, messages=[]))


def test_midstream_failure_retains_partial_known_usage_and_never_parses_partial_json() -> None:
    failure = RuntimeError("stream disconnected")
    model, client = public_model(
        [AssistantMessageChunk(content='{"answer":', usage_metadata=UsageMetadata(input_tokens=12)), failure]
    )

    class Parser:
        async def parse(self, text):
            raise AssertionError("incomplete streams must not be parsed")

    with pytest.raises(RuntimeError, match="disconnected") as caught:
        asyncio.run(invoke_model(model, messages=[], output_parser=Parser()))
    partial = getattr(caught.value, "usage")
    assert partial.content == '{"answer":' and partial.usage_metadata == {"input_tokens": 12}
    assert partial.finish_reason is None and partial.sdk_usage_limited
    assert client.closed


def test_lossy_public_anthropic_stream_is_rejected_before_dispatch() -> None:
    model, client = public_model([], provider="Anthropic")
    with pytest.raises(ValueError, match="discard max_tokens"):
        asyncio.run(invoke_model(model, messages=[]))
    assert not client.calls


def test_explicit_nonmodel_fixture_keeps_its_original_invoke_contract() -> None:
    expected = SimpleNamespace(content="fixture", finish_reason="length")

    class Fake:
        async def invoke(self, **kwargs):
            assert kwargs == {"messages": "fixture messages", "output_parser": None, "seed": 8}
            return expected

    assert asyncio.run(invoke_model(Fake(), messages="fixture messages", seed=8)) is expected


def test_missing_public_finish_reason_is_an_explicit_incomplete_response() -> None:
    model, _ = public_model([AssistantMessageChunk(content='{"answer":"yes","explanation":""}')])
    with pytest.raises(RuntimeError, match="completion cannot be verified") as caught:
        asyncio.run(invoke_model(model, messages=[]))
    partial = getattr(caught.value, "usage")
    assert partial.finish_reason is None and partial.usage_metadata is None
    assert partial.content == '{"answer":"yes","explanation":""}'


def test_complete_response_parser_exception_preserves_content_and_usage() -> None:
    model, _ = public_model(
        [
            AssistantMessageChunk(
                content="invalid JSON",
                finish_reason="stop",
                usage_metadata=UsageMetadata(input_tokens=17, output_tokens=3),
            )
        ]
    )

    class RaisingParser:
        async def parse(self, text):
            raise ValueError("malformed JSON")

    result = asyncio.run(invoke_model(model, messages=[], output_parser=RaisingParser()))
    assert result.parser_content is None and result.parser_error == "ValueError: malformed JSON"
    assert result.content == "invalid JSON" and result.finish_reason == "stop"
    assert result.usage_metadata == {"input_tokens": 17, "output_tokens": 3}


@pytest.mark.parametrize(
    "first_content,first_reason,error",
    [
        ('{"answer":"yes","explanation":""}', "length", "truncated generation"),
        ('{"answer":', "stop", "not a JSON object"),
    ],
)
def test_public_stream_runtime_repair_counts_default_sdk_usage_and_preserves_seed_caps(
    first_content: str,
    first_reason: str,
    error: str,
) -> None:
    from examples.adaptive_multi_agent_collab.providers.adapter import OpenJiuwenAgent
    from examples.adaptive_multi_agent_collab.infrastructure.randomness import GenerationContext, LogicalCallIdentity
    from examples.adaptive_multi_agent_collab.workflows.runtime import Runtime
    from examples.adaptive_multi_agent_collab.schemas import QueryExample
    from examples.adaptive_multi_agent_collab.tests.test_resources import controller
    from examples.adaptive_multi_agent_collab.tests.test_runtime import _runtime, _matrix

    class ScriptedClient(StreamClient):
        async def stream(self, **kwargs: Any):
            index = len(self.calls)
            self.calls.append(kwargs)
            content = first_content if index == 0 else '{"answer":"yes","explanation":""}'
            reason = first_reason if index == 0 else "stop"
            yield AssistantMessageChunk(content=content[:5])
            yield AssistantMessageChunk(content=content[5:], finish_reason=reason)
            # Mimic SDK clients explicitly materializing missing counts/costs as zero.
            yield AssistantMessageChunk(usage_metadata=UsageMetadata(input_tokens=0, output_tokens=0, total_cost=0))

    model, _ = public_model([])
    client = ScriptedClient([])
    setattr(model, "_client", client)
    ctl = controller()
    adapter = OpenJiuwenAgent(model, resources=ctl, supports_seed=True, timeout=120)
    base, _ = _runtime(1, {}, {1: _matrix(1)})
    runtime = Runtime(base.config, {0: adapter}, base.routing_policy, base.weighting_model)
    example = QueryExample("sdk-stream", "Is this synthetic statement correct?", ("yes", "no"), "yes")
    context = GenerationContext(41, "promptval", example.id)
    trajectory = asyncio.run(runtime.run(example, generation_context=context))
    assert trajectory.completed and trajectory.initial_answers == ("yes",)
    attempts = trajectory.initial_records[0].attempts
    assert len(attempts) == len(client.calls) == len(ctl.ledger.entries) == 2
    assert any(error in item for item in attempts[0].validation_errors)
    assert trajectory.initial_usage.model_calls == 2
    assert trajectory.initial_usage.input_tokens > 0 and trajectory.initial_usage.output_tokens > 0
    assert trajectory.initial_usage.monetary_cost > 0
    assert trajectory.usage.paid_monetary_cost == pytest.approx(sum(entry["charged"] for entry in ctl.ledger.entries))
    assert trajectory.usage.monetary_cost == pytest.approx(
        sum(entry["usage"]["reference_cost"] for entry in ctl.ledger.entries)
    )
    for index, call in enumerate(client.calls):
        assert call["output_parser"] is None and call["max_tokens"] == 256
        assert call["seed"] == context.seed_for(LogicalCallIdentity(0, 0, "initial", "initial", 0, index))
        assert call["messages"][0]["content"].count("Fixed protocol:") == 1
        methods = ctl.ledger.entries[index]["usage"]["accounting_methods"]
        assert methods == {
            "input_tokens": "local",
            "output_tokens": "local",
            "total_cost": "derived",
            "reference_cost": "reference-tariff",
        }


def test_public_stream_accepts_model_name_setting_independently_of_backend_object() -> None:
    model, client = public_model([AssistantMessageChunk(content="feedback", finish_reason="stop")])
    result = asyncio.run(invoke_model(model, messages=[], model="recorded-textgrad-engine", seed=31))
    assert result.content == "feedback"
    assert client.calls[0]["model"] == "recorded-textgrad-engine" and client.calls[0]["seed"] == 31

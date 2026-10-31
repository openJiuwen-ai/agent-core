# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Output-budget admission and narrow context-overflow recovery contracts."""

from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from openai import AsyncOpenAI, BadRequestError

from openjiuwen.core.foundation.llm import ModelClientConfig, ModelRequestConfig
from openjiuwen.core.foundation.llm.schema.message import AssistantMessage, SystemMessage, UserMessage
from openjiuwen.core.foundation.llm.schema.message_chunk import AssistantMessageChunk
from openjiuwen.core.foundation.llm.schema.config import LLMApiMode
from openjiuwen.core.foundation.tool import ToolInfo
from openjiuwen.core.session.agent import Session
from openjiuwen.harness.moa import MoAConfig
from openjiuwen.harness.moa.proposal import ProposalModel, _output_budget
from openjiuwen.harness.moa.state import MoAState, ProposalBatch, ProposalSource, ProposalStore, active_moa
from tests.unit_tests.harness.moa.helpers import agent
from tests.unit_tests.harness.moa.test_moa_proposal import FixedBudgetContext, primary, request_messages


_MARKER = "proposal-only-marker"


@contextmanager
def active_batch(context=None, text=_MARKER):
    key = (object(), 1)
    state = MoAState(
        proposals=ProposalStore(active=ProposalBatch((ProposalSource(1, text),), key)),
        active_key=key,
        context=context if context is not None else FixedBudgetContext(),
    )
    token = active_moa.set(state)
    try:
        yield state
    finally:
        active_moa.reset(token)


def overflow_error(message="maximum context length exceeded"):
    request = httpx.Request("POST", "https://example.test/v1/chat/completions")
    return BadRequestError(
        message,
        response=httpx.Response(400, request=request),
        body={"error": {"code": "context_length_exceeded", "message": message}},
    )


def chat_response(stream=False):
    if stream:
        chunk = {
            "id": "chatcmpl-budget", "object": "chat.completion.chunk", "created": 0,
            "model": "test-model",
            "choices": [{"index": 0, "delta": {"role": "assistant", "content": "ok"}, "finish_reason": None}],
        }
        stopped = {
            **chunk,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }
        return httpx.Response(
            200,
            text="data: " + json.dumps(chunk) + "\n\ndata: " + json.dumps(stopped) + "\n\ndata: [DONE]\n\n",
            headers={"content-type": "text/event-stream"},
        )
    return httpx.Response(200, json={
        "id": "chatcmpl-budget", "object": "chat.completion", "created": 0,
        "model": "test-model",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 20, "completion_tokens": 1, "total_tokens": 21},
    })


@pytest.mark.parametrize(
    "configured, overrides, expected_mode, expected_tokens",
    [
        (None, {}, "unspecified", None),
        (None, {"max_tokens": None}, "unspecified", None),
        (777, {"max_tokens": None}, "known", 777),
        (777, {"max_tokens": 333}, "known", 333),
        (777, {"extra_body": {"max_tokens": 444}}, "known", 444),
        (None, {"extra_body": {"max_tokens": 444}}, "known", 444),
        (None, {"max_completion_tokens": 444}, "unresolved", None),
        (None, {"extra_body": {"max_completion_tokens": 444}}, "unresolved", None),
        (None, {"max_output_tokens": 444}, "unresolved", None),
        (None, {"extra_body": "malformed"}, "unresolved", None),
    ],
)
def test_output_budget_distinguishes_absence_from_known_and_unresolved_values(
    configured, overrides, expected_mode, expected_tokens,
):
    model = primary()
    model.model_config.max_tokens = configured
    before = deepcopy(overrides)

    budget = _output_budget(model, overrides)

    assert budget.mode == expected_mode
    assert budget.tokens == expected_tokens
    assert overrides == before
    assert model.model_config.max_tokens == configured


@pytest.mark.parametrize("value", [True, False, 0, -1, 1.5, "1024"])
@pytest.mark.parametrize("source", ["configured", "per_call", "body"])
def test_invalid_explicit_output_allowances_are_not_treated_as_unspecified(value, source):
    model = primary()
    model.model_config.max_tokens = value if source == "configured" else None
    overrides = {"max_tokens": value} if source == "per_call" else (
        {"extra_body": {"max_tokens": value}} if source == "body" else {}
    )

    budget = _output_budget(model, overrides)

    assert budget.mode == "unresolved"
    assert budget.tokens is None
    assert budget.reason


def test_output_budget_read_failure_is_unresolved():
    class UnreadableModel:
        @property
        def model_config(self):
            raise RuntimeError("configuration unavailable")

    assert _output_budget(UnreadableModel(), {}).mode == "unresolved"


@pytest.mark.parametrize("provider", ["unknown-provider", "IntelliRouter"])
def test_unrecognized_provider_output_contract_is_not_assumed_unspecified(provider):
    model = primary()
    model.model_client_config.client_provider = provider
    model.model_config.max_tokens = None

    assert _output_budget(model, {}).mode == "unresolved"


@pytest.mark.parametrize("provider", ["DeepSeek", "DashScope", "OpenRouter", "Moonshot", "SiliconFlow"])
@pytest.mark.parametrize("cap", [None, 777])
def test_compatible_provider_alias_preserves_output_budget(provider, cap):
    client_config = ModelClientConfig(
        client_provider=provider, api_key="test", api_base="https://example.test/v1",
    )
    model = SimpleNamespace(
        model_client_config=client_config,
        model_config=ModelRequestConfig(model="test-model", max_tokens=cap),
    )
    before = client_config.model_dump()

    budget = _output_budget(model, {})

    assert budget.mode == ("unspecified" if cap is None else "known")
    assert budget.tokens == cap
    assert client_config.model_dump() == before


@pytest.mark.parametrize("responses", [False, True])
def test_empty_call_body_does_not_confuse_chat_and_responses_cap_precedence(responses):
    model = primary(api_mode=LLMApiMode.Responses if responses else None)
    model.model_config.max_tokens = None
    key = "max_output_tokens" if responses else "max_tokens"
    model.model_config.extra_body = {key: 10}
    budget = _output_budget(model, {"extra_body": {}})

    if responses:
        body = model._client._build_responses_request_body(
            messages=[UserMessage(content="task")], tools=None, temperature=None,
            top_p=None, model=None, stop=None, max_tokens=None,
            send_max_output_tokens=False, extra_body={},
        )
        assert body["max_output_tokens"] == 10
        assert budget.mode != "unspecified"
        if budget.mode == "known":
            assert budget.tokens == 10
    else:
        assert budget.mode == "unspecified"


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("configured, overrides, expected_cap", [
    (None, {}, None),
    (777, {"max_tokens": None}, 777),
    (777, {"max_tokens": 333}, 333),
    (777, {"max_tokens": 333, "extra_body": {"max_tokens": 444}}, 444),
])
async def test_chat_wire_delivers_proposal_without_inventing_or_changing_output_cap(
    stream, configured, overrides, expected_cap,
):
    model = primary()
    model.model_config.max_tokens = configured
    model.model_client_config.max_retries = 0
    model.model_client_config.use_shared_llm_http_client = True
    before = model.model_config.model_dump()
    messages = request_messages()
    history = [message.model_dump() for message in messages]
    sent = []

    async def handler(request):
        body = json.loads(request.content)
        sent.append(body)
        return chat_response(body.get("stream", False))

    async with AsyncOpenAI(
        api_key="test", base_url="https://example.test/v1", max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    ) as sdk:
        with active_batch() as state, patch.object(model._client, "_create_async_openai_client", return_value=sdk):
            proxy = ProposalModel(model)
            if stream:
                assert [chunk async for chunk in proxy.stream(messages, **overrides)]
            else:
                assert (await proxy.invoke(messages, **overrides)).content == "ok"
            attempt = state.proposals.active.attempts[0]

    assert len(sent) == 1
    assert _MARKER in sent[0]["messages"][-1]["content"]
    assert _MARKER not in str(sent[0]["messages"][:-1])
    if expected_cap is None:
        assert not {"max_tokens", "max_output_tokens", "max_completion_tokens"}.intersection(sent[0])
        assert attempt.budget_mode == "unspecified"
    else:
        assert sent[0]["max_tokens"] == expected_cap
        assert attempt.budget_mode == "known"
    assert [message.model_dump() for message in messages] == history
    assert model.model_config.model_dump() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("session_mode", ["explicit", "conversation_id", "none"])
async def test_reference_through_public_agent_reaches_primary_wire_with_unspecified_output(stream, session_mode):
    model = primary()
    model.model_config.max_tokens = None
    model.model_client_config.max_retries = 0
    model.model_client_config.use_shared_llm_http_client = True
    reference = primary()
    reference.invoke = AsyncMock(return_value=AssistantMessage(content=_MARKER))
    deep = agent(model, MoAConfig(
        mode="verifier_routed", reference_models=(reference,), scorer=lambda *_: 0.5,
    ))
    session_id = f"moa-wire-reference-{stream}-{session_mode}"
    session = Session(session_id=session_id, card=deep.card) if session_mode == "explicit" else None
    inputs = {"query": "answer the task", "conversation_id": session_id} if session_mode == "conversation_id" else (
        "answer the task"
    )
    sent = []
    states = []
    contexts = []

    async def handler(request):
        body = json.loads(request.content)
        sent.append(body)
        states.append(active_moa.get())
        contexts.append(states[-1].context)
        return chat_response(body.get("stream", False))

    async with AsyncOpenAI(
        api_key="test", base_url="https://example.test/v1", max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    ) as sdk:
        with patch.object(model._client, "_create_async_openai_client", return_value=sdk):
            if stream:
                assert [chunk async for chunk in deep.stream(inputs, session=session)]
            else:
                assert (await deep.invoke(inputs, session=session))["output"] == "ok"

    reference.invoke.assert_awaited_once()
    assert len(sent) == 1
    assert _MARKER in sent[0]["messages"][-1]["content"]
    assert _MARKER not in str(sent[0]["messages"][:-1])
    assert not {"max_tokens", "max_output_tokens", "max_completion_tokens"}.intersection(sent[0])
    assert model.model_config.max_tokens is None
    assert _MARKER not in str(contexts[0].get_messages())
    assert states[0].owns_proposals is (session_mode == "none")
    assert states[0].closed and states[0].proposals.closed
    assert states[0].proposals.active is None and states[0].proposals.ready is None
    assert active_moa.get() is None
    if session_mode != "none":
        assert deep.react_agent.context_engine.get_context(session_id=session_id) is contexts[0]


@pytest.mark.asyncio
async def test_unspecified_budget_recounts_dynamic_window_tools_and_escaped_wrapper():
    model = primary()
    model.model_config.max_tokens = None
    model.invoke = AsyncMock(return_value=AssistantMessage(content="ok"))
    messages = [SystemMessage(content="system-" * 20), UserMessage(content="task")]
    before = [message.model_dump() for message in messages]
    tools = [ToolInfo(name="echo", description="tool-" * 15, parameters={"type": "object"})]
    context = FixedBudgetContext(limit=900)
    body = "<&>" * 3_000
    config = model.model_config.model_dump()

    with active_batch(context=context, text=body) as state:
        proxy = ProposalModel(model)
        await proxy.invoke(messages, tools=tools)
        context.limit = 1800
        await proxy.invoke(messages, tools=tools)
        attempts = list(state.proposals.active.attempts)

    assert 0 < attempts[0].kept_chars < attempts[1].kept_chars < len(body)
    counter = context.token_counter()
    for call, limit in zip(model.invoke.await_args_list, [900, 1800]):
        sent = call.kwargs
        candidate = sent["messages"]
        assert "max_tokens" not in sent
        assert counter.count_messages(candidate) + counter.count_tools(tools) + 128 <= limit
        assert candidate[:-1] == messages
        assert candidate[-1].metadata["transient"] is True
        assert "&lt;" in candidate[-1].content and "&amp;" in candidate[-1].content
        assert candidate[-1].content.endswith("</reference_proposals>")
    assert [message.model_dump() for message in messages] == before
    assert model.model_config.model_dump() == config


@pytest.mark.asyncio
@pytest.mark.parametrize("overrides", [{"max_completion_tokens": 512}, {"max_tokens": True}])
async def test_unresolved_budget_skips_without_rewriting_primary_request(overrides, caplog):
    model = primary()
    model.model_config.max_tokens = None
    model.invoke = AsyncMock(return_value=AssistantMessage(content="ok"))
    messages = [UserMessage(content="task")]
    expected_reason = _output_budget(model, overrides).reason
    caplog.set_level("INFO", logger="agent")

    with active_batch() as state:
        await ProposalModel(model).invoke(messages, **overrides)
        assert state.proposals.active.attempts[0].injected is False
        assert state.proposals.active.attempts[0].budget_mode == "unresolved"
        assert state.proposals.active.attempts[0].reason == expected_reason
    assert model.invoke.await_args.kwargs == {"messages": messages, **overrides}
    assert expected_reason and expected_reason in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("context, overrides", [
    (FixedBudgetContext(limit=1), {}),
    (FixedBudgetContext(), {"max_completion_tokens": 512}),
])
async def test_overflow_without_actual_proposal_injection_never_falls_back(context, overrides):
    model = primary()
    model.model_config.max_tokens = None
    model.invoke = AsyncMock(side_effect=overflow_error())

    with active_batch(context=context) as state, pytest.raises(BadRequestError):
        await ProposalModel(model).invoke([UserMessage(content="task")], **overrides)
    model.invoke.assert_awaited_once()
    assert state.proposals.active.attempts[0].injected is False
    assert state.proposals.active.disabled_reason is None


@pytest.mark.asyncio
async def test_unspecified_overflow_retries_original_once_and_disables_current_batch():
    model = primary()
    model.model_config.max_tokens = None
    model.invoke = AsyncMock(side_effect=[overflow_error(), AssistantMessage(content="recovered"), AssistantMessage(content="later")])
    messages = [UserMessage(content="task")]
    before = model.model_config.model_dump()
    options = {"temperature": 0.7, "extra_body": {"custom_flag": True}}

    with active_batch() as state:
        proxy = ProposalModel(model)
        assert (await proxy.invoke(messages, **options)).content == "recovered"
        assert state.proposals.active.disabled_reason == "context_overflow"
        fallback = state.proposals.active.attempts[-1]
        assert fallback.fallback is True and fallback.injected is False
        assert fallback.reason == "context_overflow_fallback"
        assert fallback.budget_mode == "unspecified"
        assert (await proxy.invoke(messages, **options)).content == "later"
        assert state.proposals.active.attempts[-1].reason == "batch_disabled"

    calls = model.invoke.await_args_list
    assert len(calls) == 3
    assert _MARKER in str(calls[0].kwargs["messages"])
    assert calls[1].kwargs == calls[2].kwargs == {"messages": messages, **options}
    assert calls[1].kwargs["messages"] is messages
    assert model.model_config.model_dump() == before


@pytest.mark.asyncio
async def test_unspecified_fallback_second_failure_propagates_without_third_attempt():
    model = primary()
    model.model_config.max_tokens = None
    second = overflow_error("original input is also too long")
    model.invoke = AsyncMock(side_effect=[overflow_error(), second])

    with active_batch() as state, pytest.raises(BadRequestError) as caught:
        await ProposalModel(model).invoke([UserMessage(content="task")])
    assert caught.value is second
    assert model.invoke.await_count == 2
    assert state.proposals.active.disabled_reason == "context_overflow"


@pytest.mark.asyncio
async def test_explicit_input_tokens_overflow_text_without_provider_code_allows_fallback():
    model = primary()
    model.model_config.max_tokens = None
    request = httpx.Request("POST", "https://example.test/v1/chat/completions")
    error = BadRequestError(
        "input tokens exceed the context window",
        response=httpx.Response(400, request=request),
        body={"error": {"type": "invalid_request_error", "message": "input tokens exceed the context window"}},
    )
    model.invoke = AsyncMock(side_effect=[error, AssistantMessage(content="recovered")])
    messages = [UserMessage(content="task")]

    with active_batch() as state:
        assert (await ProposalModel(model).invoke(messages)).content == "recovered"
        assert state.proposals.active.disabled_reason == "context_overflow"
    assert model.invoke.await_count == 2
    assert model.invoke.await_args.kwargs["messages"] is messages


@pytest.mark.asyncio
async def test_overflow_disables_only_the_batch_that_was_actually_injected():
    model = primary()
    model.model_config.max_tokens = None
    requests = []
    with active_batch() as state:
        original_batch = state.proposals.active
        replacement = ProposalBatch((ProposalSource(1, "replacement-only-marker"),), state.active_key)

        async def invoke(messages, **kwargs):
            requests.append(messages)
            if len(requests) == 1:
                state.proposals.active = replacement
                raise overflow_error()
            return AssistantMessage(content="ok")

        model.invoke = invoke
        proxy = ProposalModel(model)
        await proxy.invoke([UserMessage(content="task")])
        assert original_batch.disabled_reason == "context_overflow"
        assert original_batch.attempts[-1].fallback is True
        assert state.proposals.active is replacement
        assert replacement.disabled_reason is None and replacement.attempts == []
        await proxy.invoke([UserMessage(content="task")])

    assert len(requests) == 3
    assert _MARKER in str(requests[0])
    assert _MARKER not in str(requests[1])
    assert "replacement-only-marker" in str(requests[2])


@pytest.mark.asyncio
@pytest.mark.parametrize("configured", [None, 777])
@pytest.mark.parametrize("status, message", [
    (401, "context length exceeded"),
    (429, "context window request rate limit"),
    (400, "max_tokens must be <= context length"),
    (400, "invalid max_tokens parameter for context window"),
    (400, "invalid input: temperature exceeds 2; context window is 131072"),
    (500, "context length lookup failed"),
])
async def test_non_input_overflow_errors_never_trigger_proposal_fallback(configured, status, message):
    model = primary()
    model.model_config.max_tokens = configured
    request = httpx.Request("POST", "https://example.test/v1/chat/completions")
    error = httpx.HTTPStatusError(message, request=request, response=httpx.Response(status, request=request))
    model.invoke = AsyncMock(side_effect=error)

    with active_batch() as state, pytest.raises(httpx.HTTPStatusError):
        await ProposalModel(model).invoke([UserMessage(content="task")])
    model.invoke.assert_awaited_once()
    assert state.proposals.active.disabled_reason is None


@pytest.mark.asyncio
async def test_context_worded_timeout_does_not_trigger_fallback():
    model = primary()
    model.model_config.max_tokens = None
    model.invoke = AsyncMock(side_effect=httpx.ReadTimeout("context length request timed out"))

    with active_batch() as state, pytest.raises(httpx.ReadTimeout):
        await ProposalModel(model).invoke([UserMessage(content="task")])
    model.invoke.assert_awaited_once()
    assert state.proposals.active.disabled_reason is None


@pytest.mark.asyncio
async def test_known_output_cap_does_not_add_a_context_overflow_retry():
    model = primary()
    model.invoke = AsyncMock(side_effect=overflow_error())

    with active_batch() as state, pytest.raises(BadRequestError):
        await ProposalModel(model).invoke([UserMessage(content="task")])
    model.invoke.assert_awaited_once()
    assert state.proposals.active.disabled_reason is None


@pytest.mark.asyncio
@pytest.mark.parametrize("first_chunk", [None, AssistantMessageChunk(content="partial"), AssistantMessageChunk(content="")])
async def test_stream_context_overflow_replays_only_before_the_first_chunk(first_chunk):
    model = primary()
    model.model_config.max_tokens = None
    requests = []

    async def stream(messages, **kwargs):
        requests.append({"messages": messages, **kwargs})
        if len(requests) == 1:
            if first_chunk is not None:
                yield first_chunk
            raise overflow_error()
        yield AssistantMessageChunk(content="recovered")

    model.stream = stream
    messages = [UserMessage(content="task")]
    chunks = []
    with active_batch() as state:
        if first_chunk is not None:
            with pytest.raises(BadRequestError):
                async for chunk in ProposalModel(model).stream(messages):
                    chunks.append(chunk)
            assert len(requests) == 1
            assert chunks == [first_chunk]
            assert state.proposals.active.disabled_reason is None
        else:
            chunks = [chunk async for chunk in ProposalModel(model).stream(messages)]
            assert [chunk.content for chunk in chunks] == ["recovered"]
            assert len(requests) == 2
            assert requests[1]["messages"] is messages
            assert state.proposals.active.disabled_reason == "context_overflow"
    assert _MARKER in str(requests[0]["messages"])


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [asyncio.CancelledError(), GeneratorExit()])
async def test_stream_cancellation_and_generator_exit_do_not_retry(error):
    model = primary()
    model.model_config.max_tokens = None
    calls = []

    async def stream(messages, **kwargs):
        calls.append(messages)
        raise error
        yield AssistantMessageChunk(content="unreachable")

    model.stream = stream
    with active_batch() as state, pytest.raises(type(error)):
        _ = [chunk async for chunk in ProposalModel(model).stream([UserMessage(content="task")])]
    assert len(calls) == 1
    assert state.proposals.active.disabled_reason is None


@pytest.mark.asyncio
async def test_known_output_cap_stream_overflow_does_not_retry():
    model = primary()
    calls = []

    async def stream(messages, **kwargs):
        calls.append(messages)
        raise overflow_error()
        yield AssistantMessageChunk(content="unreachable")

    model.stream = stream
    with active_batch() as state, pytest.raises(BadRequestError):
        _ = [chunk async for chunk in ProposalModel(model).stream([UserMessage(content="task")])]
    assert len(calls) == 1
    assert state.proposals.active.disabled_reason is None


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_real_sdk_context_overflow_falls_back_to_original_wire_messages(stream):
    model = primary()
    model.model_config.max_tokens = None
    model.model_client_config.max_retries = 0
    model.model_client_config.use_shared_llm_http_client = True
    sent = []

    async def handler(request):
        body = json.loads(request.content)
        sent.append(body)
        if len(sent) == 1:
            return httpx.Response(400, json={"error": {
                "code": "context_length_exceeded", "type": "invalid_request_error",
                "message": "This model's maximum context length is 1024 tokens. Your messages resulted in 2048 tokens.",
            }})
        return chat_response(body.get("stream", False))

    messages = [SystemMessage(content="host rules"), UserMessage(content="task")]
    before = [message.model_dump() for message in messages]
    async with AsyncOpenAI(
        api_key="test", base_url="https://example.test/v1", max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    ) as sdk:
        with active_batch() as state, patch.object(model._client, "_create_async_openai_client", return_value=sdk):
            proxy = ProposalModel(model)
            if stream:
                assert [chunk async for chunk in proxy.stream(messages)]
            else:
                assert (await proxy.invoke(messages)).content == "ok"
            assert state.proposals.active.disabled_reason == "context_overflow"

    assert len(sent) == 2
    assert _MARKER in str(sent[0]["messages"])
    assert sent[1]["messages"] == [{"role": "system", "content": "host rules"}, {"role": "user", "content": "task"}]
    assert all(not {"max_tokens", "max_output_tokens", "max_completion_tokens"}.intersection(body) for body in sent)
    assert [message.model_dump() for message in messages] == before
    assert model.model_config.max_tokens is None

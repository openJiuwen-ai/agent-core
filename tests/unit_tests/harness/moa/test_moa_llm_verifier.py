# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""LLM verifier exports, scoring, probability decoding, and provider requests."""

import asyncio
import hashlib
import importlib.util
import inspect
import json
import math
import sys
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest
from openai import AsyncOpenAI

import openjiuwen.harness.moa as moa
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig
from openjiuwen.core.foundation.llm.schema.config import LLMApiMode
from openjiuwen.core.foundation.llm.schema.message import AssistantMessage, UserMessage
from openjiuwen.harness.moa import MoAConfig, VerifierConfig
from openjiuwen.harness.moa.state import MoAState
from openjiuwen.harness.moa.verifier import LlmVerifierProgressScorer
from openjiuwen.harness.moa.verifier.llm_verifier.progress import (
    ProgressTracker,
    decode_progress_score,
    extract_progress_scores,
    validate_chat_model,
)
from openjiuwen.harness.moa.verifier.llm_verifier.scorer import score_progress
from openjiuwen.harness.prompts.moa_verifier import build_progress_prompt, format_steps


def test_public_imports_resolve_to_one_class_and_keep_four_exports():
    assert moa.LlmVerifierProgressScorer is LlmVerifierProgressScorer
    assert set(moa.__all__) == {"MoAConfig", "VerifierConfig", "JevConfig", "LlmVerifierProgressScorer"}
    assert len(moa.__all__) == 4
    assert importlib.util.find_spec("openjiuwen.harness.moa.verifier").submodule_search_locations is not None
    assert (
        importlib.util.find_spec("openjiuwen.harness.moa.verifier.llm_verifier").submodule_search_locations is not None
    )
    assert (
        importlib.util.find_spec("openjiuwen.harness.moa.verifier.jev_verifier").submodule_search_locations is not None
    )
    assert importlib.util.find_spec("openjiuwen.harness.moa.scoring") is None
    assert importlib.util.find_spec("openjiuwen.harness.moa.jev") is None
    assert importlib.util.find_spec("openjiuwen.harness.moa.verifier._vendor") is None
    assert importlib.util.find_spec("openjiuwen.harness.moa.verifier.scoring") is None
    assert importlib.util.find_spec("openjiuwen.harness.moa.verifier.progress") is None


@pytest.mark.parametrize("arguments", [{"model": "sdk-model"}, {"client": object()}])
def test_sdk_arguments_have_a_model_migration_error(arguments):
    with pytest.raises(BaseError, match="constructed jiuwen Model") as error:
        LlmVerifierProgressScorer(**arguments)
    assert error.value.status is StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR


@pytest.mark.parametrize("repeats", [0, -1, True, 1.5, "3"])
def test_scorer_and_tracker_reject_nonpositive_or_noninteger_repeats(repeats):
    with pytest.raises(BaseError) as error:
        LlmVerifierProgressScorer(tracker_factory=Mock(), n_evaluations=repeats)
    assert error.value.status is StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR
    scorer = LlmVerifierProgressScorer(tracker_factory=Mock())
    with pytest.raises(BaseError) as error:
        scorer.for_invoke("task", n_evaluations=repeats)
    assert error.value.status is StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR


def test_tracker_factory_failure_preserves_its_cause():
    cause = RuntimeError("factory failed")
    scorer = LlmVerifierProgressScorer(tracker_factory=Mock(side_effect=cause))
    with pytest.raises(BaseError) as error:
        scorer.for_invoke("task")
    assert error.value.status is StatusCode.DEEPAGENT_RUNTIME_ERROR
    assert error.value.cause is cause
    assert error.value.__cause__ is cause


def test_factory_is_called_immediately_and_creates_isolated_trackers():
    trackers = [object(), object()]
    factory = Mock(side_effect=trackers)
    scorer = LlmVerifierProgressScorer(tracker_factory=factory, n_evaluations=3)
    first = scorer.for_invoke("x" * 9_000, n_evaluations=2)
    factory.assert_called_once_with("x" * 8_000, n_evaluations=2)
    second = scorer.for_invoke("second")
    factory.assert_called_with("second", n_evaluations=3)
    assert first._tracker is trackers[0]
    assert second._tracker is trackers[1]
    assert first is not second


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["sync", "async", "sync_awaitable"])
async def test_tracker_update_compatibility_uses_the_correct_thread_and_loop(kind):
    main_thread = threading.get_ident()
    loop = asyncio.get_running_loop()
    observed = []
    submitted = []

    async def async_update(text):
        submitted.append(text)
        observed.append((threading.get_ident(), asyncio.get_running_loop()))
        return 0.75

    def sync_update(text):
        submitted.append(text)
        observed.append((threading.get_ident(), None))
        return 0.75

    def returning_awaitable(text):
        observed.append((threading.get_ident(), None))
        return async_update(text)

    update = {"sync": sync_update, "async": async_update, "sync_awaitable": returning_awaitable}[kind]
    tracker = SimpleNamespace(update=update, result=lambda: SimpleNamespace(per_rep_scores=[[0.75]]))
    invoke_scorer = LlmVerifierProgressScorer(tracker_factory=lambda *_args, **_kwargs: tracker).for_invoke("task")
    assert await invoke_scorer("task", ("step one", "step two"), 3) == 0.75
    assert submitted == ["step one\n\nstep two"]
    if kind == "async":
        assert observed == [(main_thread, loop)]
    else:
        assert observed[0][0] != main_thread and observed[0][1] is None
        if kind == "sync_awaitable":
            assert observed[1] == (main_thread, loop)


@pytest.mark.asyncio
async def test_async_callable_scorer_stays_in_the_current_event_loop():
    loop = asyncio.get_running_loop()
    main_thread = threading.get_ident()

    class Scorer:
        async def __call__(self, *_args):
            assert threading.get_ident() == main_thread
            assert asyncio.get_running_loop() is loop
            return 0.6

    config = moa.MoAConfig(reference_models=(object(),), scorer=Scorer())
    assert await score_progress(config, MoAState(problem="task")) == 0.6


@pytest.mark.asyncio
async def test_builtin_default_repeats_work_without_external_package(monkeypatch):
    monkeypatch.setitem(sys.modules, "llm_verifier", None)
    model = SimpleNamespace(
        model_client_config=ModelClientConfig(
            client_provider="OpenAI", api_key="dummy", api_base="https://example.test"
        ),
        model_config=ModelRequestConfig(model="verifier"),
        invoke=AsyncMock(return_value=AssistantMessage(content="<c1>T</c1>")),
    )
    scorer = LlmVerifierProgressScorer(model=model).for_invoke("task")
    assert await scorer("task", ("step",), 3) == 1.0
    assert model.invoke.await_count == 3
    assert "llm_verifier" not in sys.modules or sys.modules["llm_verifier"] is None


@pytest.mark.asyncio
async def test_routing_repeats_override_builtin_default_and_redact_the_prompt():
    model = SimpleNamespace(
        model_client_config=ModelClientConfig(
            client_provider="OpenAI", api_key="dummy", api_base="https://example.test"
        ),
        model_config=ModelRequestConfig(model="verifier"),
        invoke=AsyncMock(return_value=AssistantMessage(content="<c1>T</c1>")),
    )
    config = moa.MoAConfig(
        reference_models=(object(),),
        scorer=LlmVerifierProgressScorer(model=model),
        verifier=moa.VerifierConfig(n_evaluations=2),
    )
    state = MoAState(problem="check token=secret123", steps=["tested output"])
    assert await score_progress(config, state) == 1.0
    assert model.invoke.await_count == 2
    prompt = model.invoke.await_args.kwargs["messages"][0].content
    assert "secret123" not in prompt and "[redacted]" in prompt


@pytest.mark.asyncio
async def test_scorer_factory_is_invocation_local_receives_redacted_problem_and_repeats():
    created = []

    class Scorer:
        def for_invoke(self, problem, n_evaluations):
            created.append((problem, n_evaluations))
            return AsyncMock(return_value=SimpleNamespace(value=0.5))

    config = MoAConfig(reference_models=(object(),), scorer=Scorer(), verifier=VerifierConfig(n_evaluations=2))
    first = MoAState(problem="Investigate token=secret123", steps=["step"])
    second = MoAState(problem="another task")
    assert await score_progress(config, first) == 0.5
    assert await score_progress(config, first) == 0.5
    assert await score_progress(config, second) == 0.5
    assert created == [("Investigate [redacted]", 2), ("another task", 2)]
    assert first.scorer is not second.scorer
    first.scorer.assert_awaited_with("Investigate [redacted]", ("step",), 2)


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [float("nan"), float("inf"), -0.1, 1.1, "invalid"])
async def test_invalid_scores_do_not_update_the_state_baseline(value):
    config = MoAConfig(reference_models=(object(),), scorer=lambda *_: value)
    state = MoAState(previous_score=0.8)
    assert await score_progress(config, state) is None
    assert state.previous_score == 0.8


@pytest.mark.asyncio
async def test_problem_only_factory_and_async_callable_object_remain_compatible():
    problems = []

    class Scorer:
        def for_invoke(self, problem):
            problems.append(problem)
            return self

        async def __call__(self, problem, steps, n_evaluations):
            assert problem == "task" and steps == ("step",) and n_evaluations == 3
            return 0.7

    config = MoAConfig(reference_models=(object(),), scorer=Scorer())
    state = MoAState(problem="task", steps=["step"])
    assert await score_progress(config, state) == 0.7
    assert await score_progress(config, state) == 0.7
    assert problems == ["task"]


@pytest.mark.asyncio
async def test_scorer_cancellation_propagates():
    async def scorer(*_args):
        raise asyncio.CancelledError

    config = MoAConfig(reference_models=(object(),), scorer=scorer)
    with pytest.raises(asyncio.CancelledError):
        await score_progress(config, MoAState())


@pytest.mark.asyncio
@pytest.mark.parametrize("first_response", [RuntimeError("temporary failure"), AssistantMessage(content="unparseable")])
async def test_real_tracker_keeps_submitted_steps_after_exception_or_invalid_scores(first_response):
    model = SimpleNamespace(
        model_client_config=ModelClientConfig(
            client_provider="OpenAI", api_key="dummy", api_base="https://example.test"
        ),
        model_config=ModelRequestConfig(model="verifier"),
        invoke=AsyncMock(side_effect=[first_response, AssistantMessage(content="<c1>T</c1>")]),
    )
    factory = LlmVerifierProgressScorer(model=model, n_evaluations=1)
    scorer = factory.for_invoke("task", n_evaluations=1)
    with pytest.raises(BaseError) as error:
        await scorer("task", ("step 1",), 1)
    assert error.value.status is StatusCode.DEEPAGENT_RUNTIME_ERROR
    if isinstance(first_response, Exception):
        assert error.value.cause is first_response
        assert error.value.__cause__ is first_response
    assert await scorer("task", ("step 1", "step 2"), 1) == 1.0
    assert scorer._tracker._step_texts == ["step 1", "step 2"]
    prompt = model.invoke.call_args.kwargs["messages"][0].content
    assert "step 1" in prompt and "step 2" in prompt


def model(invoke=None, *, provider="OpenAI", api_mode=None, auth_mode="api_key"):
    return SimpleNamespace(
        model_client_config=ModelClientConfig(
            client_provider=provider,
            api_mode=api_mode,
            auth_mode=auth_mode,
            api_key="test",
            api_base="https://example.test/v1",
            **({"extensions": {"kv_cache": {"mode": "affinity"}}} if provider == "InferenceAffinity" else {}),
        ),
        model_config=ModelRequestConfig(model="verifier"),
        invoke=invoke or AsyncMock(return_value=AssistantMessage(content="<c1>T</c1>")),
    )


@pytest.mark.parametrize("provider", ["OpenAI", "DeepSeek", "OpenRouter", "SiliconFlow", "InferenceAffinity"])
def test_chat_validation_uses_core_provider_aliases(provider):
    candidate = model(provider=provider)
    before = candidate.model_client_config.model_dump()
    validate_chat_model(candidate)
    assert candidate.model_client_config.model_dump() == before


@pytest.mark.parametrize(
    "provider, api_mode, auth_mode",
    [
        ("OpenAI", "responses", "api_key"),
        ("OpenAI", "anthropic_messages", "api_key"),
        ("DeepSeek", "anthropic_messages", "api_key"),
        ("Anthropic", None, "api_key"),
        ("IntelliRouter", None, "api_key"),
        ("OpenAIAccount", None, "api_key"),
        ("OpenAI", None, "openai_account_oauth"),
    ],
)
def test_chat_validation_rejects_non_chat_dispatch(provider, api_mode, auth_mode):
    with pytest.raises(BaseError, match="Chat") as error:
        validate_chat_model(model(provider=provider, api_mode=api_mode, auth_mode=auth_mode))
    assert error.value.status is StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR


def test_chat_validation_accepts_a_transparent_model_wrapper():
    class Wrapper:
        def __init__(self, wrapped):
            self._wrapped = wrapped

        def __getattr__(self, name):
            return getattr(self._wrapped, name)

    validate_chat_model(Wrapper(model()))
    with pytest.raises(BaseError, match="preconstructed") as error:
        validate_chat_model(object())
    assert error.value.status is StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR


def test_tracker_rejects_invalid_repeats_and_empty_result():
    with pytest.raises(BaseError, match="positive") as error:
        ProgressTracker("task", model=model(), n_evaluations=0)
    assert error.value.status is StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR
    with pytest.raises(BaseError, match="no steps") as error:
        ProgressTracker("task", model=model()).result()
    assert error.value.status is StatusCode.DEEPAGENT_RUNTIME_ERROR


@pytest.mark.asyncio
async def test_repeated_model_requests_start_concurrently_and_average_readable_scores():
    started = asyncio.Event()
    release = asyncio.Event()
    requests = []
    outputs = ["<c1>A</c1>", "invalid", "<c1>T</c1>"]

    async def invoke(**kwargs):
        index = len(requests)
        requests.append(kwargs)
        if len(requests) == 3:
            started.set()
        await release.wait()
        return AssistantMessage(content=outputs[index])

    tracker = ProgressTracker("task", model=model(invoke), n_evaluations=3)
    running = asyncio.create_task(tracker.update("action with observed output"))
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        assert not running.done()
        assert all(request["messages"] == requests[0]["messages"] for request in requests)
        assert all(isinstance(request["messages"][0], UserMessage) for request in requests)
        assert all(request["tools"] is None for request in requests)
        assert all(request["logprobs"] and request["top_logprobs"] == 20 for request in requests)
    finally:
        release.set()
    assert await running == 0.5
    result = tracker.result()
    assert result.steps == [1] and result.scores == [0.5]
    assert result.per_rep_scores == [[0.0], [None], [1.0]]
    result.steps.append(99)
    result.per_rep_scores[0].append(99)
    assert tracker.result().steps == [1]
    assert tracker.result().per_rep_scores == [[0.0], [None], [1.0]]


@pytest.mark.asyncio
async def test_all_invalid_scores_keep_step_and_explicit_invalid_history():
    candidate = model(AsyncMock(return_value=AssistantMessage(content="unparseable")))
    tracker = ProgressTracker("task", model=candidate, n_evaluations=2)
    assert await tracker.update("step 1") == 0.5
    assert tracker.result().per_rep_scores == [[None], [None]]
    candidate.invoke = AsyncMock(return_value=AssistantMessage(content="<c1>T</c1>"))
    assert await tracker.update("step 2") == 1.0
    assert tracker._step_texts == ["step 1", "step 2"]
    assert tracker.result().steps == [1, 2]


@pytest.mark.asyncio
async def test_repeat_exception_waits_for_other_requests_and_retains_submitted_step():
    all_started = asyncio.Event()
    failed = asyncio.Event()
    release = asyncio.Event()
    calls = 0
    completed = []

    async def invoke(**_kwargs):
        nonlocal calls
        index = calls
        calls += 1
        if calls == 3:
            all_started.set()
        await all_started.wait()
        if index == 0:
            failed.set()
            raise RuntimeError("request failed")
        await release.wait()
        completed.append(index)
        return AssistantMessage(content="<c1>T</c1>")

    candidate = model(invoke)
    tracker = ProgressTracker("task", model=candidate, n_evaluations=3)
    running = asyncio.create_task(tracker.update("step 1"))
    try:
        await asyncio.wait_for(failed.wait(), timeout=1)
        await asyncio.sleep(0)
        assert not running.done()
    finally:
        release.set()
    with pytest.raises(BaseError, match="scoring request failed") as error:
        await running
    assert error.value.status is StatusCode.DEEPAGENT_RUNTIME_ERROR
    assert isinstance(error.value.cause, RuntimeError)
    assert error.value.__cause__ is error.value.cause
    assert str(error.value.cause) == "request failed"
    assert sorted(completed) == [1, 2]
    assert tracker.steps == [] and tracker._step_texts == ["step 1"]
    candidate.invoke = AsyncMock(return_value=AssistantMessage(content="<c1>T</c1>"))
    assert await tracker.update("step 2") == 1.0
    assert tracker.result().steps == [2]
    assert tracker._step_texts == ["step 1", "step 2"]
    prompt = candidate.invoke.await_args.kwargs["messages"][0].content
    assert prompt.count("step 1") == prompt.count("step 2") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_child", [False, True])
async def test_cancellation_settles_every_request_without_committing_score(cancel_child):
    started = asyncio.Event()
    never = asyncio.Event()
    calls = 0
    settled = []

    async def invoke(**_kwargs):
        nonlocal calls
        index = calls
        calls += 1
        if calls == 2:
            started.set()
        try:
            await started.wait()
            if cancel_child and index == 0:
                raise asyncio.CancelledError
            await never.wait()
        finally:
            settled.append(index)

    tracker = ProgressTracker("task", model=model(invoke), n_evaluations=2)
    running = asyncio.create_task(tracker.update("submitted step"))
    await asyncio.wait_for(started.wait(), timeout=1)
    if not cancel_child:
        running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(running, timeout=1)
    assert sorted(settled) == [0, 1]
    assert tracker._step_texts == ["submitted step"]
    assert tracker.steps == [] and tracker.scores == []


@pytest.mark.asyncio
async def test_tracker_instances_share_model_but_keep_trajectory_and_results_separate():
    candidate = model()
    first = ProgressTracker("first problem", model=candidate)
    second = ProgressTracker("second problem", model=candidate)
    assert await asyncio.gather(first.update("first action"), second.update("second action")) == [1.0, 1.0]
    prompts = [call.kwargs["messages"][0].content for call in candidate.invoke.await_args_list]
    assert any("first action" in prompt and "second action" not in prompt for prompt in prompts)
    assert any("second action" in prompt and "first action" not in prompt for prompt in prompts)
    assert first._step_texts == ["first action"] and second._step_texts == ["second action"]


@pytest.mark.parametrize(
    "text, count, expected",
    [
        ("<c1>A</c1>", 1, [0.0]),
        ("<c1>t</c1>", 1, [1.0]),
        ("<c1>A</c1>\n<c2>T</c2>", 2, [0.0, 1.0]),
        ("b\ns", 2, [1 / 19, 18 / 19]),
        ("T", 2, [None, None]),
        ("not a score", 1, [None]),
    ],
)
def test_tagged_and_bare_letter_fallback_matches_original_scale(text, count, expected):
    assert extract_progress_scores(text, None, None, count) == expected


def test_logprob_expectation_takes_precedence_over_generated_letter():
    tokens = ["<c1>", "T", "</c1>"]
    probabilities = [[], [("A", math.log(0.8)), ("T", math.log(0.2))], []]
    assert extract_progress_scores("<c1>T</c1>", tokens, probabilities, 1)[0] == pytest.approx(0.2)


def test_merged_tag_and_letter_tokens_and_duplicate_scale_values():
    tokens = ["<c1", ">T", "</c1>"]
    probabilities = [[], [(" >T", math.log(0.2)), ("t", math.log(0.3)), ("A", math.log(0.7))], []]
    assert extract_progress_scores("", tokens, probabilities, 1)[0] == pytest.approx(0.3)


def test_logprob_expectation_remains_stable_for_large_negative_values():
    tokens = ["<c1>", "T", "</c1>"]
    probabilities = [[], [("T", -1000), ("A", -1001)], []]
    assert extract_progress_scores("", tokens, probabilities, 1)[0] == pytest.approx(1 / (1 + math.exp(-1)))


@pytest.mark.parametrize("objects", [False, True])
def test_response_normalization_accepts_dict_and_provider_objects(objects):
    def row(**kwargs):
        return SimpleNamespace(**kwargs) if objects else kwargs

    positions = [
        row(token="<c1>", logprob=-0.1, top_logprobs=[]),
        row(
            token="T",
            logprob=math.log(0.2),
            top_logprobs=[
                row(token="A", logprob="invalid"),
                row(token="A", logprob=math.log(0.8)),
                row(token="T", logprob=math.log(0.2)),
            ],
        ),
        row(token="</c1>", logprob=-0.1, top_logprobs=[]),
    ]
    response = row(content="<c1>T</c1>", logprobs=row(content=positions))
    assert decode_progress_score(response) == pytest.approx(0.2)
    assert decode_progress_score(response, text_fallback=False) == pytest.approx(0.2)


def test_missing_alternatives_uses_sampled_token_probability():
    response = AssistantMessage(
        content="<c1>T</c1>",
        logprobs={
            "content": [
                {"token": "<c1>", "logprob": -0.1},
                {"token": "T", "logprob": -0.2, "top_logprobs": None},
                {"token": "</c1>", "logprob": -0.1},
            ]
        },
    )
    assert decode_progress_score(response, text_fallback=False) == 1.0


def test_probability_probe_never_treats_plain_text_as_probability():
    response = AssistantMessage(content="<c1>T</c1>")
    assert decode_progress_score(response) == 1.0
    assert decode_progress_score(response, text_fallback=False) is None


def test_structured_text_content_is_normalized_without_stringifying_other_blocks():
    response = AssistantMessage(
        content=[
            {"type": "text", "text": "<c1>"},
            {"text": None},
            "T",
            {"text": 123},
            {"text": "</c1>"},
            {"image": "x"},
        ]
    )
    assert decode_progress_score(response) == 1.0
    assert decode_progress_score(response, text_fallback=False) is None


@pytest.mark.parametrize("logprobs", [None, {}, {"content": "invalid"}, {"content": [{"token": None}]}])
def test_incomplete_logprobs_are_unavailable_to_probability_probe(logprobs):
    response = AssistantMessage(content="<c1>T</c1>", logprobs=logprobs)
    assert decode_progress_score(response, text_fallback=False) is None


def test_non_numeric_alternatives_do_not_become_probability_scores():
    response = AssistantMessage(
        content="<c1>T</c1>",
        logprobs={
            "content": [
                {"token": "<c1>"},
                {"token": "T", "top_logprobs": [{"token": "T", "logprob": "invalid"}]},
                {"token": "</c1>"},
            ]
        },
    )
    assert decode_progress_score(response, text_fallback=False) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "configured, temperature, max_tokens", [({}, 1.0, 4096), ({"temperature": 0.4, "max_tokens": 2048}, 0.4, 2048)]
)
async def test_chat_wire_preserves_config_and_decodes_returned_probability(configured, temperature, max_tokens):
    model = Model(
        model_client_config=ModelClientConfig(
            client_provider="OpenAI",
            api_key="dummy",
            api_base="https://example.test/v1",
            max_retries=0,
            use_shared_llm_http_client=False,
        ),
        model_config=ModelRequestConfig(model="verifier", **configured),
    )
    before = model.model_config.model_dump()
    sent = []

    async def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-verifier",
                "object": "chat.completion",
                "created": 0,
                "model": "verifier",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "<c1>T</c1>"},
                        "finish_reason": "stop",
                        "logprobs": {
                            "content": [
                                {"token": "<c1>", "logprob": -0.1, "top_logprobs": []},
                                {
                                    "token": "T",
                                    "logprob": math.log(0.75),
                                    "top_logprobs": [
                                        {"token": "A", "logprob": math.log(0.25)},
                                        {"token": "T", "logprob": math.log(0.75)},
                                    ],
                                },
                                {"token": "</c1>", "logprob": -0.1, "top_logprobs": []},
                            ]
                        },
                    }
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15},
            },
        )

    sdk = AsyncOpenAI(
        api_key="dummy",
        base_url="https://example.test/v1",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    with patch.object(model._client, "_create_async_openai_client", return_value=sdk):
        scorer = LlmVerifierProgressScorer(model=model, n_evaluations=1).for_invoke("verify the task")
        assert await scorer("verify the task", ("ran validation",), 1) == pytest.approx(0.75)
    assert len(sent) == 1
    assert sent[0]["logprobs"] is True and sent[0]["top_logprobs"] == 20
    assert sent[0]["temperature"] == temperature and sent[0]["max_tokens"] == max_tokens
    assert "tools" not in sent[0]
    assert sent[0]["messages"][0]["role"] == "user"
    assert "Attached images" not in sent[0]["messages"][0]["content"]
    assert model.model_config.model_dump() == before


@pytest.mark.parametrize(
    "provider, api_mode", [("OpenAI", LLMApiMode.Responses), ("Anthropic", None), ("OpenAIAccount", None)]
)
def test_non_chat_scoring_models_fail_at_construction(provider, api_mode):
    model = Model(
        model_client_config=ModelClientConfig(
            client_provider=provider,
            api_key="dummy",
            api_base="https://example.test/v1",
            api_mode=api_mode,
        ),
        model_config=ModelRequestConfig(model="verifier"),
    )
    with pytest.raises(BaseError, match="Chat") as error:
        LlmVerifierProgressScorer(model=model)
    assert error.value.status is StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,message,fallback",
    [
        (400, "The parameters `logprobs` is not supported.", True),
        (400, "Unsupported parameter: top_logprobs", True),
        (400, "max_tokens is not supported", False),
        (401, "logprobs is not supported", False),
        (429, "logprobs is not supported", False),
    ],
)
async def test_explicit_parameter_rejection_falls_back_to_real_text_scores(status, message, fallback):
    model = Model(
        model_client_config=ModelClientConfig(
            client_provider="OpenAI",
            api_key="dummy",
            api_base="https://example.test/v1",
            max_retries=0,
            use_shared_llm_http_client=True,
        ),
        model_config=ModelRequestConfig(model="verifier"),
    )
    sent = []

    async def handler(request):
        body = json.loads(request.content)
        sent.append(body)
        if body.get("logprobs"):
            return httpx.Response(
                status,
                json={
                    "error": {
                        "code": "invalid_parameter_error",
                        "type": "invalid_request_error",
                        "message": message,
                    }
                },
            )
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-text",
                "object": "chat.completion",
                "created": 0,
                "model": "verifier",
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": "<c1>T</c1>"}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15},
            },
        )

    async with AsyncOpenAI(
        api_key="dummy",
        base_url="https://example.test/v1",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    ) as sdk:
        with patch.object(model._client, "_create_async_openai_client", return_value=sdk):
            score = LlmVerifierProgressScorer(model=model, n_evaluations=1).for_invoke("task")
            if fallback:
                assert await score("task", ("step1",), 1) == 1.0
                assert await score("task", ("step1", "step2"), 1) == 1.0
                assert len(sent) == 3
                assert all("logprobs" not in body and "top_logprobs" not in body for body in sent[1:])
            else:
                with pytest.raises(Exception):
                    await score("task", ("step1",), 1)
                assert len(sent) == 1


@pytest.mark.parametrize(
    "problem, steps, checkpoints, expected",
    [
        (" task ", [" action "], [1], "e608d6af8c36cb2b2ddd075976f4f0ed05f3391a2829dcd39fe43a849edf135f"),
        (
            "Repair parser",
            ["first step", "second step"],
            [1, 2],
            "868c5c2f8c1762c3541b366debbf3e4b4a1426b1f5604b57f5d587b7af01eeae",
        ),
        ("", [], [], "287a52d2ec51a823602a03f882d43d47f33eeeae70f6c038c6854fc1b9e88d88"),
    ],
)
def test_text_only_prompt_matches_pinned_upstream(problem, steps, checkpoints, expected):
    prompt = build_progress_prompt(problem, format_steps(steps), len(steps), checkpoints)
    assert hashlib.sha256(prompt.encode()).hexdigest() == expected
    assert "Attached images" not in prompt


def test_progress_prompt_has_no_image_parameter_or_branch():
    assert "n_images" not in inspect.signature(build_progress_prompt).parameters
    assert "n_images" not in inspect.getsource(build_progress_prompt)

# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Shared capacity rules (F_20 D-09): configuration, available input and trusted counts."""

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.context_engine.token import NativeTokenizerCounter, TiktokenCounter, TokenCounter
from openjiuwen.core.foundation.llm import AssistantMessage, SystemMessage, UserMessage
from openjiuwen.harness.persistent_agent.execution import capacity as CAPACITY
from openjiuwen.harness.persistent_agent.execution.model_rail import PersistentModelCallRail

pytestmark = pytest.mark.level0


def spec(window=1000, reserve=100, input_limit=800, margin=50):
    request = SimpleNamespace(context_window=window, max_tokens=reserve, model_name="m")
    model = SimpleNamespace(model_request_config=request, model_client_config=SimpleNamespace(client_provider="p"))
    return SimpleNamespace(model=model, model_input_limit_tokens=input_limit, capacity_safety_margin_tokens=margin)


def test_no_capacity_fields_ask_for_no_check():
    assert CAPACITY.capacity_limits(spec(input_limit=None, margin=None)) is None


def test_the_available_input_is_the_tighter_of_the_two_limits():
    assert CAPACITY.capacity_limits(spec()).available_input == 800
    assert CAPACITY.capacity_limits(spec(input_limit=1000, reserve=300, margin=200)).available_input == 500


@pytest.mark.parametrize(
    "changes",
    [
        {"window": None},
        {"window": 0},
        {"reserve": None},
        {"input_limit": None},
        {"margin": None},
        {"input_limit": 1001},
        {"reserve": 900, "margin": 100},
        {"window": True},
    ],
)
def test_an_incomplete_or_impossible_configuration_is_refused(changes):
    with pytest.raises(BaseError) as exc_info:
        CAPACITY.capacity_limits(spec(**changes))
    assert exc_info.value.status == StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR


class Counter(TokenCounter):
    """A counter double whose quality flags are set by the test."""

    def __init__(self, source="native_tokenizer", fallback=None, fail=False, estimated=False):
        self.measurement_source = source
        self.measurement_estimated = estimated
        self.measurement_fallback_reason = fallback
        self.fail = fail

    def count(self, text, *, model="", **kwargs):
        if self.fail:
            raise RuntimeError("tokenizer broke")
        return len(text or "")

    def count_messages(self, messages, *, model="", **kwargs):
        return sum(self.count(message.content) for message in messages)

    def count_tools(self, tools, *, model="", **kwargs):
        return sum(self.count(str(tool)) for tool in tools)


MESSAGES = [SystemMessage(content="sys"), UserMessage(content="hello"), AssistantMessage(content="")]


def test_an_exact_count_covers_messages_and_tools_once():
    assert CAPACITY.count_request(Counter(), MESSAGES, ["tool"]) == len("sys") + len("hello") + len("tool")
    assert CAPACITY.count_request(Counter(), MESSAGES, None) == 8


@pytest.mark.parametrize(
    "counter",
    [
        pytest.param(Counter(estimated=True), id="native_but_estimated"),
        pytest.param(Counter(source="string_length_fallback"), id="string_length"),
        pytest.param(Counter(source="tiktoken_fallback"), id="tiktoken_fallback"),
        pytest.param(Counter(source="family_tokenizer_fallback"), id="family"),
        pytest.param(Counter(fallback="model_variant_tokenizer_family"), id="fallback_reason"),
        pytest.param(Counter(source="not_reported"), id="not_reported"),
        pytest.param(Counter(fail=True), id="counter_error"),
    ],
)
def test_an_estimated_or_failed_count_is_unknown(counter):
    assert CAPACITY.count_request(counter, MESSAGES, []) is None


def final_check_context(get_counter):
    binding: Any = SimpleNamespace(
        control=SimpleNamespace(emitter=object(), window_switch_manager=None),
        ports=SimpleNamespace(budget=SimpleNamespace(admit=AsyncMock())),
    )
    ctx = SimpleNamespace(
        context=SimpleNamespace(token_counter=get_counter), inputs=SimpleNamespace(messages=MESSAGES, tools=[])
    )
    return PersistentModelCallRail(binding, spec=spec()), ctx, binding.ports.budget


@pytest.mark.asyncio
async def test_staged_window_rebuild_does_not_admit_a_stale_or_second_model_request():
    rail, ctx, budget = final_check_context(lambda: Counter())
    manager = SimpleNamespace(before_model_request=AsyncMock(return_value=None))
    rail._binding.control.window_switch_manager = manager
    ctx.has_context_rebuild_request = True

    result = await rail.check_final_request(ctx)

    assert result is None
    manager.before_model_request.assert_awaited_once_with(ctx)
    budget.admit.assert_not_awaited()


@pytest.mark.asyncio
async def test_complete_react_iteration_is_forwarded_only_when_persistent_execution_is_active():
    rail, _ctx, _budget = final_check_context(lambda: Counter())
    manager = SimpleNamespace(after_react_iteration=AsyncMock())
    rail._binding.control.window_switch_manager = manager
    callback_context = object()

    await rail.after_react_iteration(callback_context)

    manager.after_react_iteration.assert_awaited_once_with(callback_context)


@pytest.mark.asyncio
async def test_final_checks_read_the_current_context_counter_without_caching_it():
    rail, ctx, _budget = final_check_context(lambda: Counter())
    assert await rail._count(ctx) == 8
    ctx.context.token_counter = lambda: Counter(estimated=True)
    assert await rail._count(ctx) is None, "a rebound context must not keep using the previous exact counter"


@pytest.mark.parametrize("failure", ["absent", "lookup_error", "count_error"])
@pytest.mark.asyncio
async def test_unavailable_or_broken_context_counters_block_before_budget(failure):
    def get_counter():
        if failure == "lookup_error":
            raise OSError("private tokenizer path")
        return Counter(fail=True) if failure == "count_error" else None

    rail, ctx, budget = final_check_context(get_counter)
    result = await rail.check_final_request(ctx)
    assert result["reason"] == "error" and "capacity unknown" in result["detail"]
    assert "private tokenizer path" not in result["detail"]
    budget.admit.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancellation_while_getting_the_context_counter_propagates():
    def get_counter():
        raise asyncio.CancelledError()

    rail, ctx, budget = final_check_context(get_counter)
    with pytest.raises(asyncio.CancelledError):
        await rail.check_final_request(ctx)
    budget.admit.assert_not_awaited()


def test_non_text_content_is_unknown():
    image = UserMessage(content=[{"type": "image_url", "image_url": {"url": "data:image/png;base64,AA"}}])
    blocks = UserMessage(content=[{"type": "text", "text": "hello"}])
    assert CAPACITY.count_request(Counter(), [*MESSAGES, image], []) is None
    assert CAPACITY.count_request(Counter(), [*MESSAGES, blocks], []) is None, "block lists are not counted as sent"


# Real counters: the flags they report and the text they rewrite before counting.

DATA_URL = "data:image/png;base64," + "iVBORw0KGgo" * 6000


@pytest.fixture
def native_counter(tmp_path):
    """A real NativeTokenizerCounter over a one-token-per-character tokenizer."""
    from tokenizers import Regex, Tokenizer, models, pre_tokenizers

    tokenizer = Tokenizer(models.WordLevel(vocab={"[UNK]": 0}, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = pre_tokenizers.Split(Regex("."), "isolated")
    path = tmp_path / "tokenizer.json"
    tokenizer.save(str(path))
    return NativeTokenizerCounter(path, model="char-model")


def test_a_real_native_count_of_plain_text_is_trusted(native_counter):
    assert native_counter.measure_messages(MESSAGES).estimated is False
    assert CAPACITY.count_request(native_counter, MESSAGES, []) == native_counter.count_messages(MESSAGES)


def test_text_a_counter_would_rewrite_is_unknown_even_for_an_exact_counter(native_counter):
    """The counter replaces the data URL with a short placeholder; the model is sent the whole text."""
    message = UserMessage(content=f"截图如下：{DATA_URL}")
    assert native_counter.count_messages([message]) * 100 < native_counter.count(message.content)
    assert CAPACITY.count_request(native_counter, [*MESSAGES, message], []) is None


def test_a_real_tiktoken_count_reports_itself_as_estimated_so_it_is_unknown():
    counter = TiktokenCounter("gpt-4")
    assert counter.measure_messages(MESSAGES).estimated is True
    assert CAPACITY.count_request(counter, MESSAGES, []) is None
    assert CAPACITY.count_request(counter, [UserMessage(content=f"截图如下：{DATA_URL}")], []) is None

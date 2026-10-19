# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""PA-11 trusted request counts, legacy limit parsing and final-request budget admission."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.context_engine.token import NativeTokenizerCounter, TiktokenCounter, TokenCounter
from openjiuwen.core.foundation.llm import AssistantMessage, SystemMessage, UsageMetadata, UserMessage
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


def test_a_count_without_usage_uses_the_context_engine_counter_for_messages_and_tools():
    assert CAPACITY.count_request(Counter(), MESSAGES, ["tool"]) == len("sys") + len("hello") + len("tool")
    assert CAPACITY.count_request(Counter(), MESSAGES, None) == 8


def test_a_model_usage_baseline_counts_only_the_messages_appended_after_it():
    messages = [
        SystemMessage(content="system"),
        AssistantMessage(content="done", usage_metadata=UsageMetadata(total_tokens=100)),
        UserMessage(content="x" * 40),
    ]

    assert CAPACITY.count_request(Counter(fail=True), messages, ["tools-already-in-baseline"]) == 113


def test_an_estimated_context_counter_is_used_instead_of_being_rejected():
    assert CAPACITY.count_request(Counter(estimated=True), MESSAGES, ["tool"]) == 12


def test_a_failed_context_counter_falls_back_to_the_compressor_character_estimate():
    assert CAPACITY.count_request(Counter(fail=True), MESSAGES, ["tool"]) == 4


def final_check_context(get_counter):
    binding: Any = SimpleNamespace(
        control=SimpleNamespace(emitter=object(), window_switch_manager=None),
        ports=SimpleNamespace(budget=SimpleNamespace(admit=AsyncMock())),
    )
    ctx = SimpleNamespace(
        context=SimpleNamespace(token_counter=get_counter), inputs=SimpleNamespace(messages=MESSAGES, tools=[])
    )
    return PersistentModelCallRail(binding), ctx, binding.ports.budget


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


def test_non_text_content_uses_the_context_engine_measurement_strategy():
    image = UserMessage(content=[{"type": "image_url", "image_url": {"url": "data:image/png;base64,AA"}}])
    blocks = UserMessage(content=[{"type": "text", "text": "hello"}])
    assert CAPACITY.count_request(Counter(), [*MESSAGES, image], []) is not None
    assert CAPACITY.count_request(Counter(), [*MESSAGES, blocks], []) is not None


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


def test_text_a_counter_would_rewrite_uses_the_same_sanitized_count_as_context_engine(native_counter):
    """PA-11 follows ContextEngine's existing sanitizer-aware estimate policy."""
    message = UserMessage(content=f"截图如下：{DATA_URL}")
    assert native_counter.count_messages([message]) * 100 < native_counter.count(message.content)
    assert CAPACITY.count_request(native_counter, [*MESSAGES, message], []) is not None


def test_a_tiktoken_fallback_count_is_usable_like_the_context_engine_compressor():
    counter = TiktokenCounter("gpt-4")
    assert counter.measure_messages(MESSAGES).estimated is True
    assert CAPACITY.count_request(counter, MESSAGES, []) is not None
    assert CAPACITY.count_request(counter, [UserMessage(content=f"截图如下：{DATA_URL}")], []) is not None

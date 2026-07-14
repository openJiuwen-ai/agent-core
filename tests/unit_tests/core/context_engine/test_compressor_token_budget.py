from unittest.mock import MagicMock

from openjiuwen.core.context_engine.processor.forked.compressor.dialogue_compressor import (
    DialogueCompressor,
    DialogueCompressorConfig,
)
from openjiuwen.core.context_engine.processor.forked.compressor.support.util import (
    count_messages_tokens,
    resolve_context_max,
    resolve_ratio_token_threshold,
)
from openjiuwen.core.foundation.llm import UserMessage


def test_compressor_delegates_message_counting_to_shared_helper(monkeypatch):
    shared_counter = MagicMock(return_value=37)
    monkeypatch.setattr(
        "openjiuwen.core.context_engine.processor.forked.compressor.base.count_messages_tokens",
        shared_counter,
    )
    context = MagicMock()
    context.token_counter.return_value = MagicMock()
    compressor = DialogueCompressor(DialogueCompressorConfig())
    messages = [UserMessage(content="hello")]

    assert compressor.count_messages_tokens(messages, context) == 37
    shared_counter.assert_called_once_with(messages, context.token_counter(), "DialogueCompressor")


def test_shared_message_counter_uses_token_counter():
    token_counter = MagicMock()
    token_counter.count_messages.return_value = 23
    messages = [UserMessage(content="hello")]

    assert count_messages_tokens(messages, token_counter, "TestProcessor") == 23
    token_counter.count_messages.assert_called_once_with(messages)


def test_shared_message_counter_falls_back_to_character_estimate(caplog):
    token_counter = MagicMock()
    token_counter.count_messages.side_effect = RuntimeError("counter unavailable")

    result = count_messages_tokens(
        [UserMessage(content="x" * 12)],
        token_counter,
        "TestProcessor",
    )

    assert result == 4
    assert "[TestProcessor] token_counter failed" in caplog.text


def test_shared_context_max_resolver_uses_config_model_mapping_and_default():
    configured = MagicMock()
    configured._context_window_tokens = 123
    configured._model_name = "custom-model"
    configured._model_context_window_tokens = {"custom-model": 456}
    assert resolve_context_max(configured) == 123

    mapped = MagicMock()
    mapped._context_window_tokens = None
    mapped._model_name = "custom-model"
    mapped._model_context_window_tokens = {"custom-model": 456}
    assert resolve_context_max(mapped) == 456

    defaulted = MagicMock()
    defaulted._context_window_tokens = None
    defaulted._model_name = None
    defaulted._model_context_window_tokens = None
    assert resolve_context_max(defaulted) == 200000


def test_ratio_token_threshold_rounds_down_and_stays_positive():
    assert resolve_ratio_token_threshold(101, 0.1) == 10
    assert resolve_ratio_token_threshold(1, 0.1) == 1

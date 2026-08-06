from unittest.mock import MagicMock

from openjiuwen.core.context_engine.base import ContextWindow
from openjiuwen.core.context_engine.context.context_utils import ContextUtils
from openjiuwen.core.context_engine.processor.forked.compressor.dialogue_compressor import (
    DialogueCompressor,
    DialogueCompressorConfig,
)
from openjiuwen.core.context_engine.processor.forked.compressor.support.util import (
    count_messages_tokens,
    resolve_context_max,
    resolve_ratio_token_threshold,
)
from openjiuwen.core.foundation.llm import AssistantMessage, UsageMetadata, UserMessage
from openjiuwen.core.foundation.tool import ToolInfo


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
    shared_counter.assert_called_once_with(
        messages,
        context.token_counter(),
        "DialogueCompressor",
        usage_aware=False,
    )


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

    assert result == 3
    assert "[TestProcessor] token_counter failed" in caplog.text


def test_usage_aware_uses_last_assistant_usage_plus_tail_len_estimate():
    token_counter = MagicMock()
    token_counter.count_messages.return_value = 999  # should not be reached

    messages = [
        UserMessage(content="earlier"),
        AssistantMessage(content="ok", usage_metadata=UsageMetadata(total_tokens=5000)),
        UserMessage(content="x" * 40),  # tail: 40 // 4 = 10
    ]

    result = count_messages_tokens(messages, token_counter, "TestProcessor", usage_aware=True)

    assert result == 5010
    token_counter.count_messages.assert_not_called()


def test_usage_aware_without_usage_falls_back_to_token_counter():
    token_counter = MagicMock()
    token_counter.count_messages.return_value = 23

    messages = [UserMessage(content="hello")]  # no AssistantMessage with usage

    result = count_messages_tokens(messages, token_counter, "TestProcessor", usage_aware=True)

    assert result == 23
    token_counter.count_messages.assert_called_once_with(messages)


def test_usage_aware_false_default_ignores_usage():
    token_counter = MagicMock()
    token_counter.count_messages.return_value = 7

    messages = [
        AssistantMessage(content="ok", usage_metadata=UsageMetadata(total_tokens=5000)),
    ]

    result = count_messages_tokens(messages, token_counter, "TestProcessor")

    assert result == 7
    token_counter.count_messages.assert_called_once_with(messages)


def test_usage_aware_ignores_stale_usage_after_context_rewrite():
    token_counter = MagicMock()
    token_counter.count_messages.return_value = 23
    messages = [
        UserMessage(content="old prefix"),
        AssistantMessage(content="ok", usage_metadata=UsageMetadata(total_tokens=5000)),
        UserMessage(content="tail"),
    ]
    ContextUtils.invalidate_usage_metadata(messages)

    result = count_messages_tokens(messages, token_counter, "TestProcessor", usage_aware=True)

    assert result == 23
    token_counter.count_messages.assert_called_once_with(messages)


def test_usage_aware_handles_truthy_non_dict_metadata():
    assistant = AssistantMessage(content="ok", usage_metadata=UsageMetadata(total_tokens=5000))
    object.__setattr__(assistant, "metadata", ["unexpected metadata"])

    assert ContextUtils.has_valid_usage_metadata(assistant) is True


def test_context_window_without_valid_usage_counts_messages_and_tools():
    token_counter = MagicMock()
    token_counter.count_messages.return_value = 20
    token_counter.count_tools.return_value = 7
    context = MagicMock()
    context.token_counter.return_value = token_counter
    compressor = DialogueCompressor(DialogueCompressorConfig())
    window = ContextWindow(
        context_messages=[UserMessage(content="hello")],
        tools=[ToolInfo(name="read_file", description="Read a file", parameters={})],
    )

    result = compressor._count_context_window_tokens(window, context)

    assert result == 27
    token_counter.count_messages.assert_called_once_with(window.context_messages)
    token_counter.count_tools.assert_called_once_with(window.tools)


def test_context_window_with_valid_usage_does_not_double_count_tools():
    token_counter = MagicMock()
    context = MagicMock()
    context.token_counter.return_value = token_counter
    compressor = DialogueCompressor(DialogueCompressorConfig())
    window = ContextWindow(
        context_messages=[
            AssistantMessage(content="ok", usage_metadata=UsageMetadata(total_tokens=5000)),
            UserMessage(content="x" * 40),
        ],
        tools=[ToolInfo(name="read_file", description="Read a file", parameters={})],
    )

    result = compressor._count_context_window_tokens(window, context)

    assert result == 5010
    token_counter.count_messages.assert_not_called()
    token_counter.count_tools.assert_not_called()


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

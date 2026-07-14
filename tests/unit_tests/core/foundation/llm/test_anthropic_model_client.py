# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for AnthropicModelClient pure converters and helpers.

Scope: schema converters, cache-control breakpoint placement, base_url
normalization, and usage extraction. Network/SDK paths (invoke/stream)
are covered separately and require the optional ``anthropic`` SDK.
"""

from unittest.mock import MagicMock

from openjiuwen.core.foundation.llm import (
    ModelClientConfig,
    ModelRequestConfig,
    ProviderType,
)
from openjiuwen.core.foundation.llm.model_clients.anthropic_model_client import (
    AnthropicModelClient,
    _apply_messages_cache_breakpoint,
    _apply_static_cache_breakpoints,
    _content_to_blocks,
    _convert_message_schemas,
    _convert_tool_schemas,
    _last_input_is_transient,
    _mark_cache_control,
)


# ---------------------------------------------------------------------------
# A. Pure converters: _content_to_blocks
# ---------------------------------------------------------------------------


class TestContentToBlocks:
    def test_string_content_wraps_into_text_block(self):
        blocks = _content_to_blocks("hello")
        assert blocks == [{"type": "text", "text": "hello"}]

    def test_empty_string_returns_empty_list(self):
        assert _content_to_blocks("") == []

    def test_none_returns_empty_list(self):
        assert _content_to_blocks(None) == []

    def test_list_of_strings_each_becomes_block(self):
        blocks = _content_to_blocks(["a", "b"])
        assert blocks == [
            {"type": "text", "text": "a"},
            {"type": "text", "text": "b"},
        ]

    def test_list_of_dicts_deep_copied(self):
        src = [{"type": "text", "text": "x"}]
        blocks = _content_to_blocks(src)
        assert blocks == src
        # Verify it's a copy, not the same object
        assert blocks[0] is not src[0]

    def test_list_with_empty_strings_skipped(self):
        blocks = _content_to_blocks(["a", "", "b"])
        assert blocks == [
            {"type": "text", "text": "a"},
            {"type": "text", "text": "b"},
        ]

    def test_non_string_non_list_coerced_to_text(self):
        blocks = _content_to_blocks(42)
        assert blocks == [{"type": "text", "text": "42"}]


# ---------------------------------------------------------------------------
# A. Pure converters: _convert_message_schemas
# ---------------------------------------------------------------------------


class TestConvertMessageSchemas:
    def test_system_extracted_to_top_level(self):
        system_blocks, messages = _convert_message_schemas([
            {"role": "system", "content": "you are helpful"},
            {"role": "user", "content": "hi"},
        ])
        assert system_blocks == [{"type": "text", "text": "you are helpful"}]
        assert len(messages) == 1
        assert messages[0]["role"] == "user"

    def test_no_system_returns_none_blocks(self):
        system_blocks, messages = _convert_message_schemas([
            {"role": "user", "content": "hi"},
        ])
        assert system_blocks is None
        assert len(messages) == 1

    def test_consecutive_tool_results_merged_into_one_user(self):
        _, messages = _convert_message_schemas([
            {"role": "user", "content": "do it"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "t1", "type": "function",
                 "function": {"name": "fn", "arguments": "{}"}},
                {"id": "t2", "type": "function",
                 "function": {"name": "fn2", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "t1", "content": "result1"},
            {"role": "tool", "tool_call_id": "t2", "content": "result2"},
        ])
        # tool messages should merge into a single user message with two
        # tool_result blocks (Anthropic alternation requirement).
        assert len(messages) == 3
        assert messages[0]["role"] == "user"
        assert messages[1]["role"] == "assistant"
        assert messages[2]["role"] == "user"
        tool_results = messages[2]["content"]
        assert len(tool_results) == 2
        assert all(b["type"] == "tool_result" for b in tool_results)
        assert tool_results[0]["tool_use_id"] == "t1"
        assert tool_results[1]["tool_use_id"] == "t2"

    def test_assistant_tool_calls_become_tool_use_blocks(self):
        _, messages = _convert_message_schemas([
            {"role": "user", "content": "go"},
            {"role": "assistant", "content": "thinking", "tool_calls": [
                {"id": "abc", "type": "function",
                 "function": {"name": "search",
                              "arguments": '{"q": "x"}'}},
            ]},
        ])
        assistant = messages[1]
        assert assistant["role"] == "assistant"
        tool_use = [b for b in assistant["content"] if b["type"] == "tool_use"]
        assert len(tool_use) == 1
        assert tool_use[0]["id"] == "abc"
        assert tool_use[0]["name"] == "search"
        # arguments JSON string should be parsed into a dict object
        assert tool_use[0]["input"] == {"q": "x"}

    def test_assistant_with_invalid_json_arguments_falls_back_to_raw(self):
        _, messages = _convert_message_schemas([
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "id1", "type": "function",
                 "function": {"name": "fn", "arguments": "not json"}},
            ]},
        ])
        tool_use = [b for b in messages[0]["content"] if b["type"] == "tool_use"]
        assert tool_use[0]["input"] == {"_raw_arguments": "not json"}

    def test_tool_result_empty_content_padded(self):
        _, messages = _convert_message_schemas([
            {"role": "tool", "tool_call_id": "t1", "content": ""},
        ])
        tool_result = messages[0]["content"][0]
        # Anthropic requires non-empty content for tool_result; should be padded
        assert tool_result["content"] == [{"type": "text", "text": ""}]

    def test_unknown_role_treated_as_user(self):
        _, messages = _convert_message_schemas([
            {"role": "developer", "content": "note"},
        ])
        assert messages[0]["role"] == "user"

    def test_strict_alternation_with_pending_tool_results_flush(self):
        # When a user message lands between tool responses and the next message,
        # the pending tool_results must flush first.
        _, messages = _convert_message_schemas([
            {"role": "tool", "tool_call_id": "t1", "content": "r1"},
            {"role": "user", "content": "another"},
        ])
        assert len(messages) == 2
        assert messages[0]["role"] == "user"
        assert messages[0]["content"][0]["type"] == "tool_result"
        assert messages[1]["role"] == "user"
        assert messages[1]["content"][0]["type"] == "text"


# ---------------------------------------------------------------------------
# A. Pure converters: _convert_tool_schemas
# ---------------------------------------------------------------------------


class TestConvertToolSchemas:
    def test_openai_function_schema_converted(self):
        tools = _convert_tool_schemas([
            {"type": "function", "function": {
                "name": "search",
                "description": "search the web",
                "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
            }},
        ])
        assert tools == [{
            "name": "search",
            "description": "search the web",
            "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}},
        }]

    def test_missing_parameters_defaults_to_empty_object(self):
        tools = _convert_tool_schemas([
            {"type": "function", "function": {"name": "fn", "description": "d"}},
        ])
        assert tools[0]["input_schema"] == {"type": "object", "properties": {}}

    def test_none_input_returns_none(self):
        assert _convert_tool_schemas(None) is None

    def test_empty_list_returns_none(self):
        assert _convert_tool_schemas([]) is None

    def test_non_dict_tool_skipped(self):
        tools = _convert_tool_schemas([
            "not a dict",
            {"type": "function", "function": {"name": "ok"}},
        ])
        assert len(tools) == 1
        assert tools[0]["name"] == "ok"


# ---------------------------------------------------------------------------
# B. Cache-control breakpoint placement
# ---------------------------------------------------------------------------


def _is_5m_ephemeral(marker: dict) -> bool:
    """5m TTL is Anthropic's default; both ``{type: ephemeral}`` and
    ``{type: ephemeral, ttl: '5m'}`` are valid wire forms."""
    if marker.get("type") != "ephemeral":
        return False
    ttl = marker.get("ttl")
    return ttl is None or ttl == "5m"


class TestCacheControlPlacement:
    def test_mark_cache_control_targets_last_block_with_1h_ttl(self):
        blocks = [
            {"type": "text", "text": "a"},
            {"type": "text", "text": "b"},
        ]
        _mark_cache_control(blocks, "1h")
        assert "cache_control" not in blocks[0]
        assert blocks[1]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}

    def test_mark_cache_control_default_5m_accepts_omitted_or_explicit_ttl(self):
        blocks = [{"type": "text", "text": "a"}]
        _mark_cache_control(blocks, "5m")
        assert _is_5m_ephemeral(blocks[0]["cache_control"])

    def test_mark_cache_control_noop_on_empty(self):
        # Should not raise on empty list
        _mark_cache_control([], "1h")

    def test_apply_static_breakpoints_marks_last_tool_and_last_system(self):
        tools = [{"name": "a"}, {"name": "b"}]
        system_blocks = [{"type": "text", "text": "s"}]
        _apply_static_cache_breakpoints(system_blocks, tools)
        assert "cache_control" not in tools[0]
        assert _is_5m_ephemeral(tools[1]["cache_control"])
        assert _is_5m_ephemeral(system_blocks[0]["cache_control"])

    def test_apply_static_breakpoints_handles_no_tools(self):
        system_blocks = [{"type": "text", "text": "s"}]
        _apply_static_cache_breakpoints(system_blocks, None)
        assert _is_5m_ephemeral(system_blocks[0]["cache_control"])

    def test_apply_static_breakpoints_handles_no_system(self):
        tools = [{"name": "only"}]
        _apply_static_cache_breakpoints(None, tools)
        assert _is_5m_ephemeral(tools[0]["cache_control"])

    def test_apply_static_breakpoints_handles_both_none(self):
        # Must not raise
        _apply_static_cache_breakpoints(None, None)


# ---------------------------------------------------------------------------
# B2. Transient-tail detection: _last_input_is_transient
# ---------------------------------------------------------------------------


class TestLastInputIsTransient:
    def test_dict_message_with_transient_metadata(self):
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "user", "content": "budget", "metadata": {"transient": True}},
        ]
        assert _last_input_is_transient(messages) is True

    def test_dict_message_without_transient_metadata(self):
        messages = [{"role": "user", "content": "hi", "metadata": {"transient": False}}]
        assert _last_input_is_transient(messages) is False

    def test_dict_message_missing_metadata(self):
        assert _last_input_is_transient([{"role": "user", "content": "hi"}]) is False

    def test_object_message_with_transient_metadata(self):
        last = MagicMock()
        last.metadata = {"transient": True}
        assert _last_input_is_transient([last]) is True

    def test_object_message_without_metadata_attr(self):
        last = MagicMock(spec=["role", "content"])
        assert _last_input_is_transient([last]) is False

    def test_only_last_message_inspected(self):
        messages = [
            {"role": "user", "content": "x", "metadata": {"transient": True}},
            {"role": "user", "content": "y"},
        ]
        assert _last_input_is_transient(messages) is False

    def test_empty_list_returns_false(self):
        assert _last_input_is_transient([]) is False

    def test_non_list_returns_false(self):
        assert _last_input_is_transient(None) is False
        assert _last_input_is_transient("not a list") is False


# ---------------------------------------------------------------------------
# B3. Conversation breakpoint placement: _apply_messages_cache_breakpoint
# ---------------------------------------------------------------------------


class TestApplyMessagesCacheBreakpoint:
    def _msg(self, text: str) -> dict:
        return {"role": "user", "content": [{"type": "text", "text": text}]}

    def test_anchors_last_message_when_tail_stable(self):
        messages = [self._msg("a"), self._msg("b")]
        _apply_messages_cache_breakpoint(messages, exclude_tail=False)
        assert "cache_control" not in messages[0]["content"][0]
        assert _is_5m_ephemeral(messages[1]["content"][0]["cache_control"])

    def test_anchors_penultimate_when_tail_transient(self):
        messages = [self._msg("a"), self._msg("b"), self._msg("transient")]
        _apply_messages_cache_breakpoint(messages, exclude_tail=True)
        # The transient tail stays uncached; anchor on the stable message before.
        assert _is_5m_ephemeral(messages[1]["content"][0]["cache_control"])
        assert "cache_control" not in messages[2]["content"][0]

    def test_single_message_transient_tail_still_anchors_itself(self):
        # idx >= 1 guard: with only one message there is nothing before it,
        # so it anchors on the lone message rather than going out of range.
        messages = [self._msg("only")]
        _apply_messages_cache_breakpoint(messages, exclude_tail=True)
        assert _is_5m_ephemeral(messages[0]["content"][0]["cache_control"])

    def test_empty_messages_noop(self):
        # Must not raise.
        _apply_messages_cache_breakpoint([], exclude_tail=False)

    def test_non_list_content_skipped(self):
        # String content (not a block list) is left untouched without error.
        messages = [{"role": "user", "content": "plain string"}]
        _apply_messages_cache_breakpoint(messages, exclude_tail=False)
        assert messages[0]["content"] == "plain string"


# ---------------------------------------------------------------------------
# C. _normalize_base_url
# ---------------------------------------------------------------------------


class TestNormalizeBaseUrl:
    def test_strips_trailing_v1(self):
        assert (
            AnthropicModelClient._normalize_base_url("https://openrouter.ai/api/v1")
            == "https://openrouter.ai/api"
        )

    def test_strips_trailing_slash_then_v1(self):
        assert (
            AnthropicModelClient._normalize_base_url("https://openrouter.ai/api/v1/")
            == "https://openrouter.ai/api"
        )

    def test_passthrough_when_no_v1_suffix(self):
        assert (
            AnthropicModelClient._normalize_base_url("https://api.anthropic.com")
            == "https://api.anthropic.com"
        )

    def test_empty_string_returns_none(self):
        assert AnthropicModelClient._normalize_base_url("") is None

    def test_none_returns_none(self):
        assert AnthropicModelClient._normalize_base_url(None) is None


# ---------------------------------------------------------------------------
# D. _usage_from_anthropic
# ---------------------------------------------------------------------------


def _make_client() -> AnthropicModelClient:
    client_config = ModelClientConfig(
        client_provider="Anthropic",
        api_key="mock-key",
        api_base="https://api.anthropic.com",
        verify_ssl=False,
    )
    request_config = ModelRequestConfig(model_name="claude-opus-4")
    return AnthropicModelClient(request_config, client_config)


class TestUsageFromAnthropic:
    def test_input_tokens_sums_uncached_read_and_write(self):
        usage = MagicMock()
        usage.model_dump.return_value = {
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_read_input_tokens": 30,
            "cache_creation_input_tokens": 20,
        }
        client = _make_client()
        meta = client._usage_from_anthropic(usage)
        assert meta is not None
        assert meta.input_tokens == 150  # 100 + 30 + 20
        assert meta.output_tokens == 50
        assert meta.total_tokens == 200
        assert meta.cache_tokens == 30  # cache_read only
        assert meta.model_name == "claude-opus-4"

    def test_zero_cache_fields_handled(self):
        usage = MagicMock()
        usage.model_dump.return_value = {
            "input_tokens": 10,
            "output_tokens": 5,
        }
        meta = _make_client()._usage_from_anthropic(usage)
        assert meta.input_tokens == 10
        assert meta.cache_tokens == 0

    def test_none_usage_returns_none(self):
        assert _make_client()._usage_from_anthropic(None) is None


# ---------------------------------------------------------------------------
# E. Client registration / identity
# ---------------------------------------------------------------------------


class TestClientIdentity:
    def test_client_name_is_anthropic(self):
        assert AnthropicModelClient.__client_name__ == [ProviderType.Anthropic.value]

    def test_get_client_name(self):
        assert _make_client()._get_client_name() == "Anthropic client"

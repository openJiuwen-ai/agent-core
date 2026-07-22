# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.foundation.llm import (
    Model,
    ModelClientConfig,
    ModelRequestConfig,
    ProviderType,
    UserMessage,
)


def _build_mock_response(content: str = "ok") -> MagicMock:
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message = MagicMock()
    response.choices[0].message.content = content
    response.choices[0].message.tool_calls = None
    response.choices[0].message.reasoning = None
    response.choices[0].message.reasoning_content = None
    response.usage = MagicMock()
    response.usage.prompt_tokens = 5
    response.usage.completion_tokens = 3
    response.usage.total_tokens = 8
    response.usage.prompt_tokens_details = None
    return response


def _build_stream_chunk(content: str = "ok") -> MagicMock:
    chunk = MagicMock()
    chunk.choices = [MagicMock()]
    chunk.choices[0].delta = MagicMock()
    chunk.choices[0].delta.content = content
    chunk.choices[0].delta.reasoning = None
    chunk.choices[0].delta.reasoning_content = None
    chunk.choices[0].delta.tool_calls = None
    chunk.choices[0].finish_reason = "stop"
    chunk.usage = None
    return chunk


class TestOpenRouterModelClient:

    def _make_configs(self, custom_headers=None, **client_config_kwargs):
        client_config = ModelClientConfig(
            client_provider="OpenRouter",
            api_key="sk-or-test-key",
            api_base="https://openrouter.ai/api/v1",
            timeout=60.0,
            verify_ssl=False,
            custom_headers=custom_headers,
            **client_config_kwargs,
        )
        request_config = ModelRequestConfig(model="anthropic/claude-sonnet-4")
        return client_config, request_config

    def test_no_default_attribution_headers(self):
        from openjiuwen.core.foundation.llm.model_clients.openrouter_model_client import (
            OpenRouterModelClient,
        )
        client_config, request_config = self._make_configs()
        client = OpenRouterModelClient(request_config, client_config)

        assert "HTTP-Referer" not in client._base_headers
        assert "X-OpenRouter-Title" not in client._base_headers
        assert "X-OpenRouter-Categories" not in client._base_headers

    def test_configurable_headers_from_custom_headers(self):
        from openjiuwen.core.foundation.llm.model_clients.openrouter_model_client import (
            OpenRouterModelClient,
        )
        client_config, request_config = self._make_configs(
            custom_headers={
                "HTTP-Referer": "https://openjiuwen.com/",
                "X-OpenRouter-Title": "JiuwenSwarm",
                "X-OpenRouter-Categories": "cli-agent,cloud-agent",
            }
        )
        client = OpenRouterModelClient(request_config, client_config)

        assert client._base_headers["HTTP-Referer"] == "https://openjiuwen.com/"
        assert client._base_headers["X-OpenRouter-Title"] == "JiuwenSwarm"
        assert client._base_headers["X-OpenRouter-Categories"] == "cli-agent,cloud-agent"

    def test_attribution_headers_protected_from_request_override(self):
        from openjiuwen.core.foundation.llm.model_clients.openrouter_model_client import (
            OpenRouterModelClient,
        )
        client_config, request_config = self._make_configs(
            custom_headers={
                "HTTP-Referer": "https://openjiuwen.com/",
                "X-OpenRouter-Title": "JiuwenSwarm",
            }
        )
        client = OpenRouterModelClient(request_config, client_config)

        effective = client._build_request_headers(
            client._base_headers,
            {
                "HTTP-Referer": "https://evil.com",
                "X-OpenRouter-Title": "EvilApp",
            },
        )
        assert effective["HTTP-Referer"] == "https://openjiuwen.com/"
        assert effective["X-OpenRouter-Title"] == "JiuwenSwarm"

    def test_custom_non_attribution_headers_preserved(self):
        from openjiuwen.core.foundation.llm.model_clients.openrouter_model_client import (
            OpenRouterModelClient,
        )
        client_config, request_config = self._make_configs(
            custom_headers={"X-Custom-Header": "my-value"}
        )
        client = OpenRouterModelClient(request_config, client_config)

        assert client._base_headers.get("X-Custom-Header") == "my-value"

    def test_non_attribution_headers_can_be_overridden(self):
        from openjiuwen.core.foundation.llm.model_clients.openrouter_model_client import (
            OpenRouterModelClient,
        )
        client_config, request_config = self._make_configs(
            custom_headers={"X-Custom-Header": "original"}
        )
        client = OpenRouterModelClient(request_config, client_config)

        effective = client._build_request_headers(
            client._base_headers,
            {"X-Custom-Header": "overridden"},
        )
        assert effective["X-Custom-Header"] == "overridden"

    def test_client_name_is_openrouter_only(self):
        from openjiuwen.core.foundation.llm.model_clients.openrouter_model_client import (
            OpenRouterModelClient,
        )
        assert "OpenRouter" in OpenRouterModelClient.__client_name__
        assert "OpenAI" not in OpenRouterModelClient.__client_name__

    def test_attribution_protection_is_case_insensitive(self):
        from openjiuwen.core.foundation.llm.model_clients.openrouter_model_client import (
            OpenRouterModelClient,
        )
        client_config, request_config = self._make_configs(
            custom_headers={
                "HTTP-Referer": "https://openjiuwen.com/",
                "X-OpenRouter-Title": "JiuwenSwarm",
            }
        )
        client = OpenRouterModelClient(request_config, client_config)

        effective = client._build_request_headers(
            client._base_headers,
            {
                "http-referer": "https://evil.com",
                "x-openrouter-title": "EvilApp",
            },
        )
        assert effective["HTTP-Referer"] == "https://openjiuwen.com/"
        assert effective["X-OpenRouter-Title"] == "JiuwenSwarm"

    def test_prompt_cache_marks_last_tool_first_message_and_last_message_for_anthropic(self):
        from openjiuwen.core.foundation.llm.model_clients.openrouter_model_client import (
            OpenRouterModelClient,
        )
        client_config, request_config = self._make_configs()
        client = OpenRouterModelClient(request_config, client_config)

        params = client._build_request_params(
            messages=[
                {"role": "system", "content": "system prompt"},
                {"role": "user", "content": "message 1"},
                {"role": "assistant", "content": "message 2"},
                {"role": "user", "content": "message 3"},
            ],
            tools=[
                {"type": "function", "function": {"name": "first", "parameters": {}}},
                {"type": "function", "function": {"name": "last", "parameters": {}}},
            ],
            temperature=None,
            top_p=None,
            model=None,
            stop=None,
            max_tokens=None,
            stream=False,
        )

        assert "cache_control" not in params["tools"][0]
        assert params["tools"][1]["cache_control"] == {"type": "ephemeral"}
        assert params["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}
        assert params["messages"][3]["content"][0]["cache_control"] == {"type": "ephemeral"}

    def test_prompt_cache_adds_1h_ttl_for_anthropic_when_enabled(self):
        from openjiuwen.core.foundation.llm.model_clients.openrouter_model_client import (
            OpenRouterModelClient,
        )
        client_config, request_config = self._make_configs(
            openrouter_enable_1h_prompt_cache_ttl=True,
        )
        client = OpenRouterModelClient(
            request_config,
            client_config,
        )

        params = client._build_request_params(
            messages=[{"role": "user", "content": "hello"}],
            tools=[{"type": "function", "function": {"name": "lookup", "parameters": {}}}],
            temperature=None,
            top_p=None,
            model=None,
            stop=None,
            max_tokens=None,
            stream=False,
        )

        expected_cache_control = {"type": "ephemeral", "ttl": "1h"}
        assert params["tools"][0]["cache_control"] == expected_cache_control
        assert params["messages"][0]["content"][0]["cache_control"] == expected_cache_control

    def test_prompt_cache_supports_openrouter_latest_model_tilde_prefix(self):
        from openjiuwen.core.foundation.llm.model_clients.openrouter_model_client import (
            OpenRouterModelClient,
        )
        client_config, request_config = self._make_configs()
        request_config.model_name = "~anthropic/claude-opus-latest"
        client = OpenRouterModelClient(
            request_config,
            client_config,
        )

        params = client._build_request_params(
            messages=[{"role": "user", "content": "hello"}],
            tools=[{"type": "function", "function": {"name": "lookup", "parameters": {}}}],
            temperature=None,
            top_p=None,
            model=None,
            stop=None,
            max_tokens=None,
            stream=False,
        )

        assert params["model"] == "~anthropic/claude-opus-latest"
        assert params["tools"][0]["cache_control"] == {"type": "ephemeral"}
        assert params["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}

    def test_prompt_cache_does_not_add_1h_ttl_for_qwen_when_enabled(self):
        from openjiuwen.core.foundation.llm.model_clients.openrouter_model_client import (
            OpenRouterModelClient,
        )
        client_config, request_config = self._make_configs(
            openrouter_enable_1h_prompt_cache_ttl=True,
        )
        request_config.model_name = "qwen/qwen3-max"
        client = OpenRouterModelClient(
            request_config,
            client_config,
        )

        params = client._build_request_params(
            messages=[{"role": "user", "content": "hello"}],
            tools=[{"type": "function", "function": {"name": "lookup", "parameters": {}}}],
            temperature=None,
            top_p=None,
            model=None,
            stop=None,
            max_tokens=None,
            stream=False,
        )

        assert params["tools"][0]["cache_control"] == {"type": "ephemeral"}
        assert params["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}

    def test_prompt_cache_warns_when_explicit_caching_unsupported(self):
        from openjiuwen.core.foundation.llm.model_clients import openrouter_model_client
        from openjiuwen.core.foundation.llm.model_clients.openrouter_model_client import (
            OpenRouterModelClient,
        )
        client_config, request_config = self._make_configs()
        request_config.model_name = "openai/gpt-4o"
        client = OpenRouterModelClient(request_config, client_config)

        with patch.object(openrouter_model_client.llm_logger, "warning") as warning:
            params = client._build_request_params(
                messages=[{"role": "user", "content": "hello"}],
                tools=None,
                temperature=None,
                top_p=None,
                model=None,
                stop=None,
                max_tokens=None,
                stream=False,
            )

        assert params["messages"][0]["content"] == "hello"
        assert warning.call_count == 1
        assert "explicit prompt caching is enabled but unsupported" in warning.call_args.args[0]
        assert warning.call_args.args[1] == "openai/gpt-4o"

    def test_prompt_cache_warns_when_1h_ttl_unsupported_but_explicit_cache_supported(self):
        from openjiuwen.core.foundation.llm.model_clients import openrouter_model_client
        from openjiuwen.core.foundation.llm.model_clients.openrouter_model_client import (
            OpenRouterModelClient,
        )
        client_config, request_config = self._make_configs(
            openrouter_enable_1h_prompt_cache_ttl=True,
        )
        request_config.model_name = "qwen/qwen3-max"
        client = OpenRouterModelClient(
            request_config,
            client_config,
        )

        with patch.object(openrouter_model_client.llm_logger, "warning") as warning:
            params = client._build_request_params(
                messages=[{"role": "user", "content": "hello"}],
                tools=None,
                temperature=None,
                top_p=None,
                model=None,
                stop=None,
                max_tokens=None,
                stream=False,
            )

        assert params["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}
        assert warning.call_count == 1
        assert "1h prompt-cache TTL is enabled but unsupported" in warning.call_args.args[0]
        assert warning.call_args.args[1] == "qwen/qwen3-max"

    def test_prompt_cache_warns_for_both_unsupported_explicit_cache_and_1h_ttl(self):
        from openjiuwen.core.foundation.llm.model_clients import openrouter_model_client
        from openjiuwen.core.foundation.llm.model_clients.openrouter_model_client import (
            OpenRouterModelClient,
        )
        client_config, request_config = self._make_configs(
            openrouter_enable_1h_prompt_cache_ttl=True,
        )
        request_config.model_name = "openai/gpt-4o"
        client = OpenRouterModelClient(
            request_config,
            client_config,
        )

        with patch.object(openrouter_model_client.llm_logger, "warning") as warning:
            params = client._build_request_params(
                messages=[{"role": "user", "content": "hello"}],
                tools=None,
                temperature=None,
                top_p=None,
                model=None,
                stop=None,
                max_tokens=None,
                stream=False,
            )

        assert params["messages"][0]["content"] == "hello"
        assert warning.call_count == 2
        warning_messages = [call.args[0] for call in warning.call_args_list]
        assert "explicit prompt caching is enabled but unsupported" in warning_messages[0]
        assert "1h prompt-cache TTL is enabled but unsupported" in warning_messages[1]

    def test_prompt_cache_marks_longest_prefix_overlap_by_default(self):
        from openjiuwen.core.foundation.llm.model_clients.openrouter_model_client import (
            OpenRouterModelClient,
        )
        client_config, request_config = self._make_configs()
        client = OpenRouterModelClient(request_config, client_config)

        first_messages = [
            {"role": "system", "content": "stable instructions"},
            {"role": "user", "content": "message 1"},
            {"role": "assistant", "content": "message 2"},
            {"role": "user", "content": "message 3"},
        ]
        second_messages = [
            {"role": "system", "content": "stable instructions"},
            {"role": "user", "content": "message 1"},
            {"role": "assistant", "content": "message 2"},
            {"role": "user", "content": "different message 3"},
        ]

        client._build_request_params(
            messages=first_messages,
            tools=None,
            temperature=None,
            top_p=None,
            model=None,
            stop=None,
            max_tokens=None,
            stream=False,
        )
        params = client._build_request_params(
            messages=second_messages,
            tools=None,
            temperature=None,
            top_p=None,
            model=None,
            stop=None,
            max_tokens=None,
            stream=False,
        )

        assert params["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}
        assert params["messages"][2]["content"][0]["cache_control"] == {"type": "ephemeral"}
        assert params["messages"][3]["content"][0]["cache_control"] == {"type": "ephemeral"}

    def test_prompt_cache_skips_prefix_overlap_when_disabled(self):
        from openjiuwen.core.foundation.llm.model_clients.openrouter_model_client import (
            OpenRouterModelClient,
        )
        client_config, request_config = self._make_configs(
            openrouter_enable_prompt_cache_prefix_matching=False,
        )
        client = OpenRouterModelClient(request_config, client_config)

        first_messages = [
            {"role": "system", "content": "stable instructions"},
            {"role": "user", "content": "message 1"},
            {"role": "assistant", "content": "message 2"},
            {"role": "user", "content": "message 3"},
        ]
        second_messages = [
            {"role": "system", "content": "stable instructions"},
            {"role": "user", "content": "message 1"},
            {"role": "assistant", "content": "message 2"},
            {"role": "user", "content": "different message 3"},
        ]

        client._build_request_params(
            messages=first_messages,
            tools=None,
            temperature=None,
            top_p=None,
            model=None,
            stop=None,
            max_tokens=None,
            stream=False,
        )
        params = client._build_request_params(
            messages=second_messages,
            tools=None,
            temperature=None,
            top_p=None,
            model=None,
            stop=None,
            max_tokens=None,
            stream=False,
        )

        assert client._previous_prompt_cache_messages is None
        assert params["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}
        assert params["messages"][3]["content"][0]["cache_control"] == {"type": "ephemeral"}
        assert params["messages"][2]["content"] == "message 2" # No cache_control

    def test_prompt_cache_prefix_overlap_ignores_marker_text_block_shape(self):
        from openjiuwen.core.foundation.llm.model_clients.openrouter_model_client import (
            _longest_prefix_overlap_index,
        )

        previous_messages = [{
            "role": "system",
            "content": [{
                "type": "text",
                "text": "stable instructions",
                "cache_control": {"type": "ephemeral"},
            }],
        }]
        current_messages = [{"role": "system", "content": "stable instructions"}]

        assert _longest_prefix_overlap_index(previous_messages, current_messages) == 0

    def test_prompt_cache_respects_existing_message_cache_control(self):
        from openjiuwen.core.foundation.llm.model_clients.openrouter_model_client import (
            OpenRouterModelClient,
        )
        client_config, request_config = self._make_configs()
        client = OpenRouterModelClient(request_config, client_config)

        params = client._build_request_params(
            messages=[{
                "role": "user",
                "content": [{
                    "type": "text",
                    "text": "hello",
                    "cache_control": {"type": "ephemeral", "ttl": "1h"},
                }],
            }],
            tools=None,
            temperature=None,
            top_p=None,
            model=None,
            stop=None,
            max_tokens=None,
            stream=False,
        )

        assert params["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}

    def test_prompt_cache_applies_to_qwen_model_prefix(self):
        from openjiuwen.core.foundation.llm.model_clients.openrouter_model_client import (
            OpenRouterModelClient,
        )
        client_config, request_config = self._make_configs()
        request_config.model_name = "qwen/qwen3-max"
        client = OpenRouterModelClient(request_config, client_config)

        params = client._build_request_params(
            messages=[{"role": "user", "content": "hello"}],
            tools=None,
            temperature=None,
            top_p=None,
            model=None,
            stop=None,
            max_tokens=None,
            stream=False,
        )

        assert params["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}

    def test_prompt_cache_supported_providers_can_be_overridden_from_config(self):
        from openjiuwen.core.foundation.llm.model_clients.openrouter_model_client import (
            OpenRouterModelClient,
        )
        client_config, request_config = self._make_configs(
            openrouter_explicit_prompt_cache_providers={"openai"},
        )
        request_config.model_name = "openai/gpt-4o"
        client = OpenRouterModelClient(request_config, client_config)

        params = client._build_request_params(
            messages=[{"role": "user", "content": "hello"}],
            tools=None,
            temperature=None,
            top_p=None,
            model=None,
            stop=None,
            max_tokens=None,
            stream=False,
        )

        assert params["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}

    def test_prompt_cache_1h_ttl_supported_providers_can_be_overridden_from_config(self):
        from openjiuwen.core.foundation.llm.model_clients.openrouter_model_client import (
            OpenRouterModelClient,
        )
        client_config, request_config = self._make_configs(
            openrouter_enable_1h_prompt_cache_ttl=True,
            openrouter_prompt_cache_1h_ttl_providers="anthropic,qwen",
        )
        request_config.model_name = "qwen/qwen3-max"
        client = OpenRouterModelClient(request_config, client_config)

        params = client._build_request_params(
            messages=[{"role": "user", "content": "hello"}],
            tools=None,
            temperature=None,
            top_p=None,
            model=None,
            stop=None,
            max_tokens=None,
            stream=False,
        )

        assert params["messages"][0]["content"][0]["cache_control"] == {
            "type": "ephemeral",
            "ttl": "1h",
        }

    def test_prompt_cache_skips_unsupported_model_prefix(self):
        from openjiuwen.core.foundation.llm.model_clients.openrouter_model_client import (
            OpenRouterModelClient,
        )
        client_config, request_config = self._make_configs()
        request_config.model_name = "openai/gpt-4o"
        client = OpenRouterModelClient(request_config, client_config)

        params = client._build_request_params(
            messages=[{"role": "user", "content": "hello"}],
            tools=None,
            temperature=None,
            top_p=None,
            model=None,
            stop=None,
            max_tokens=None,
            stream=False,
        )

        assert params["messages"][0]["content"] == "hello"

    def test_prompt_cache_skips_when_explicit_caching_disabled(self):
        from openjiuwen.core.foundation.llm.model_clients.openrouter_model_client import (
            OpenRouterModelClient,
        )
        client_config, request_config = self._make_configs(
            openrouter_enable_explicit_prompt_caching=False,
        )
        client = OpenRouterModelClient(request_config, client_config)

        params = client._build_request_params(
            messages=[{"role": "user", "content": "hello"}],
            tools=[{"type": "function", "function": {"name": "lookup", "parameters": {}}}],
            temperature=None,
            top_p=None,
            model=None,
            stop=None,
            max_tokens=None,
            stream=False,
        )

        assert params["messages"][0]["content"] == "hello"
        assert "cache_control" not in params["tools"][0]
        assert client._previous_prompt_cache_messages is None

    def test_prompt_cache_disabled_clears_previous_message_state(self):
        from openjiuwen.core.foundation.llm.model_clients.openrouter_model_client import (
            OpenRouterModelClient,
        )
        enabled_client_config, request_config = self._make_configs(
            openrouter_enable_prompt_cache_prefix_matching=True,
        )
        client = OpenRouterModelClient(request_config, enabled_client_config)

        client._build_request_params(
            messages=[{"role": "system", "content": "stable"}],
            tools=None,
            temperature=None,
            top_p=None,
            model=None,
            stop=None,
            max_tokens=None,
            stream=False,
        )
        assert client._previous_prompt_cache_messages is not None

        disabled_client_config, _ = self._make_configs(
            openrouter_enable_explicit_prompt_caching=False,
            openrouter_enable_prompt_cache_prefix_matching=True,
        )
        client = OpenRouterModelClient(request_config, disabled_client_config)
        client._previous_prompt_cache_messages = [{"role": "system", "content": "stale"}]
        params = client._build_request_params(
            messages=[{"role": "system", "content": "stable"}],
            tools=None,
            temperature=None,
            top_p=None,
            model=None,
            stop=None,
            max_tokens=None,
            stream=False,
        )

        assert params["messages"][0]["content"] == "stable"
        assert client._previous_prompt_cache_messages is None

    def test_prompt_cache_does_not_mutate_caller_messages_or_tools(self):
        from openjiuwen.core.foundation.llm.model_clients.openrouter_model_client import (
            OpenRouterModelClient,
        )
        client_config, request_config = self._make_configs()
        client = OpenRouterModelClient(request_config, client_config)
        messages = [{"role": "user", "content": "hello"}]
        tools = [{"type": "function", "function": {"name": "lookup", "parameters": {}}}]

        client._build_request_params(
            messages=messages,
            tools=tools,
            temperature=None,
            top_p=None,
            model=None,
            stop=None,
            max_tokens=None,
            stream=False,
        )

        assert messages == [{"role": "user", "content": "hello"}]
        assert tools == [{"type": "function", "function": {"name": "lookup", "parameters": {}}}]


class TestOpenRouterFactoryRouting:

    def test_factory_routes_openrouter_to_dedicated_client(self):
        from openjiuwen.core.foundation.llm.model_clients import create_model_client
        from openjiuwen.core.foundation.llm.model_clients.openrouter_model_client import (
            OpenRouterModelClient,
        )
        client_config = ModelClientConfig(
            client_provider="OpenRouter",
            api_key="sk-or-test-key",
            api_base="https://openrouter.ai/api/v1",
            timeout=60.0,
            verify_ssl=False,
        )
        request_config = ModelRequestConfig(model="anthropic/claude-sonnet-4")
        client = create_model_client(client_config, request_config)
        assert isinstance(client, OpenRouterModelClient)

    def test_factory_routes_openai_to_openai_client(self):
        from openjiuwen.core.foundation.llm.model_clients import create_model_client
        from openjiuwen.core.foundation.llm.model_clients.openai_model_client import (
            OpenAIModelClient,
        )
        from openjiuwen.core.foundation.llm.model_clients.openrouter_model_client import (
            OpenRouterModelClient,
        )
        client_config = ModelClientConfig(
            client_provider="OpenAI",
            api_key="sk-test-key",
            api_base="https://api.openai.com/v1",
            timeout=60.0,
            verify_ssl=False,
        )
        request_config = ModelRequestConfig(model="gpt-4o")
        client = create_model_client(client_config, request_config)
        assert isinstance(client, OpenAIModelClient)
        assert not isinstance(client, OpenRouterModelClient)

    def test_openai_client_no_longer_registers_openrouter(self):
        from openjiuwen.core.foundation.llm.model_clients.openai_model_client import (
            OpenAIModelClient,
        )
        names = OpenAIModelClient.__client_name__
        if isinstance(names, list):
            assert "OpenRouter" not in names
        else:
            assert names != "OpenRouter"


class TestOpenRouterModelIntegration:

    async def _invoke_and_get_sent_headers(self, model: Model, request_headers=None) -> dict:
        mock_async_client = AsyncMock()
        mock_async_client.chat.completions.create = AsyncMock(return_value=_build_mock_response())

        invoke_kwargs = {}
        if request_headers is not None:
            invoke_kwargs["custom_headers"] = request_headers

        with patch.object(model._client, "_create_async_openai_client", new=AsyncMock(return_value=mock_async_client)):
            await model.invoke(messages=[UserMessage(content="hello")], **invoke_kwargs)

        sent_params = mock_async_client.chat.completions.create.call_args.kwargs
        return sent_params.get("extra_headers", {})

    @pytest.mark.asyncio
    async def test_openrouter_model_sends_attribution_headers(self):
        model = Model(
            model_client_config=ModelClientConfig(
                client_provider=ProviderType.OpenRouter,
                api_key="sk-or-test",
                api_base="https://openrouter.ai/api/v1",
                verify_ssl=False,
                custom_headers={
                    "HTTP-Referer": "https://openjiuwen.com/",
                    "X-OpenRouter-Title": "JiuwenSwarm",
                    "X-OpenRouter-Categories": "cli-agent,cloud-agent",
                },
            ),
            model_config=ModelRequestConfig(model="anthropic/claude-sonnet-4"),
        )

        sent_headers = await self._invoke_and_get_sent_headers(model)

        assert sent_headers["HTTP-Referer"] == "https://openjiuwen.com/"
        assert sent_headers["X-OpenRouter-Title"] == "JiuwenSwarm"
        assert sent_headers["X-OpenRouter-Categories"] == "cli-agent,cloud-agent"

    @pytest.mark.asyncio
    async def test_openrouter_model_protects_attribution_from_request_headers(self):
        model = Model(
            model_client_config=ModelClientConfig(
                client_provider=ProviderType.OpenRouter,
                api_key="sk-or-test",
                api_base="https://openrouter.ai/api/v1",
                verify_ssl=False,
                custom_headers={
                    "HTTP-Referer": "https://openjiuwen.com/",
                    "X-OpenRouter-Title": "JiuwenSwarm",
                },
            ),
            model_config=ModelRequestConfig(model="anthropic/claude-sonnet-4"),
        )

        sent_headers = await self._invoke_and_get_sent_headers(
            model,
            request_headers={
                "HTTP-Referer": "https://evil.com",
                "X-OpenRouter-Title": "EvilApp",
            },
        )

        assert sent_headers["HTTP-Referer"] == "https://openjiuwen.com/"
        assert sent_headers["X-OpenRouter-Title"] == "JiuwenSwarm"

    @pytest.mark.asyncio
    async def test_openrouter_model_allows_non_attribution_request_headers(self):
        model = Model(
            model_client_config=ModelClientConfig(
                client_provider=ProviderType.OpenRouter,
                api_key="sk-or-test",
                api_base="https://openrouter.ai/api/v1",
                verify_ssl=False,
                custom_headers={
                    "HTTP-Referer": "https://openjiuwen.com/",
                },
            ),
            model_config=ModelRequestConfig(model="anthropic/claude-sonnet-4"),
        )

        sent_headers = await self._invoke_and_get_sent_headers(
            model,
            request_headers={
                "X-Custom-Request": "request-value",
            },
        )

        assert sent_headers["HTTP-Referer"] == "https://openjiuwen.com/"
        assert sent_headers["X-Custom-Request"] == "request-value"

    @pytest.mark.asyncio
    async def test_openrouter_stream_sends_attribution_headers(self):
        model = Model(
            model_client_config=ModelClientConfig(
                client_provider=ProviderType.OpenRouter,
                api_key="sk-or-test",
                api_base="https://openrouter.ai/api/v1",
                verify_ssl=False,
                custom_headers={
                    "HTTP-Referer": "https://openjiuwen.com/",
                    "X-OpenRouter-Title": "JiuwenSwarm",
                },
            ),
            model_config=ModelRequestConfig(model="anthropic/claude-sonnet-4"),
        )

        async def chunk_generator():
            yield _build_stream_chunk("hello")

        mock_async_client = AsyncMock()
        mock_async_client.chat.completions.create = AsyncMock(return_value=chunk_generator())

        with patch.object(model._client, "_create_async_openai_client", new=AsyncMock(return_value=mock_async_client)):
            async for _ in model.stream(messages=[UserMessage(content="hello")]):
                pass

        sent_params = mock_async_client.chat.completions.create.call_args.kwargs
        sent_headers = sent_params.get("extra_headers", {})

        assert sent_headers["HTTP-Referer"] == "https://openjiuwen.com/"
        assert sent_headers["X-OpenRouter-Title"] == "JiuwenSwarm"

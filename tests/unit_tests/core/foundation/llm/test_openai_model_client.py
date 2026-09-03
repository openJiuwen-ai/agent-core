# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.foundation.llm import (
    AssistantMessageChunk,
    ModelClientConfig,
    ModelRequestConfig,
    UsageMetadata,
    UserMessage,
)
from openjiuwen.core.foundation.llm.schema.config import LLMAuthMode, LLMApiMode
from openjiuwen.core.foundation.llm.utils.responses_transport import OpenAIAccountResponsesTransport
from openjiuwen.core.foundation.llm.model_clients.openai_model_client import (
    ModelParamRule,
    OpenAIModelClient,
)


def _make_client() -> OpenAIModelClient:
    client_config = ModelClientConfig(
        client_provider="OpenAI",
        api_key="sk-test-key",
        api_base="https://api.openai.com/v1",
        timeout=60.0,
        verify_ssl=False,
    )
    request_config = ModelRequestConfig(model="MiniMax-M3")
    return OpenAIModelClient(request_config, client_config)


class _UpperParser:
    async def parse(self, content: str) -> str:
        return content.upper()


class _Obj:
    """Small object with explicit attrs; avoids MagicMock's auto-created fields."""

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class _OpenAIStyleError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None, body: dict | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


def _response(content: str = "ok") -> _Obj:
    return _Obj(
        choices=[
            _Obj(
                message=_Obj(content=content),
                finish_reason="stop",
            )
        ],
        usage=None,
    )


def _stream_chunk(content: str, *, finish_reason: str | None = None) -> _Obj:
    return _Obj(
        choices=[
            _Obj(
                delta=_Obj(content=content),
                finish_reason=finish_reason,
            )
        ],
        usage=None,
    )


def test_stream_chunk_reads_tool_calls_from_final_message():
    client = _make_client()
    chunk = _Obj(
        choices=[
            _Obj(
                delta=_Obj(content=""),
                message=_Obj(
                    content="",
                    tool_calls=[
                        _Obj(
                            id="call-1",
                            index=0,
                            function=_Obj(
                                name="task_tool",
                                arguments='{"subagent_type":"explore_agent"}',
                            ),
                        )
                    ],
                ),
                finish_reason="tool_calls",
                token_ids=None,
                logprobs=None,
            )
        ],
        usage=None,
        prompt_token_ids=None,
    )

    parsed = client._parse_stream_chunk(chunk)

    assert parsed is not None
    assert parsed.finish_reason == "tool_calls"
    assert parsed.tool_calls is not None
    assert parsed.tool_calls[0].id == "call-1"
    assert parsed.tool_calls[0].name == "task_tool"


async def _stream_response(*contents: str):
    for content in contents:
        yield _stream_chunk(content)


def _mock_sdk_client(*side_effects) -> AsyncMock:
    client = AsyncMock()
    client.chat.completions.create = AsyncMock(side_effect=list(side_effects))
    return client


def _unsupported_disabled_thinking_error() -> _OpenAIStyleError:
    return _OpenAIStyleError(
        "Error code: 400 - {'error': {'code': '1210', "
        "'message': '该模型始终思考，不支持关闭思考；请使用 low、high 或 max。'}}",
        status_code=400,
        body={
            "error": {
                "code": "1210",
                "message": "该模型始终思考，不支持关闭思考；请使用 low、high 或 max。",
            }
        },
    )


@pytest.mark.asyncio
async def test_stream_parser_preserves_response_facts_through_usage_terminal() -> None:
    client = _make_client()
    parsed_chunks = [
        AssistantMessageChunk(
            content="answer",
            metadata={"source": "provider"},
            response_id="resp-actual",
            response_model="model-actual",
            provider_metadata={"service_tier": "priority"},
        ),
        AssistantMessageChunk(
            content="",
            usage_metadata=UsageMetadata(input_tokens=2, output_tokens=1, total_tokens=3),
            finish_reason="stop",
            response_id="resp-actual",
            response_model="model-actual",
            provider_metadata={"service_tier": "priority"},
        ),
    ]
    client._parse_stream_chunk = MagicMock(side_effect=parsed_chunks)

    class _Parser:
        async def parse(self, content):
            return {"parsed": content}

    async def _raw_stream():
        yield object()
        yield object()

    actual = [
        chunk
        async for chunk in client._astream_with_parser(_raw_stream(), _Parser())
    ]

    assert actual[0].metadata == {"source": "provider"}
    assert actual[0].parser_content == {"parsed": "answer"}
    assert actual[0].response_id == "resp-actual"
    assert actual[0].response_model == "model-actual"
    assert actual[0].provider_metadata == {"service_tier": "priority"}
    assert actual[1].usage_metadata.total_tokens == 3
    assert actual[1].finish_reason == "stop"
    assert actual[1].response_id == "resp-actual"
    assert actual[1].response_model == "model-actual"
    assert actual[1].provider_metadata == {"service_tier": "priority"}


class TestApplyModelSpecificParams:

    def test_minimax_m_injects_reasoning_split(self):
        client = _make_client()
        params: dict = {}
        client._apply_model_specific_params("MiniMax-M3", params)

        assert params["extra_body"] == {"reasoning_split": True}

    def test_non_minimax_model_leaves_params_untouched(self):
        client = _make_client()
        params: dict = {}
        client._apply_model_specific_params("gpt-4o", params)

        assert "extra_body" not in params

    def test_existing_extra_body_preserved_and_merged(self):
        client = _make_client()
        params: dict = {"extra_body": {"return_token_ids": True}}
        client._apply_model_specific_params("MiniMax-M3", params)

        assert params["extra_body"] == {
            "return_token_ids": True,
            "reasoning_split": True,
        }

    def test_none_model_is_noop(self):
        client = _make_client()
        params: dict = {"extra_body": {"existing": 1}}
        client._apply_model_specific_params(None, params)

        assert params == {"extra_body": {"existing": 1}}

    def test_subclass_extending_rules_keeps_parent_rules_intact(self):
        class _Sub(OpenAIModelClient):
            _MODEL_PARAM_RULES = OpenAIModelClient._MODEL_PARAM_RULES + (
                ModelParamRule(
                    name="deepseek_force_thinking",
                    predicate=lambda m: m == "deepseek-reasoner",
                    extra_body_fields={"enable_thinking": True},
                ),
            )

        client = _Sub(ModelRequestConfig(model="deepseek-reasoner"),
                      ModelClientConfig(client_provider="OpenAI",
                                        api_key="sk-test-key",
                                        api_base="https://api.openai.com/v1",
                                        timeout=60.0,
                                        verify_ssl=False))

        params: dict = {}
        client._apply_model_specific_params("deepseek-reasoner", params)

        assert params["extra_body"] == {"enable_thinking": True}

        assert _Sub._MODEL_PARAM_RULES is not OpenAIModelClient._MODEL_PARAM_RULES
        assert OpenAIModelClient._MODEL_PARAM_RULES[0].name == "minimax_reasoning_split"

    def test_subclass_rule_does_not_leak_into_parent(self):
        original_count = len(OpenAIModelClient._MODEL_PARAM_RULES)

        class _Another(OpenAIModelClient):
            _MODEL_PARAM_RULES = OpenAIModelClient._MODEL_PARAM_RULES + (
                ModelParamRule(
                    name="extra_rule",
                    predicate=lambda m: m.startswith("X-"),
                    extra_body_fields={"x": 1},
                ),
            )

        params: dict = {}
        OpenAIModelClient(ModelRequestConfig(model="X-1"),
                          ModelClientConfig(client_provider="OpenAI",
                                            api_key="sk-test-key",
                                            api_base="https://api.openai.com/v1",
                                            timeout=60.0,
                                            verify_ssl=False))._apply_model_specific_params("X-1", params)

        assert "extra_body" not in params, "subclass rule must not leak into parent"
        assert len(OpenAIModelClient._MODEL_PARAM_RULES) == original_count
        assert len(_Another._MODEL_PARAM_RULES) == original_count + 1

    def test_predicate_match(self):
        class _WithPredicate(OpenAIModelClient):
            _MODEL_PARAM_RULES = (
                ModelParamRule(
                    name="custom_predicate",
                    predicate=lambda m: m in {"special-a", "special-b"},
                    extra_body_fields={"flag": True},
                ),
            )

        client = _WithPredicate(ModelRequestConfig(model="special-a"),
                                 ModelClientConfig(client_provider="OpenAI",
                                                   api_key="sk-test-key",
                                                   api_base="https://api.openai.com/v1",
                                                   timeout=60.0,
                                                   verify_ssl=False))

        params_a: dict = {}
        client._apply_model_specific_params("special-a", params_a)
        assert params_a["extra_body"] == {"flag": True}

        params_c: dict = {}
        client._apply_model_specific_params("special-c", params_c)
        assert "extra_body" not in params_c

    def test_multiple_matching_rules_merge_extra_body(self):
        class _Multi(OpenAIModelClient):
            _MODEL_PARAM_RULES = OpenAIModelClient._MODEL_PARAM_RULES + (
                ModelParamRule(
                    name="rule_a",
                    predicate=lambda m: m == "combo-model",
                    extra_body_fields={"field_a": 1},
                ),
                ModelParamRule(
                    name="rule_b",
                    predicate=lambda m: m == "combo-model",
                    extra_body_fields={"field_b": 2},
                ),
            )

        client = _Multi(ModelRequestConfig(model="combo-model"),
                       ModelClientConfig(client_provider="OpenAI",
                                         api_key="sk-test-key",
                                         api_base="https://api.openai.com/v1",
                                         timeout=60.0,
                                         verify_ssl=False))

        params: dict = {"extra_body": {"return_token_ids": True}}
        client._apply_model_specific_params("combo-model", params)

        assert params["extra_body"] == {
            "return_token_ids": True,
            "field_a": 1,
            "field_b": 2,
        }

    def test_later_rule_overrides_earlier_same_field(self):
        class _Override(OpenAIModelClient):
            _MODEL_PARAM_RULES = (
                ModelParamRule(
                    name="first",
                    predicate=lambda m: m == "dup-model",
                    extra_body_fields={"shared": "from-first", "only_first": True},
                ),
                ModelParamRule(
                    name="second",
                    predicate=lambda m: m == "dup-model",
                    extra_body_fields={"shared": "from-second", "only_second": True},
                ),
            )

        client = _Override(ModelRequestConfig(model="dup-model"),
                           ModelClientConfig(client_provider="OpenAI",
                                             api_key="sk-test-key",
                                             api_base="https://api.openai.com/v1",
                                             timeout=60.0,
                                             verify_ssl=False))

        params: dict = {}
        client._apply_model_specific_params("dup-model", params)

        assert params["extra_body"] == {
            "shared": "from-second",
            "only_first": True,
            "only_second": True,
        }

    def test_empty_extra_body_fields_skipped(self):
        class _Empty(OpenAIModelClient):
            _MODEL_PARAM_RULES = (
                ModelParamRule(
                    name="empty_rule",
                    predicate=lambda m: m == "empty-model",
                    extra_body_fields={},
                ),
            )

        client = _Empty(ModelRequestConfig(model="empty-model"),
                        ModelClientConfig(client_provider="OpenAI",
                                          api_key="sk-test-key",
                                          api_base="https://api.openai.com/v1",
                                          timeout=60.0,
                                          verify_ssl=False))

        params: dict = {}
        client._apply_model_specific_params("empty-model", params)

        assert "extra_body" not in params

    def test_subclass_replacing_rules_drops_parent_rules(self):
        class _Replace(OpenAIModelClient):
            _MODEL_PARAM_RULES = (
                ModelParamRule(
                    name="only_rule",
                    predicate=lambda m: m == "solo-model",
                    extra_body_fields={"solo": True},
                ),
            )

        client = _Replace(ModelRequestConfig(model="MiniMax-M3"),
                          ModelClientConfig(client_provider="OpenAI",
                                            api_key="sk-test-key",
                                            api_base="https://api.openai.com/v1",
                                            timeout=60.0,
                                            verify_ssl=False))

        params: dict = {}
        client._apply_model_specific_params("MiniMax-M3", params)

        assert "extra_body" not in params, "parent minimax rule must not apply when subclass replaces the tuple"

        params_solo: dict = {}
        client._apply_model_specific_params("solo-model", params_solo)
        assert params_solo["extra_body"] == {"solo": True}

    def test_default_minimax_predicate_is_case_sensitive(self):
        client = _make_client()
        params: dict = {}
        client._apply_model_specific_params("minimax-m3", params)

        assert "extra_body" not in params


class TestDisabledThinkingIntent:
    @staticmethod
    def _disabled_request_kwargs() -> dict:
        return {
            "extra_body": {
                "routing": "blue",
                "thinking": {"type": "disabled"},
            },
            "enable_thinking": False,
            "chat_template_kwargs": {
                "enable_thinking": False,
                "template": "keep",
            },
            "reasoning": {
                "enabled": False,
                "budget": 32,
            },
            "reasoning_effort": "off",
        }

    @pytest.mark.asyncio
    async def test_supported_disabled_thinking_request_is_sent_once_unchanged(self):
        client = _make_client()
        sdk_client = _mock_sdk_client(_response("ok"))

        with patch.object(client, "_create_async_openai_client", return_value=sdk_client):
            result = await client.invoke("hello", **self._disabled_request_kwargs())

        assert result.content == "ok"
        assert sdk_client.chat.completions.create.call_count == 1
        sent_call = sdk_client.chat.completions.create.call_args.kwargs
        assert sent_call["extra_body"] == {
            "routing": "blue",
            "thinking": {"type": "disabled"},
        }
        assert sent_call["enable_thinking"] is False
        assert sent_call["chat_template_kwargs"]["enable_thinking"] is False
        assert sent_call["reasoning"]["enabled"] is False
        assert sent_call["reasoning_effort"] == "off"

    @pytest.mark.asyncio
    async def test_rejected_disable_is_not_silently_retried_with_model_defaults(self):
        client = _make_client()
        sdk_client = _mock_sdk_client(_unsupported_disabled_thinking_error())

        with patch.object(client, "_create_async_openai_client", return_value=sdk_client):
            with pytest.raises(BaseError):
                await client.invoke("hello", **self._disabled_request_kwargs())

        assert sdk_client.chat.completions.create.call_count == 1

    @pytest.mark.asyncio
    async def test_stream_rejected_disable_is_not_silently_retried(self):
        client = _make_client()
        sdk_client = _mock_sdk_client(_unsupported_disabled_thinking_error())

        with patch.object(client, "_create_async_openai_client", return_value=sdk_client):
            with pytest.raises(BaseError):
                _ = [chunk async for chunk in client.stream("hello", **self._disabled_request_kwargs())]

        assert sdk_client.chat.completions.create.call_count == 1


def test_deepseek_endpoint_profile_adds_reasoning_content_to_assistant_messages():
    client_config = ModelClientConfig(
        client_provider="OpenAI",
        endpoint_profile="deepseek",
        api_key="sk-test-key",
        api_base="https://api.deepseek.com/v1",
        verify_ssl=False,
    )
    client = OpenAIModelClient(ModelRequestConfig(model="deepseek-chat"), client_config)

    params = client._build_request_params(
        messages=[
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ],
        tools=None,
        temperature=None,
        top_p=None,
        model=None,
        stop=None,
        max_tokens=None,
        stream=False,
    )

    assert params["messages"][1]["reasoning_content"] == ""


def test_openai_none_auth_uses_placeholder_sdk_key():
    client_config = ModelClientConfig(
        client_provider="OpenAI",
        endpoint_profile="ollama",
        api_base="http://localhost:11434/v1",
        auth_mode=LLMAuthMode.NoneAuth,
        verify_ssl=False,
    )
    client = OpenAIModelClient(ModelRequestConfig(model="qwen2.5:7b"), client_config)

    assert client._resolved_api_key() == "EMPTY"


def test_dashscope_profile_converts_text_and_reference_images_for_generation():
    client_config = ModelClientConfig(
        client_provider="OpenAI",
        endpoint_profile="dashscope",
        api_key="sk-test-key",
        api_base="https://dashscope.aliyuncs.com",
        verify_ssl=False,
    )
    client = OpenAIModelClient(ModelRequestConfig(model="wan2.6-image"), client_config)

    content = client._dashscope_image_content([
        UserMessage(content=[
            {"text": "turn this into watercolor"},
            {"image": "https://example.test/source.png"},
            {"image_url": {"url": "https://example.test/ref.png"}},
        ])
    ])

    assert content == [
        {"text": "turn this into watercolor"},
        {"image": "https://example.test/source.png"},
        {"image": "https://example.test/ref.png"},
    ]


@pytest.mark.parametrize(
    "content",
    [
        [{"text": "prompt", "extra": "ignored"}],
        [{"text": "prompt"}, {"image": "https://example.test/a.png", "extra": "ignored"}],
        [{"text": "prompt"}, {"image_url": {"url": ""}}],
        [{"text": "prompt"}, {"type": "image", "image": "https://example.test/a.png"}],
    ],
)
def test_dashscope_profile_rejects_invalid_image_generation_content(content):
    with pytest.raises(BaseError):
        OpenAIModelClient._dashscope_image_content([UserMessage(content=content)])


@pytest.mark.parametrize(
    ("voice", "language_type"),
    [
        ("UnknownVoice", "Auto"),
        ("Cherry", "UnknownLanguage"),
    ],
)
def test_dashscope_profile_rejects_invalid_speech_params(voice, language_type):
    with pytest.raises(BaseError):
        OpenAIModelClient._validate_dashscope_speech_params(
            voice=voice,
            language_type=language_type,
        )


@pytest.mark.parametrize(
    ("img_url", "size", "resolution"),
    [
        ("https://example.test/a.png", "1280*720", None),
        (None, None, "720P"),
    ],
)
def test_dashscope_profile_rejects_mismatched_video_size_params(img_url, size, resolution):
    with pytest.raises(BaseError):
        OpenAIModelClient._validate_dashscope_video_params(
            img_url=img_url,
            size=size,
            resolution=resolution,
        )


def test_openrouter_profile_adds_prompt_cache_markers_on_openai_client():
    client_config = ModelClientConfig(
        client_provider="OpenAI",
        endpoint_profile="openrouter",
        api_key="sk-test-key",
        api_base="https://openrouter.ai/api/v1",
        verify_ssl=False,
    )
    client = OpenAIModelClient(ModelRequestConfig(model="anthropic/claude-sonnet-4"), client_config)

    params = client._build_request_params(
        messages=[{"role": "user", "content": "hello"}],
        tools=[{"type": "function", "function": {"name": "search", "parameters": {}}}],
        temperature=None,
        top_p=None,
        model=None,
        stop=None,
        max_tokens=None,
        stream=False,
    )

    assert params["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert params["tools"][0]["cache_control"] == {"type": "ephemeral"}


def test_kv_affinity_agent_hint_moves_to_extra_body_for_openai_sdk():
    client_config = ModelClientConfig(
        client_provider="OpenAI",
        api_base="https://example.test/v1",
        auth_mode=LLMAuthMode.CustomHeaders,
        extensions={"kv_cache": {"mode": "affinity"}},
        verify_ssl=False,
    )
    client = OpenAIModelClient(ModelRequestConfig(model="qwen"), client_config)

    params = client._build_request_params(
        messages=[{"role": "user", "content": "hello"}],
        tools=None,
        temperature=None,
        top_p=None,
        model=None,
        stop=None,
        max_tokens=None,
        stream=False,
        session_id="child",
        parent_session_id="parent",
    )
    client._move_openai_extra_body_extensions(params)

    assert params["extra_body"]["agent_hint"] == {
        "session_id": "child",
        "parent_session_id": "parent",
    }
    assert "agent_hint" not in params


class _Delta:
    """Lightweight stand-in for an OpenAI SDK delta/message object."""

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class TestExtractReasoningContent:

    def test_reasoning_details_with_text_returns_text(self):
        delta = _Delta(reasoning_details=[{"text": "thinking..."}])
        assert OpenAIModelClient._extract_reasoning_content(delta) == "thinking..."

    def test_empty_reasoning_details_falls_back_to_reasoning_content(self):
        delta = _Delta(reasoning_details=[], reasoning_content="fallback")
        assert OpenAIModelClient._extract_reasoning_content(delta) == "fallback"

    def test_reasoning_details_first_item_missing_text_falls_back(self):
        delta = _Delta(reasoning_details=[{"no_text": "..."}], reasoning_content="fallback")
        assert OpenAIModelClient._extract_reasoning_content(delta) == "fallback"

    def test_missing_reasoning_details_falls_back_to_reasoning_content(self):
        delta = _Delta(reasoning_content="only reasoning")
        assert OpenAIModelClient._extract_reasoning_content(delta) == "only reasoning"

    def test_missing_reasoning_details_falls_back_to_reasoning_attr(self):
        delta = _Delta(reasoning="ollama style")
        assert OpenAIModelClient._extract_reasoning_content(delta) == "ollama style"

    def test_reasoning_details_first_item_non_dict_does_not_raise(self):
        delta = _Delta(reasoning_details=["bare-string-item"], reasoning_content="fallback")
        assert OpenAIModelClient._extract_reasoning_content(delta) == "fallback"

    def test_no_reasoning_fields_returns_none(self):
        delta = _Delta()
        assert OpenAIModelClient._extract_reasoning_content(delta) is None

    def test_falls_back_to_reasoning_token_text(self):
        delta = _Delta(reasoning_token_text="gateway think")
        assert OpenAIModelClient._extract_reasoning_content(delta) == "gateway think"

    def test_reasoning_details_text_empty_falls_back(self):
        delta = _Delta(reasoning_details=[{"text": ""}], reasoning_content="fallback")
        assert OpenAIModelClient._extract_reasoning_content(delta) == "fallback"


@pytest.mark.asyncio
async def test_parse_response_preserves_additive_provider_facts():
    response = _Obj(
        id="resp-1",
        model="returned-model",
        system_fingerprint="fp-1",
        service_tier="default",
        prompt_token_ids=[1, 2],
        usage=_Obj(
            prompt_tokens=3,
            completion_tokens=2,
            total_tokens=5,
            input_tokens_details=_Obj(
                cached_tokens=1,
                cache_creation_tokens=1,
            ),
        ),
        choices=[
            _Obj(
                message=_Obj(content="answer"),
                finish_reason="stop",
                token_ids=[3, 4],
                logprobs=None,
            )
        ],
    )

    message = await _make_client()._parse_response(response, None)

    assert message.response_id == "resp-1"
    assert message.response_model == "returned-model"
    assert message.provider_metadata == {
        "system_fingerprint": "fp-1",
        "service_tier": "default",
    }
    assert message.prompt_token_ids == [1, 2]
    assert message.completion_token_ids == [3, 4]
    assert message.usage_metadata.cache_tokens == 1
    assert message.usage_metadata.cache_creation_input_tokens == 1


class TestOpenAIResponsesApiKeyMode:
    """OpenAIModelClient with api_mode=responses talks to /responses."""

    @staticmethod
    def _make_responses_client() -> OpenAIModelClient:
        client_config = ModelClientConfig(
            client_provider="OpenAI",
            api_key="sk-test-key",
            api_base="https://api.openai.com/v1",
            api_mode=LLMApiMode.Responses,
            timeout=60.0,
            verify_ssl=False,
        )
        request_config = ModelRequestConfig(model="gpt-5.4-mini", temperature=0.2, top_p=0.1)
        return OpenAIModelClient(request_config, client_config)

    @staticmethod
    def _responses_stream_body() -> bytes:
        return (
            "event: response.output_text.delta\n"
            'data: {"delta":"ok"}\n\n'
            "event: response.completed\n"
            'data: {"response":{"id":"resp-1","model":"gpt-returned","status":"completed",'
            '"usage":{"input_tokens":2,"output_tokens":1,"total_tokens":3,'
            '"input_tokens_details":{"cache_creation_tokens":1}}}}\n\n'
        ).encode()

    def test_uses_responses_api_detects_api_mode(self):
        assert self._make_responses_client()._uses_responses_api() is True
        assert _make_client()._uses_responses_api() is False

    @pytest.mark.asyncio
    async def test_invoke_routes_to_responses_endpoint_with_api_key(self):
        import httpx

        seen_request = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            seen_request["headers"] = request.headers
            seen_request["body"] = json.loads(request.content.decode())
            return httpx.Response(
                200,
                content=self._responses_stream_body(),
                headers={"content-type": "text/event-stream"},
            )

        client = self._make_responses_client()
        with patch.object(
            client,
            "_make_responses_transport",
            return_value=OpenAIAccountResponsesTransport(transport=httpx.MockTransport(handler)),
        ):
            response = await client.invoke("hello", session_id="session-1", custom_headers={"X-Request": "v"})

        assert seen_request["headers"]["Authorization"] == "Bearer sk-test-key"
        assert seen_request["headers"]["X-Request"] == "v"
        assert seen_request["body"]["model"] == "gpt-5.4-mini"
        assert seen_request["body"]["stream"] is True
        assert response.content == "ok"
        assert response.response_id == "resp-1"
        assert response.usage_metadata.total_tokens == 3
        assert response.usage_metadata.cache_creation_input_tokens == 1

    @pytest.mark.asyncio
    async def test_invoke_does_not_send_sampling_params_by_default(self):
        import httpx

        seen_body = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            seen_body.update(json.loads(request.content.decode()))
            return httpx.Response(
                200,
                content=(
                    "event: response.output_text.delta\n"
                    'data: {"delta":"ok"}\n\n'
                    "event: response.completed\n"
                    'data: {"response":{"usage":{"input_tokens":1,"output_tokens":1,"total_tokens":2}}}\n\n'
                ).encode(),
                headers={"content-type": "text/event-stream"},
            )

        client = self._make_responses_client()
        with patch.object(
            client,
            "_make_responses_transport",
            return_value=OpenAIAccountResponsesTransport(transport=httpx.MockTransport(handler)),
        ):
            await client.invoke("hello", temperature=0.3, top_p=0.9)

        assert "temperature" not in seen_body
        assert "top_p" not in seen_body

    @pytest.mark.asyncio
    async def test_stream_routes_to_responses_endpoint_with_api_key(self):
        import httpx

        seen_request = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            seen_request["headers"] = request.headers
            seen_request["body"] = json.loads(request.content.decode())
            return httpx.Response(
                200,
                content=self._responses_stream_body(),
                headers={"content-type": "text/event-stream"},
            )

        client = self._make_responses_client()
        with patch.object(
            client,
            "_make_responses_transport",
            return_value=OpenAIAccountResponsesTransport(transport=httpx.MockTransport(handler)),
        ):
            chunks = [chunk async for chunk in client.stream("hello")]

        assert seen_request["headers"]["Authorization"] == "Bearer sk-test-key"
        assert seen_request["body"]["model"] == "gpt-5.4-mini"
        assert seen_request["body"]["stream"] is True
        assert "".join(chunk.content for chunk in chunks) == "ok"
        assert chunks[-1].usage_metadata.total_tokens == 3

    @pytest.mark.asyncio
    async def test_stream_routes_to_responses_endpoint_with_output_parser(self):
        import httpx

        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=(
                    "event: response.output_text.delta\n"
                    'data: {"delta":"ok"}\n\n'
                    "event: response.completed\n"
                    'data: {"response":{"usage":{"input_tokens":1,"output_tokens":1,"total_tokens":2}}}\n\n'
                ).encode(),
                headers={"content-type": "text/event-stream"},
            )

        client = self._make_responses_client()
        with patch.object(
            client,
            "_make_responses_transport",
            return_value=OpenAIAccountResponsesTransport(transport=httpx.MockTransport(handler)),
        ):
            chunks = [chunk async for chunk in client.stream("hello", output_parser=_UpperParser())]

        assert "".join(chunk.content for chunk in chunks) == "ok"
        assert any(chunk.parser_content == "OK" for chunk in chunks)

    @pytest.mark.asyncio
    async def test_invoke_wraps_responses_transport_error(self):
        import httpx

        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": {"message": "bad key"}})

        client = self._make_responses_client()
        with patch.object(
            client,
            "_make_responses_transport",
            return_value=OpenAIAccountResponsesTransport(transport=httpx.MockTransport(handler)),
        ):
            with pytest.raises(BaseError):
                await client.invoke("hello")

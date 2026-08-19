# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from unittest.mock import AsyncMock, patch

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.foundation.llm import (
    ModelClientConfig,
    ModelRequestConfig,
)
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


class TestDisabledThinkingCompatibility:
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
    async def test_invoke_retries_without_disabled_thinking_on_1210_and_caches_by_model(self):
        client = _make_client()
        sdk_client = _mock_sdk_client(
            _unsupported_disabled_thinking_error(),
            _response("fallback"),
            _response("cached"),
            _response("other"),
        )

        with patch.object(client, "_create_async_openai_client", return_value=sdk_client):
            result = await client.invoke("hello", **self._disabled_request_kwargs())
            cached_result = await client.invoke("hello again", **self._disabled_request_kwargs())
            other_result = await client.invoke(
                "hello other",
                model="other-model",
                **self._disabled_request_kwargs(),
            )

        assert result.content == "fallback"
        assert cached_result.content == "cached"
        assert other_result.content == "other"
        assert sdk_client.chat.completions.create.call_count == 4

        first_call = sdk_client.chat.completions.create.call_args_list[0].kwargs
        retry_call = sdk_client.chat.completions.create.call_args_list[1].kwargs
        cached_call = sdk_client.chat.completions.create.call_args_list[2].kwargs
        other_model_call = sdk_client.chat.completions.create.call_args_list[3].kwargs

        assert first_call["extra_body"] == {
            "routing": "blue",
            "thinking": {"type": "disabled"},
        }
        assert first_call["enable_thinking"] is False
        assert first_call["chat_template_kwargs"]["enable_thinking"] is False
        assert first_call["reasoning"]["enabled"] is False
        assert first_call["reasoning_effort"] == "off"

        for call in (retry_call, cached_call):
            assert call["model"] == "MiniMax-M3"
            assert call["extra_body"] == {"routing": "blue"}
            assert "enable_thinking" not in call
            assert call["chat_template_kwargs"] == {"template": "keep"}
            assert call["reasoning"] == {"budget": 32}
            assert "reasoning_effort" not in call

        assert other_model_call["model"] == "other-model"
        assert other_model_call["extra_body"] == {
            "routing": "blue",
            "thinking": {"type": "disabled"},
        }
        assert other_model_call["enable_thinking"] is False

    @pytest.mark.asyncio
    async def test_stream_retries_without_disabled_thinking_on_1210(self):
        client = _make_client()
        sdk_client = _mock_sdk_client(
            _unsupported_disabled_thinking_error(),
            _stream_response("a", "b"),
        )

        with patch.object(client, "_create_async_openai_client", return_value=sdk_client):
            chunks = [chunk.content async for chunk in client.stream("hello", **self._disabled_request_kwargs())]

        assert chunks == ["a", "b"]
        assert sdk_client.chat.completions.create.call_count == 2
        retry_call = sdk_client.chat.completions.create.call_args_list[1].kwargs
        assert retry_call["extra_body"] == {"routing": "blue"}
        assert "enable_thinking" not in retry_call
        assert retry_call["chat_template_kwargs"] == {"template": "keep"}
        assert retry_call["reasoning"] == {"budget": 32}
        assert "reasoning_effort" not in retry_call

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

    @pytest.mark.parametrize(
        ("request_kwargs", "error"),
        [
            (
                {"extra_body": {"routing": "blue"}},
                _unsupported_disabled_thinking_error(),
            ),
            (
                _disabled_request_kwargs.__func__(),
                _OpenAIStyleError(
                    "Unauthorized",
                    status_code=401,
                    body={"error": {"code": "1210", "message": "不支持关闭思考"}},
                ),
            ),
            (
                _disabled_request_kwargs.__func__(),
                _OpenAIStyleError(
                    "Rate limited",
                    status_code=429,
                    body={"error": {"code": "1210", "message": "不支持关闭思考"}},
                ),
            ),
            (
                _disabled_request_kwargs.__func__(),
                TimeoutError("timed out"),
            ),
            (
                _disabled_request_kwargs.__func__(),
                _OpenAIStyleError(
                    "Server error",
                    status_code=500,
                    body={"error": {"code": "1210", "message": "不支持关闭思考"}},
                ),
            ),
            (
                _disabled_request_kwargs.__func__(),
                _OpenAIStyleError("Bad request: invalid temperature", status_code=400),
            ),
        ],
    )
    @pytest.mark.asyncio
    async def test_non_compatibility_errors_do_not_retry(self, request_kwargs, error):
        client = _make_client()
        sdk_client = _mock_sdk_client(error)

        with patch.object(client, "_create_async_openai_client", return_value=sdk_client):
            with pytest.raises(BaseError):
                await client.invoke("hello", **request_kwargs)

        assert sdk_client.chat.completions.create.call_count == 1

    @pytest.mark.asyncio
    async def test_fallback_failure_is_not_retried_again(self):
        client = _make_client()
        sdk_client = _mock_sdk_client(
            _unsupported_disabled_thinking_error(),
            _OpenAIStyleError("fallback failed", status_code=400),
        )

        with patch.object(client, "_create_async_openai_client", return_value=sdk_client):
            with pytest.raises(BaseError) as exc_info:
                await client.invoke("hello", **self._disabled_request_kwargs())

        assert "fallback failed" in str(exc_info.value)
        assert sdk_client.chat.completions.create.call_count == 2


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

    def test_reasoning_details_text_empty_falls_back(self):
        delta = _Delta(reasoning_details=[{"text": ""}], reasoning_content="fallback")
        assert OpenAIModelClient._extract_reasoning_content(delta) == "fallback"

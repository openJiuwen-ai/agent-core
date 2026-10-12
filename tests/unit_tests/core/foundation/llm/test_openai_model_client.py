# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import json

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


class TestUtf8SafeRequestParams:
    """Gate: lone UTF-16 surrogates must not reach OpenAI JSON encode."""

    @staticmethod
    def _assert_utf8_json_encodable(params: dict) -> None:
        json.dumps(params, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")

    def test_build_request_params_strips_lone_surrogate(self):
        client = _make_client()
        # U+D83D is a high surrogate half; pairing with U+DE00 would form 😀, alone it is illegal UTF-8.
        lone_high = "前置\ud83d后置"
        lone_low = "尾\udc00半"
        intact_emoji = "完整\U0001F600表情"
        params = client._build_request_params(
            messages=[{"role": "user", "content": f"{lone_high}|{lone_low}|{intact_emoji}"}],
            tools=None,
            temperature=None,
            top_p=None,
            model=None,
            stop=None,
            max_tokens=None,
            stream=True,
        )
        self._assert_utf8_json_encodable(params)
        content = params["messages"][0]["content"]
        assert "\ud83d" not in content
        assert "\udc00" not in content
        assert "\U0001F600" in content
        assert "前置" in content and "后置" in content

    def test_build_request_params_sanitizes_nested_tool_strings(self):
        client = _make_client()
        params = client._build_request_params(
            messages=[{"role": "user", "content": "ok"}],
            tools=[{
                "type": "function",
                "function": {
                    "name": "demo",
                    "description": "desc\ud83d",
                    "parameters": {
                        "type": "object",
                        "properties": {"q": {"type": "string", "description": "q\udc00"}},
                    },
                },
            }],
            temperature=None,
            top_p=None,
            model=None,
            stop=None,
            max_tokens=None,
            stream=False,
        )
        self._assert_utf8_json_encodable(params)
        dumped = json.dumps(params, ensure_ascii=False)
        assert "\ud83d" not in dumped
        assert "\udc00" not in dumped


class TestUtf8SafeInboundAssistantReply:
    """Gate: inbound LLM stream/non-stream text must be UTF-8 safe before WS/history."""

    def test_assistant_message_chunk_replaces_lone_surrogate(self):
        from openjiuwen.core.foundation.llm.schema.message_chunk import AssistantMessageChunk

        chunk = AssistantMessageChunk(
            content="【注入】\ud83d【结束】\U0001F600",
            reasoning_content="想\ud83d法",
        )
        assert "\ud83d" not in chunk.content
        assert "\ufffd" in chunk.content
        assert "\U0001F600" in chunk.content
        assert "【注入】" in chunk.content and "【结束】" in chunk.content
        assert "\ud83d" not in (chunk.reasoning_content or "")
        assert "\ufffd" in (chunk.reasoning_content or "")
        # Must survive JSON UTF-8 encode used on wire/history paths.
        json.dumps(
            {"content": chunk.content, "reasoning": chunk.reasoning_content},
            ensure_ascii=False,
        ).encode("utf-8")

    def test_tool_call_arguments_sanitized_on_construct(self):
        from openjiuwen.core.foundation.llm.schema.tool_call import ToolCall

        tc = ToolCall(
            id="call_1",
            type="function",
            name="demo",
            arguments='{"q":"坏\ud83d"}',
            index=0,
        )
        assert "\ud83d" not in tc.arguments
        assert "\ufffd" in tc.arguments
        tc.arguments.encode("utf-8")

    def test_parse_stream_chunk_sanitizes_delta_content(self):
        client = _make_client()

        class _Fn:
            name = ""
            arguments = '{"x":"\ud83d"}'

        class _Tc:
            id = "c1"
            index = 0
            function = _Fn()

        class _Choice:
            finish_reason = None
            delta = _Delta(
                content="答\ud83d案\U0001F600",
                reasoning_content="理\ud83d由",
                tool_calls=[_Tc()],
            )
            logprobs = None
            token_ids = None

        class _Chunk:
            choices = [_Choice()]
            usage = None
            id = "chatcmpl-test"
            model = "GLM-5.3-Flash"
            prompt_token_ids = None
            system_fingerprint = None
            service_tier = None

        parsed = client._parse_stream_chunk(_Chunk())
        assert parsed is not None
        assert "\ud83d" not in parsed.content
        assert "\ufffd" in parsed.content
        assert "\U0001F600" in parsed.content
        assert "\ud83d" not in (parsed.reasoning_content or "")
        assert parsed.tool_calls
        assert "\ud83d" not in parsed.tool_calls[0].arguments
        json.dumps(
            {
                "content": parsed.content,
                "reasoning": parsed.reasoning_content,
                "args": parsed.tool_calls[0].arguments,
            },
            ensure_ascii=False,
        ).encode("utf-8")


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

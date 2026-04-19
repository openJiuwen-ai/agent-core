# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import os
import unittest
from typing import Any, Mapping
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from openjiuwen.core.common.clients.llm_client import create_async_openai_client, create_openai_client
from openjiuwen.core.foundation.llm import (
    Model,
    ModelClientConfig,
    ModelRequestConfig,
    ProviderType,
    UserMessage,
    init_model,
)
from openjiuwen.core.single_agent.agents.react_agent import ReActAgentConfig


REAL_LLM_PROVIDER = os.getenv("REAL_LLM_PROVIDER", "OpenAI")
REAL_LLM_MODEL_NAME = os.getenv("REAL_LLM_MODEL_NAME", "qwen-plus")
REAL_LLM_API_KEY = os.getenv("REAL_LLM_API_KEY", "").strip()
REAL_LLM_API_BASE = os.getenv("REAL_LLM_API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1")
REAL_LLM_VERIFY_SSL = os.getenv("REAL_LLM_VERIFY_SSL", "false").lower() == "true"
REAL_LLM_TIMEOUT = float(os.getenv("REAL_LLM_TIMEOUT", "60"))


def _has_real_llm_params() -> bool:
    return bool(REAL_LLM_PROVIDER and REAL_LLM_MODEL_NAME and REAL_LLM_API_KEY and REAL_LLM_API_BASE)


@unittest.skip("temporarily skipped while upstream integration stabilizes")
class TestCustomHeadersSystem(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.use_real_llm = _has_real_llm_params()

    @staticmethod
    def _build_mock_response(content: str = "ok") -> MagicMock:
        response = MagicMock()
        response.choices = [MagicMock()]
        response.choices[0].message = MagicMock()
        response.choices[0].message.content = content
        response.choices[0].message.tool_calls = None
        response.choices[0].message.reasoning_content = None
        response.usage = MagicMock()
        response.usage.prompt_tokens = 5
        response.usage.completion_tokens = 3
        response.usage.total_tokens = 8
        response.usage.prompt_tokens_details = None
        return response

    @staticmethod
    def _build_stream_chunk(content: str = "ok") -> MagicMock:
        chunk = MagicMock()
        chunk.choices = [MagicMock()]
        chunk.choices[0].delta = MagicMock()
        chunk.choices[0].delta.content = content
        chunk.choices[0].delta.reasoning_content = None
        chunk.choices[0].delta.tool_calls = None
        chunk.choices[0].finish_reason = "stop"
        chunk.usage = None
        return chunk

    @staticmethod
    def _find_header_value(headers: Mapping[str, str], key: str) -> str:
        target = key.lower()
        for header_key, header_value in headers.items():
            if header_key.lower() == target:
                return header_value
        return ""

    @staticmethod
    def _build_model(mode: str, custom_headers=None, *, use_real_llm: bool = False) -> Model:
        provider = REAL_LLM_PROVIDER if use_real_llm else ProviderType.OpenAI.value
        model_name = REAL_LLM_MODEL_NAME if use_real_llm else "gpt-4o-mini"
        api_key = REAL_LLM_API_KEY if use_real_llm else "sk-test"
        api_base = REAL_LLM_API_BASE if use_real_llm else "https://api.openai.com/v1"
        verify_ssl = REAL_LLM_VERIFY_SSL if use_real_llm else False

        if mode == "model":
            return Model(
                model_client_config=ModelClientConfig(
                    client_provider=provider,
                    api_key=api_key,
                    api_base=api_base,
                    verify_ssl=verify_ssl,
                    custom_headers=custom_headers,
                ),
                model_config=ModelRequestConfig(model=model_name),
            )

        if mode == "init_model":
            return init_model(
                provider=provider,
                model_name=model_name,
                api_key=api_key,
                api_base=api_base,
                verify_ssl=verify_ssl,
                custom_headers=custom_headers,
            )

        if mode == "react_config":
            config = ReActAgentConfig()
            config.configure_custom_headers(custom_headers)
            config.configure_model_client(
                provider=provider,
                api_key=api_key,
                api_base=api_base,
                model_name=model_name,
                verify_ssl=verify_ssl,
            )
            return Model(
                model_client_config=config.model_client_config,
                model_config=config.model_config_obj,
            )

        raise ValueError(f"Unsupported mode: {mode}")

    @staticmethod
    def _install_httpx_send_header_capture() -> tuple[list[dict[str, str]], Any]:
        captured_headers: list[dict[str, str]] = []
        original_send = httpx.AsyncClient.send

        async def _wrapped_send(client: httpx.AsyncClient, request: httpx.Request, *args: Any, **kwargs: Any):
            captured_headers.append(dict(request.headers.items()))
            return await original_send(client, request, *args, **kwargs)

        httpx.AsyncClient.send = _wrapped_send

        def _restore() -> None:
            httpx.AsyncClient.send = original_send

        return captured_headers, _restore

    def _assert_effective_headers(self, sent: dict, expected: dict[str, str]) -> None:
        self.assertEqual(sent.get("extra_headers", {}), expected)
        if not self.use_real_llm or not expected:
            return

        http_headers = sent.get("_http_headers", {})
        self.assertTrue(http_headers, "Expected real mode to capture outgoing HTTP headers")
        for key, expected_value in expected.items():
            self.assertEqual(self._find_header_value(http_headers, key), expected_value)

    async def _invoke_and_get_sent_params(self, model: Model, request_headers=None) -> dict:
        if self.use_real_llm:
            sent_params: dict[str, Any] = {}

            async def tracer_record_data(**kwargs):
                llm_params = kwargs.get("llm_params") or {}
                if "extra_headers" in llm_params:
                    sent_params["extra_headers"] = dict(llm_params["extra_headers"])

            invoke_kwargs = {}
            if request_headers is not None:
                invoke_kwargs["custom_headers"] = request_headers

            captured_http_headers, restore_httpx_send = self._install_httpx_send_header_capture()
            try:
                await model.invoke(
                    messages=[UserMessage(content="请回复: header test ok")],
                    tracer_record_data=tracer_record_data,
                    timeout=REAL_LLM_TIMEOUT,
                    **invoke_kwargs,
                )
            finally:
                restore_httpx_send()

            if captured_http_headers:
                sent_params["_http_headers"] = captured_http_headers[-1]
            return sent_params

        mock_async_client = AsyncMock()
        mock_async_client.chat.completions.create = AsyncMock(return_value=self._build_mock_response())

        invoke_kwargs = {}
        if request_headers is not None:
            invoke_kwargs["custom_headers"] = request_headers

        with patch.object(model._client, "_create_async_openai_client", return_value=mock_async_client):
            await model.invoke(messages=[UserMessage(content="hello")], **invoke_kwargs)

        return mock_async_client.chat.completions.create.call_args.kwargs

    async def _stream_and_get_sent_params(self, model: Model, request_headers=None) -> dict:
        if self.use_real_llm:
            sent_params: dict[str, Any] = {}

            async def tracer_record_data(**kwargs):
                llm_params = kwargs.get("llm_params") or {}
                if "extra_headers" in llm_params:
                    sent_params["extra_headers"] = dict(llm_params["extra_headers"])

            stream_kwargs = {}
            if request_headers is not None:
                stream_kwargs["custom_headers"] = request_headers

            captured_http_headers, restore_httpx_send = self._install_httpx_send_header_capture()
            try:
                async for _ in model.stream(
                    messages=[UserMessage(content="请回复: stream header test ok")],
                    tracer_record_data=tracer_record_data,
                    timeout=REAL_LLM_TIMEOUT,
                    **stream_kwargs,
                ):
                    pass
            finally:
                restore_httpx_send()

            if captured_http_headers:
                sent_params["_http_headers"] = captured_http_headers[-1]
            return sent_params

        async def _chunk_generator():
            yield self._build_stream_chunk("hello")

        mock_async_client = AsyncMock()
        mock_async_client.chat.completions.create = AsyncMock(return_value=_chunk_generator())

        stream_kwargs = {}
        if request_headers is not None:
            stream_kwargs["custom_headers"] = request_headers

        with patch.object(model._client, "_create_async_openai_client", return_value=mock_async_client):
            async for _ in model.stream(messages=[UserMessage(content="hello")], **stream_kwargs):
                pass

        return mock_async_client.chat.completions.create.call_args.kwargs

    async def test_model_invoke_injects_sanitized_config_headers(self):
        model = self._build_model(
            "model",
            use_real_llm=self.use_real_llm,
            custom_headers={
                "Token": "token-a",
                "UserID": "user-a",
                "Authorization": "blocked",
                "X-None": None,
            },
        )

        sent = await self._invoke_and_get_sent_params(model)
        self._assert_effective_headers(sent, {"Token": "token-a", "UserID": "user-a"})

    async def test_model_invoke_request_headers_override_case_insensitive(self):
        model = self._build_model(
            "model",
            use_real_llm=self.use_real_llm,
            custom_headers={"X-Tenant": "tenant-cfg", "UserID": "user-cfg"},
        )

        sent = await self._invoke_and_get_sent_params(
            model,
            request_headers={
                "x-tenant": "tenant-req",
                "userid": "user-req",
                "Connection": "blocked",
            },
        )

        self._assert_effective_headers(sent, {"X-Tenant": "tenant-req", "UserID": "user-req"})

    async def test_model_invoke_without_headers_has_no_extra_headers(self):
        model = self._build_model("model", custom_headers=None, use_real_llm=self.use_real_llm)

        sent = await self._invoke_and_get_sent_params(model)
        self.assertNotIn("extra_headers", sent)

    async def test_model_stream_injects_sanitized_config_headers(self):
        model = self._build_model(
            "model",
            use_real_llm=self.use_real_llm,
            custom_headers={"UserID": "stream-cfg", "Host": "blocked"},
        )

        sent = await self._stream_and_get_sent_params(model)
        self._assert_effective_headers(sent, {"UserID": "stream-cfg"})

    async def test_model_stream_request_headers_override(self):
        model = self._build_model(
            "model",
            use_real_llm=self.use_real_llm,
            custom_headers={"Token": "cfg", "UserID": "cfg-user"},
        )

        sent = await self._stream_and_get_sent_params(
            model,
            request_headers={"token": "req", "userid": "req-user", "Authorization": "blocked"},
        )

        self._assert_effective_headers(sent, {"Token": "req", "UserID": "req-user"})

    async def test_init_model_invoke_injects_headers(self):
        model = self._build_model(
            "init_model",
            use_real_llm=self.use_real_llm,
            custom_headers={"Token": "init-token", "Content-Length": "blocked"},
        )

        sent = await self._invoke_and_get_sent_params(model)
        self._assert_effective_headers(sent, {"Token": "init-token"})

    async def test_init_model_stream_request_headers_override(self):
        model = self._build_model(
            "init_model",
            use_real_llm=self.use_real_llm,
            custom_headers={"UserID": "init-user"},
        )

        sent = await self._stream_and_get_sent_params(
            model,
            request_headers={"userid": "init-user-req", "Transfer-Encoding": "blocked"},
        )

        self._assert_effective_headers(sent, {"UserID": "init-user-req"})

    async def test_react_config_invoke_injects_headers(self):
        model = self._build_model(
            "react_config",
            use_real_llm=self.use_real_llm,
            custom_headers={"Token": "react-token", "Connection": "blocked"},
        )

        sent = await self._invoke_and_get_sent_params(model)
        self._assert_effective_headers(sent, {"Token": "react-token"})

    async def test_react_config_stream_request_headers_override(self):
        model = self._build_model(
            "react_config",
            use_real_llm=self.use_real_llm,
            custom_headers={"UserID": "react-cfg-user"},
        )

        sent = await self._stream_and_get_sent_params(
            model,
            request_headers={"userid": "react-req-user", "Host": "blocked"},
        )

        self._assert_effective_headers(sent, {"UserID": "react-req-user"})

    async def test_react_config_without_headers_has_no_extra_headers(self):
        model = self._build_model("react_config", custom_headers=None, use_real_llm=self.use_real_llm)

        sent = await self._invoke_and_get_sent_params(model)
        self.assertNotIn("extra_headers", sent)

    async def test_common_async_openai_client_forwards_sanitized_default_headers(self):
        fake_httpx_client = object()

        with patch(
            "openjiuwen.core.common.clients.llm_client.create_httpx_client",
            new=AsyncMock(return_value=fake_httpx_client),
        ), patch("openai.AsyncOpenAI") as mock_async_openai:
            await create_async_openai_client(
                ModelClientConfig(
                    client_provider=ProviderType.OpenAI,
                    api_key="sk-test",
                    api_base="https://api.openai.com/v1",
                    verify_ssl=False,
                    custom_headers={
                        "Token": "token-x",
                        "Authorization": "blocked",
                        "X-Blank": " ",
                    },
                )
            )

        kwargs = mock_async_openai.call_args.kwargs
        self.assertEqual(kwargs.get("default_headers"), {"Token": "token-x"})

    async def test_common_sync_openai_client_forwards_sanitized_default_headers(self):
        fake_httpx_client = object()

        with patch(
            "openjiuwen.core.common.clients.llm_client.create_httpx_client",
            new=AsyncMock(return_value=fake_httpx_client),
        ), patch("openai.OpenAI") as mock_openai:
            await create_openai_client(
                ModelClientConfig(
                    client_provider=ProviderType.OpenAI,
                    api_key="sk-test",
                    api_base="https://api.openai.com/v1",
                    verify_ssl=False,
                    custom_headers={
                        "UserID": "user-x",
                        "Content-Length": "blocked",
                        "X-None": None,
                    },
                )
            )

        kwargs = mock_openai.call_args.kwargs
        self.assertEqual(kwargs.get("default_headers"), {"UserID": "user-x"})

    async def test_common_openai_client_without_custom_headers_omits_default_headers(self):
        fake_httpx_client = object()

        with patch(
            "openjiuwen.core.common.clients.llm_client.create_httpx_client",
            new=AsyncMock(return_value=fake_httpx_client),
        ), patch("openai.AsyncOpenAI") as mock_async_openai:
            await create_async_openai_client(
                ModelClientConfig(
                    client_provider=ProviderType.OpenAI,
                    api_key="sk-test",
                    api_base="https://api.openai.com/v1",
                    verify_ssl=False,
                    custom_headers=None,
                )
            )

        kwargs = mock_async_openai.call_args.kwargs
        self.assertNotIn("default_headers", kwargs)

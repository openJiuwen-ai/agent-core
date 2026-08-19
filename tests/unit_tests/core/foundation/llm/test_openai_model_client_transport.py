# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for OpenAIModelClient._create_async_openai_client transport wiring.

Locks the transport-layer refactor introduced in IR-2026-0813-001:

    httpx.AsyncClient(proxy=..., verify=...)
    ->
    httpx.AsyncHTTPTransport(proxy=..., verify=..., limits=..., socket_options=...)
    + httpx.AsyncClient(transport=transport)

Existing tests in test_model_no_custom_headers.py / test_model_custom_headers.py
mock the entire ``_create_async_openai_client`` method, so the transport
construction parameters were never asserted. These tests fill that gap by
mocking the internal dependencies (``httpx.AsyncHTTPTransport``,
``httpx.Limits``, ``httpx.AsyncClient``, ``openai.AsyncOpenAI``,
``UrlUtils.get_global_proxy_url``, ``SslUtils``) and letting the method body
execute, then asserting on the captured call args.

Covers the three contract points requested by the PR review:

1. ``transport`` is created with a non-empty ``socket_options`` list.
2. ``limits.keepalive_expiry == 60.0`` (the bump from httpx default 5s).
3. ``proxy`` and ``verify`` are forwarded into ``AsyncHTTPTransport``.
"""

from unittest.mock import MagicMock, patch

import pytest

from openjiuwen.core.foundation.llm import (
    ModelClientConfig,
    ModelRequestConfig,
    ProviderType,
)
from openjiuwen.core.foundation.llm.model_clients.openai_model_client import OpenAIModelClient


_CLIENT_MODULE = "openjiuwen.core.foundation.llm.model_clients.openai_model_client"


def _make_client(
    verify_ssl: bool = False,
    api_base: str = "https://api.openai.com/v1",
    ssl_cert: str = None,
) -> OpenAIModelClient:
    return OpenAIModelClient(
        model_config=ModelRequestConfig(model="gpt-4o-mini"),
        model_client_config=ModelClientConfig(
            client_provider=ProviderType.OpenAI,
            api_key="sk-test",
            api_base=api_base,
            verify_ssl=verify_ssl,
            ssl_cert=ssl_cert,
        ),
    )


class TestCreateAsyncOpenaiClientTransportWiring:
    """Verify _create_async_openai_client forwards parameters into AsyncHTTPTransport."""

    @pytest.fixture
    def captured(self):
        """Run _create_async_openai_client with mocked deps; return mock call sites."""
        with patch(f"{_CLIENT_MODULE}.httpx.AsyncHTTPTransport", return_value=MagicMock()) as transport_class, \
             patch(f"{_CLIENT_MODULE}.httpx.AsyncClient", return_value=MagicMock()) as client_class, \
             patch(f"{_CLIENT_MODULE}.httpx.Limits") as limits_class, \
             patch(f"{_CLIENT_MODULE}.UrlUtils.get_global_proxy_url", return_value="http://global-proxy:8080"), \
             patch(f"{_CLIENT_MODULE}.SslUtils.create_strict_ssl_context", return_value="SSL_CTX_STUB"), \
             patch("openai.AsyncOpenAI", return_value=MagicMock()) as openai_class:

            client = _make_client(verify_ssl=False)
            client._create_async_openai_client(timeout=42.0)

            yield {
                "transport_class": transport_class,
                "client_class": client_class,
                "limits_class": limits_class,
                "openai_class": openai_class,
            }

    def test_transport_created_exactly_once(self, captured):
        assert captured["transport_class"].call_count == 1

    def test_transport_created_with_non_empty_socket_options(self, captured):
        kwargs = captured["transport_class"].call_args.kwargs
        assert "socket_options" in kwargs
        assert kwargs["socket_options"]  # non-empty list with SO_KEEPALIVE

    def test_socket_options_is_a_list_of_socket_option_tuples(self, captured):
        kwargs = captured["transport_class"].call_args.kwargs
        opts = kwargs["socket_options"]
        assert isinstance(opts, list)
        assert all(isinstance(t, tuple) and len(t) == 3 for t in opts)

    def test_limits_keepalive_expiry_is_60_seconds(self, captured):
        limits_class = captured["limits_class"]
        assert limits_class.called
        kwargs = limits_class.call_args.kwargs
        assert kwargs["keepalive_expiry"] == 60.0

    def test_limits_pool_sizes_match_design(self, captured):
        kwargs = captured["limits_class"].call_args.kwargs
        assert kwargs["max_connections"] == 100
        assert kwargs["max_keepalive_connections"] == 20

    def test_proxy_forwarded_to_transport(self, captured):
        kwargs = captured["transport_class"].call_args.kwargs
        assert kwargs["proxy"] == "http://global-proxy:8080"

    def test_verify_false_forwarded_to_transport_when_ssl_disabled(self, captured):
        kwargs = captured["transport_class"].call_args.kwargs
        assert kwargs["verify"] is False

    def test_async_client_constructed_with_transport_instance(self, captured):
        transport_mock = captured["transport_class"].return_value
        client_class = captured["client_class"]
        client_class.assert_called_once()
        assert client_class.call_args.kwargs["transport"] is transport_mock

    def test_openai_client_receives_http_client_and_timeout(self, captured):
        openai_class = captured["openai_class"]
        openai_class.assert_called_once()
        kwargs = openai_class.call_args.kwargs
        assert kwargs["api_key"] == "sk-test"
        assert kwargs["base_url"] == "https://api.openai.com/v1"
        assert kwargs["timeout"] == 42.0
        assert kwargs["http_client"] is captured["client_class"].return_value


class TestVerifySslForwarding:
    """When verify_ssl=True, an SSL context is built and forwarded to transport."""

    def test_ssl_context_forwarded_to_transport_when_verify_enabled(self):
        with patch(f"{_CLIENT_MODULE}.httpx.AsyncHTTPTransport", return_value=MagicMock()) as transport_class, \
             patch(f"{_CLIENT_MODULE}.httpx.AsyncClient", return_value=MagicMock()), \
             patch(f"{_CLIENT_MODULE}.httpx.Limits"), \
             patch(f"{_CLIENT_MODULE}.UrlUtils.get_global_proxy_url", return_value=None), \
             patch(f"{_CLIENT_MODULE}.SslUtils.create_strict_ssl_context", return_value="SSL_CTX_STUB"), \
             patch("openai.AsyncOpenAI", return_value=MagicMock()):

            client = _make_client(verify_ssl=True, ssl_cert="/fake/cert.pem")
            client._create_async_openai_client()

        kwargs = transport_class.call_args.kwargs
        assert kwargs["verify"] == "SSL_CTX_STUB"


class TestProxyNoneHandling:
    """When UrlUtils returns None (no global proxy), None is forwarded to transport."""

    def test_proxy_none_forwarded_when_no_global_proxy(self):
        with patch(f"{_CLIENT_MODULE}.httpx.AsyncHTTPTransport", return_value=MagicMock()) as transport_class, \
             patch(f"{_CLIENT_MODULE}.httpx.AsyncClient", return_value=MagicMock()), \
             patch(f"{_CLIENT_MODULE}.httpx.Limits"), \
             patch(f"{_CLIENT_MODULE}.UrlUtils.get_global_proxy_url", return_value=None), \
             patch(f"{_CLIENT_MODULE}.SslUtils.create_strict_ssl_context", return_value="SSL_CTX_STUB"), \
             patch("openai.AsyncOpenAI", return_value=MagicMock()):

            client = _make_client(verify_ssl=False)
            client._create_async_openai_client()

        kwargs = transport_class.call_args.kwargs
        assert kwargs["proxy"] is None
        assert "socket_options" in kwargs  # keepalive still applied

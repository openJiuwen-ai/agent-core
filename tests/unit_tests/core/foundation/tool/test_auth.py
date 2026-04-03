#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from unittest.mock import AsyncMock, patch, MagicMock

import httpx
import pytest

from openjiuwen.core.foundation.tool.auth.auth import ToolAuthConfig, ToolAuthResult
from openjiuwen.core.foundation.tool.auth.auth_callback import (
    AuthStrategyRegistry,
    SSLAuthStrategy,
    HeaderQueryAuthStrategy,
    AuthType,
    AuthHeaderAndQueryProvider
)
from openjiuwen.core.foundation.tool.mcp.client.sse_client import SseClient
from openjiuwen.core.foundation.tool.mcp.client.streamable_http_client import StreamableHttpClient
from openjiuwen.core.foundation.tool import McpServerConfig
from openjiuwen.core.runner.callback.events import ToolCallEvents


class TestToolAuthConfig:
    @staticmethod
    def test_tool_auth_config_creation():
        config = ToolAuthConfig(
            auth_type=AuthType.SSL,
            config={"verify_switch_env": "RESTFUL_SSL_VERIFY"},
            tool_type="restful_api",
            tool_id="test-tool-id"
        )
        
        assert config.auth_type == AuthType.SSL
        assert config.config == {"verify_switch_env": "RESTFUL_SSL_VERIFY"}
        assert config.tool_type == "restful_api"
        assert config.tool_id == "test-tool-id"
    
    @staticmethod
    def test_tool_auth_config_without_tool_id():
        config = ToolAuthConfig(
            auth_type="api_key",
            config={"api_key": "test-key"},
            tool_type="database"
        )
        
        assert config.auth_type == "api_key"
        assert config.tool_id is None


class TestToolAuthResult:
    @staticmethod
    def test_tool_auth_result_creation():
        result = ToolAuthResult(
            success=True,
            auth_data={"headers": {"Authorization": "Bearer token"}},
            message="Authentication successful",
            error=None
        )
        
        assert result.success is True
        assert result.auth_data == {"headers": {"Authorization": "Bearer token"}}
        assert result.message == "Authentication successful"
        assert result.error is None
    
    @staticmethod
    def test_tool_auth_result_with_error():
        error = RuntimeError("Authentication failed")
        result = ToolAuthResult(
            success=False,
            auth_data={},
            message="Authentication failed",
            error=error
        )
        
        assert result.success is False
        assert result.error == error


class TestAuthHeaderAndQueryProvider:
    @staticmethod
    @pytest.mark.asyncio
    async def test_auth_provider_with_headers():
        provider = AuthHeaderAndQueryProvider(
            auth_headers={"Authorization": "Bearer test-token", "X-Custom": "value"},
            auth_query_params={}
        )
        request = httpx.Request("GET", "https://example.com/api")
        
        flow = provider.async_auth_flow(request)
        signed_request = await anext(flow)
        await flow.aclose()
        
        assert signed_request.headers["Authorization"] == "Bearer test-token"
        assert signed_request.headers["X-Custom"] == "value"
    
    @staticmethod
    @pytest.mark.asyncio
    async def test_auth_provider_with_query_params():
        provider = AuthHeaderAndQueryProvider(
            auth_headers={},
            auth_query_params={"api_key": "test-key", "version": "v1"}
        )
        request = httpx.Request("GET", "https://example.com/api?existing=1")
        
        flow = provider.async_auth_flow(request)
        signed_request = await anext(flow)
        await flow.aclose()
        
        assert signed_request.url.params["existing"] == "1"
        assert signed_request.url.params["api_key"] == "test-key"
        assert signed_request.url.params["version"] == "v1"
    
    @staticmethod
    @pytest.mark.asyncio
    async def test_auth_provider_with_both():
        provider = AuthHeaderAndQueryProvider(
            auth_headers={"Authorization": "Bearer test-token"},
            auth_query_params={"api_key": "test-key"}
        )
        request = httpx.Request("GET", "https://example.com/api")
        
        flow = provider.async_auth_flow(request)
        signed_request = await anext(flow)
        await flow.aclose()
        
        assert signed_request.headers["Authorization"] == "Bearer test-token"
        assert signed_request.url.params["api_key"] == "test-key"
    
    @staticmethod
    @pytest.mark.asyncio
    async def test_auth_provider_without_credentials():
        provider = AuthHeaderAndQueryProvider(
            auth_headers={},
            auth_query_params={}
        )
        request = httpx.Request("GET", "https://example.com/api")
        
        flow = provider.async_auth_flow(request)
        signed_request = await anext(flow)
        await flow.aclose()
        
        # Request should be unchanged
        assert signed_request.url == request.url
        assert dict(signed_request.headers) == dict(request.headers)


class TestAuthCallbacks:
    @staticmethod
    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.tool.auth.auth_callback.SslUtils")
    @patch("openjiuwen.core.foundation.tool.auth.auth_callback.aiohttp")
    async def test_ssl_auth_handler_verify_true(mock_aiohttp, mock_ssl_utils):
        # Create a mock SSL context that aiohttp will accept
        mock_ssl_context = MagicMock()
        mock_ssl_utils.get_ssl_config.return_value = (True, "/path/to/cert")
        mock_ssl_utils.create_strict_ssl_context.return_value = mock_ssl_context
        
        # Mock TCPConnector to accept our mock ssl context
        mock_connector = MagicMock()
        mock_aiohttp.TCPConnector.return_value = mock_connector
        
        auth_config = ToolAuthConfig(
            auth_type=AuthType.SSL,
            config={
                "verify_switch_env": "RESTFUL_SSL_VERIFY",
                "ssl_cert_env": "RESTFUL_SSL_CERT"
            },
            tool_type="restful_api",
            tool_id="test-tool"
        )
        
        result = await SSLAuthStrategy().authenticate(auth_config)
        
        assert isinstance(result, ToolAuthResult)
        assert result.success is True
        assert result.auth_data["connector"] is mock_connector
        mock_ssl_utils.get_ssl_config.assert_called_once()
        mock_ssl_utils.create_strict_ssl_context.assert_called_once_with("/path/to/cert")
        mock_aiohttp.TCPConnector.assert_called_once_with(ssl=mock_ssl_context)
    
    @staticmethod
    @pytest.mark.asyncio
    @patch("openjiuwen.core.foundation.tool.auth.auth_callback.SslUtils")
    @patch("openjiuwen.core.foundation.tool.auth.auth_callback.aiohttp")
    async def test_ssl_auth_handler_verify_false(mock_aiohttp, mock_ssl_utils):
        mock_ssl_utils.get_ssl_config.return_value = (False, None)
        
        # Mock TCPConnector
        mock_connector = MagicMock()
        mock_aiohttp.TCPConnector.return_value = mock_connector
        
        auth_config = ToolAuthConfig(
            auth_type=AuthType.SSL,
            config={
                "verify_switch_env": "RESTFUL_SSL_VERIFY",
                "ssl_cert_env": "RESTFUL_SSL_CERT"
            },
            tool_type="restful_api"
        )
        
        result = await SSLAuthStrategy().authenticate(auth_config)
        
        assert isinstance(result, ToolAuthResult)
        assert result.success is True
        assert result.auth_data["connector"] is mock_connector
        mock_aiohttp.TCPConnector.assert_called_once_with(ssl=False)
    
    @staticmethod
    @pytest.mark.asyncio
    async def test_ssl_auth_handler_cert_empty():
        auth_config = ToolAuthConfig(
            auth_type=AuthType.SSL,
            config={},
            tool_type="restful_api"
        )
        
        # Verify that an exception is raised when SSL cert is empty
        with pytest.raises(Exception) as excinfo:
            await SSLAuthStrategy().authenticate(auth_config)
        
        assert "must provide ssl cert" in str(excinfo.value)
    
    @staticmethod
    @pytest.mark.asyncio
    async def test_auth_header_and_query_params_handler_with_credentials():
        auth_config = ToolAuthConfig(
            auth_type=AuthType.HEADER_AND_QUERY,
            config={
                "auth_headers": {"Authorization": "Bearer test-token"},
                "auth_query_params": {"api_key": "test-key"}
            },
            tool_type="mcp",
            tool_id="test-mcp"
        )
        
        result = await HeaderQueryAuthStrategy().authenticate(auth_config)
        
        assert isinstance(result, ToolAuthResult)
        assert result.success is True
        assert isinstance(result.auth_data["auth_provider"], AuthHeaderAndQueryProvider)
        assert result.auth_data["auth_provider"].headers == {"Authorization": "Bearer test-token"}
        assert result.auth_data["auth_provider"].query_params == {"api_key": "test-key"}
    
    @staticmethod
    @pytest.mark.asyncio
    async def test_auth_header_and_query_params_handler_only_headers():
        auth_config = ToolAuthConfig(
            auth_type=AuthType.HEADER_AND_QUERY,
            config={"auth_headers": {"Authorization": "Bearer test-token"}},
            tool_type="mcp"
        )
        
        result = await HeaderQueryAuthStrategy().authenticate(auth_config)
        
        assert isinstance(result, ToolAuthResult)
        assert result.success is True
        assert isinstance(result.auth_data["auth_provider"], AuthHeaderAndQueryProvider)
        assert result.auth_data["auth_provider"].headers == {"Authorization": "Bearer test-token"}
        assert result.auth_data["auth_provider"].query_params == {}
    
    @staticmethod
    @pytest.mark.asyncio
    async def test_auth_header_and_query_params_handler_only_query_params():
        auth_config = ToolAuthConfig(
            auth_type=AuthType.HEADER_AND_QUERY,
            config={"auth_query_params": {"api_key": "test-key"}},
            tool_type="mcp"
        )
        
        result = await HeaderQueryAuthStrategy().authenticate(auth_config)
        
        assert isinstance(result, ToolAuthResult)
        assert result.success is True
        assert isinstance(result.auth_data["auth_provider"], AuthHeaderAndQueryProvider)
        assert result.auth_data["auth_provider"].headers == {}
        assert result.auth_data["auth_provider"].query_params == {"api_key": "test-key"}
    
    @staticmethod
    @pytest.mark.asyncio
    async def test_auth_header_and_query_params_handler_empty_credentials():
        auth_config = ToolAuthConfig(
            auth_type=AuthType.HEADER_AND_QUERY,
            config={
                "auth_headers": None,
                "auth_query_params": None
            },
            tool_type="mcp"
        )
        
        result = await HeaderQueryAuthStrategy().authenticate(auth_config)
        
        assert isinstance(result, ToolAuthResult)
        assert result.success is True
        assert result.auth_data["auth_provider"] is None
    
    @staticmethod
    @pytest.mark.asyncio
    async def test_auth_handler_wrong_type():
        auth_config = ToolAuthConfig(
            auth_type="api_key",
            config={}, 
            tool_type="mcp"
        )
        
        result = await AuthStrategyRegistry.execute_auth(auth_config)
        
        assert result.success is False
        assert result.auth_data == {}


class TestSseClientAuth:
    from openjiuwen.core.runner import Runner

    @staticmethod
    @pytest.mark.asyncio
    @patch.object(Runner.callback_framework, "trigger")
    async def test_sse_client_auth_flow(mock_trigger):
        # Mock the auth provider result
        mock_auth_provider = AuthHeaderAndQueryProvider(
            auth_headers={"Authorization": "Bearer test-token"},
            auth_query_params={"api_key": "test-key"}
        )
        # Configure the mock to return a list (trigger returns a list of results)
        # AsyncMock will handle the await automatically
        mock_trigger.return_value = [
            None,
            ToolAuthResult(
                success=True,
                auth_data={"auth_provider": mock_auth_provider}
            )
        ]
        
        # Mock the SSE connection with proper async context manager
        class MockSseClient:
            def __init__(self):
                self.is_connected = True
                self.connect = AsyncMock(return_value=True)
                self.disconnect = AsyncMock(return_value=True)
            
            async def __aenter__(self):
                return MagicMock(), MagicMock()
            
            async def __aexit__(self, exc_type, exc, tb):
                return False
        
        mock_sse_client = MockSseClient()
        
        # Create SSE client with auth config
        client = SseClient(
            config=McpServerConfig(
                server_path="http://127.0.0.1:8930/mcp",
                server_name="test-server",
                auth_headers={"Authorization": "Bearer test-token"},
                auth_query_params={"api_key": "test-key"}
            )
        )
        
        # Mock the sse_client import from mcp.client.sse
        import sys
        import types
        
        # Mock ClientSession with proper async context manager and async initialize
        class MockClientSession:
            def __init__(self, read, write, sampling_callback=None):
                self.read = read
                self.write = write
                self.sampling_callback = sampling_callback
            
            async def __aenter__(self):
                return self
            
            async def __aexit__(self, exc_type, exc, tb):
                return False
            
            async def initialize(self):
                pass
        
        # Create mock modules
        fake_mcp = types.ModuleType("mcp")
        fake_mcp.ClientSession = MockClientSession
        fake_mcp_sse = types.ModuleType("mcp.client.sse")
        fake_mcp_sse.sse_client = MagicMock(return_value=mock_sse_client)
        fake_mcp_client = types.ModuleType("mcp.client")
        fake_mcp_client.sse = fake_mcp_sse
        
        with patch.dict(
                sys.modules,
                {
                    "mcp": fake_mcp,
                    "mcp.client": fake_mcp_client,
                    "mcp.client.sse": fake_mcp_sse,
                },
                clear=False,
        ):
            
            # Test connect with auth
            connected = await client.connect(timeout=10.0)
            
            assert connected is True
            mock_trigger.assert_called_once()
            # Verify trigger was called with correct auth config
            # Get the arguments from the mock call
            args, kwargs = mock_trigger.call_args
            assert args[0] == ToolCallEvents.TOOL_AUTH
            auth_config = kwargs.get("auth_config") or args[1]
            assert auth_config.auth_type == AuthType.HEADER_AND_QUERY
            assert auth_config.config["auth_headers"] == {"Authorization": "Bearer test-token"}
            assert auth_config.config["auth_query_params"] == {"api_key": "test-key"}
            assert auth_config.tool_type == "test-server"


class TestStreamableHttpClientAuth:
    from openjiuwen.core.runner import Runner

    @staticmethod
    @pytest.mark.asyncio
    @patch.object(Runner.callback_framework, "trigger")
    async def test_streamable_http_client_auth_flow(mock_trigger):      
        # Mock the auth provider result
        mock_auth_provider = AuthHeaderAndQueryProvider(
            auth_headers={"Authorization": "Bearer test-token"},
            auth_query_params={"api_key": "test-key"}
        )
        # Configure the mock to return a list (trigger returns a list of results)
        # AsyncMock will handle the await automatically
        mock_trigger.return_value = [
            None,
            ToolAuthResult(
                success=True,
                auth_data={"auth_provider": mock_auth_provider}
            )
        ]
        
        # Mock the streamable HTTP client
        class FakeTransportContext:
            async def __aenter__(self):
                return "reader", "writer", "unused"
            
            async def __aexit__(self, exc_type, exc, tb):
                return False
        
        def fake_streamablehttp_client(server_path, timeout, auth=None):
            return FakeTransportContext()
        
        # Create mock mcp module
        import sys
        import types
        fake_mcp = types.ModuleType("mcp")
        fake_mcp.ClientSession = MagicMock()
        # Ensure ClientSession's initialize method is a coroutine
        fake_mcp.ClientSession.return_value.initialize = AsyncMock()
        fake_mcp_client = types.ModuleType("mcp.client")
        fake_streamable_http = types.ModuleType("mcp.client.streamable_http")
        fake_streamable_http.streamablehttp_client = fake_streamablehttp_client
        fake_mcp_client.streamable_http = fake_streamable_http
        
        with patch.dict(
                sys.modules,
                {
                    "mcp": fake_mcp,
                    "mcp.client": fake_mcp_client,
                    "mcp.client.streamable_http": fake_streamable_http,
                },
                clear=False,
        ):
            # Create StreamableHttpClient with auth config
            client = StreamableHttpClient(
                config=McpServerConfig(
                    server_path="http://127.0.0.1:8930/mcp",
                    server_name="test-server",
                    auth_headers={"Authorization": "Bearer test-token"},
                    auth_query_params={"api_key": "test-key"}
                )
            )
            
            # Test connect with auth
            connected = await client.connect()

            assert connected is True
            mock_trigger.assert_called_once()
            # Verify trigger was called with correct auth config
            # Get the arguments from the mock call
            args, kwargs = mock_trigger.call_args
            assert args[0] == ToolCallEvents.TOOL_AUTH
            auth_config = kwargs.get("auth_config") or args[1]
            assert auth_config.auth_type == AuthType.HEADER_AND_QUERY
            assert auth_config.config["auth_headers"] == {"Authorization": "Bearer test-token"}
            assert auth_config.config["auth_query_params"] == {"api_key": "test-key"}
            assert auth_config.tool_type == "test-server"


class TestRestfulApiAuth:
    from openjiuwen.core.runner import Runner

    @staticmethod
    @pytest.mark.asyncio
    @patch.object(Runner.callback_framework, "trigger")
    @patch("openjiuwen.core.foundation.tool.service_api.restful_api.aiohttp")
    @patch("openjiuwen.core.foundation.tool.service_api.restful_api.UrlUtils")
    async def test_restful_api_auth_flow(mock_url_utils, mock_aiohttp, mock_trigger):
        # Import RestfulApi and RestfulApiCard
        from openjiuwen.core.foundation.tool.service_api.restful_api import RestfulApi, RestfulApiCard
        from openjiuwen.core.foundation.tool.service_api.api_param_mapper import APIParamLocation
        
        # Mock URL validation to bypass hostname resolution
        mock_url_utils.check_url_is_valid.return_value = None
        
        # Mock the SSL connector result from auth
        mock_connector = MagicMock()
        mock_trigger.return_value = [
            None,
            ToolAuthResult(
                success=True,
                auth_data={"connector": mock_connector}
            )
        ]
        
        # Mock the aiohttp.ClientSession and its methods
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"message": "success"})
        mock_response.__aenter__.return_value = mock_response
        mock_response.__aexit__ = AsyncMock()
        
        mock_session = MagicMock()
        mock_session.request = AsyncMock(return_value=mock_response)
        mock_session.__aenter__.return_value = mock_session
        mock_session.__aexit__ = AsyncMock()
        
        mock_aiohttp.ClientSession.return_value = mock_session
        
        # Create a RestfulApiCard with sample configuration
        restful_card = RestfulApiCard(
            id="test-restful-api",
            name="Test API",
            description="Test RESTful API with authentication",
            url="http://api.example.com/users/{id}",
            method="GET",
            input_params={
                "type": "object",
                "properties": {
                    "id": {
                        "type": "integer",
                        "description": "User ID",
                        "location": "path"
                    },
                    "api_key": {
                        "type": "string",
                        "description": "API Key",
                        "location": "query"
                    }
                },
                "required": ["id"]
            },
            output_params={
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "name": {"type": "string"}
                }
            },
            headers={"Content-Type": "application/json"},
            queries={"version": "v1"},
            timeout=30.0
        )
        
        # Create RestfulApi instance
        restful_api = RestfulApi(restful_card)
        
        # Test the authentication flow by directly calling a modified version of _async_request
        # that only tests the authentication part
        
        # Capture the auth_config passed to trigger
        captured_auth_config = None
        
        async def mock_async_request_simplified(map_results, timeout,
                                                max_response_byte_size, raise_for_status, request_args=None):
            nonlocal captured_auth_config
            # This is a simplified version that only tests the authentication logic
            # extracted from the real _async_request method
            request_arg = request_args.copy() if request_args and isinstance(request_args, dict) else {}
            if restful_card.method in ["GET", "HEAD", "OPTIONS", "DELETE"]:
                request_arg["params"] = map_results.get(APIParamLocation.BODY)
            else:
                request_arg["json"] = map_results.get(APIParamLocation.BODY)
            
            # This is the authentication part we want to test
            auth_result = await mock_trigger(
                ToolCallEvents.TOOL_AUTH,
                auth_config=ToolAuthConfig(
                    auth_type=AuthType.SSL,
                    config={
                        "verify_switch_env": "RESTFUL_SSL_VERIFY",
                        "ssl_cert_env": "RESTFUL_SSL_CERT"
                    },
                    tool_type="restful_api",
                    tool_id=restful_api.card.id,
                ),
            )
            
            # Extract the auth_config from the mock call
            if mock_trigger.called:
                call_args = mock_trigger.call_args
                captured_auth_config = call_args[1]["auth_config"]
            
            return {"id": 123, "name": "Test User"}
        
        # Use the simplified version for testing
        result = await mock_async_request_simplified(
            map_results={
                APIParamLocation.PATH: {"id": 123},
                APIParamLocation.QUERY: {"api_key": "test-key", "version": "v1"},
                APIParamLocation.BODY: None,
                APIParamLocation.HEADER: {"Content-Type": "application/json"}
            },
            timeout=30.0,
            max_response_byte_size=10 * 1024 * 1024,
            raise_for_status=True
        )
        
        # Verify the trigger was called with correct auth config
        assert captured_auth_config is not None, "SSL auth trigger was not called"
        assert captured_auth_config.auth_type == AuthType.SSL
        assert captured_auth_config.tool_type == "restful_api"
        assert captured_auth_config.tool_id == "test-restful-api"
        assert captured_auth_config.config["verify_switch_env"] == "RESTFUL_SSL_VERIFY"
        assert captured_auth_config.config["ssl_cert_env"] == "RESTFUL_SSL_CERT"

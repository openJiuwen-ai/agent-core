# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from unittest.mock import patch, AsyncMock, MagicMock

import httpx
import pytest

from openjiuwen.core.foundation.tool.auth.auth_callback import AuthHeaderAndQueryProvider
from openjiuwen.core.foundation.tool import McpServerConfig
from openjiuwen.core.foundation.tool.mcp.client.sse_client import SseClient
from openjiuwen.core.foundation.tool.mcp.client.streamable_http_client import StreamableHttpClient
from openjiuwen.core.foundation.tool.service_api.restful_api import RestfulApi, RestfulApiCard
from openjiuwen.core.common.exception.errors import BaseError


class TestToolValidation:
    
    @staticmethod
    @pytest.mark.asyncio
    async def test_sse_client_auth_validation():
        """Test SseClient authentication validation functionality"""
        # Pre-set data
        auth_headers = {"Authorization": "Bearer test_token", "X-Custom-Header": "test_value"}
        auth_query_params = {"api_key": "test_key", "version": "v1"}
        server_config = McpServerConfig(
            server_name="test-sse-server",
            server_path="http://127.0.0.1:8080/sse",
            client_type="sse",
            auth_headers=auth_headers,
            auth_query_params=auth_query_params,
            server_id="test-sse-server-id"
        )
        
        # Mock configuration
        from openjiuwen.core.runner import Runner
        with (patch.object(Runner.callback_framework, "trigger") as mock_trigger,
              patch("mcp.client.sse.sse_client") as mock_sse_client_class,
              patch("mcp.ClientSession") as mock_client_session):
            
            # Configure mock_trigger return value
            mock_auth_provider = AuthHeaderAndQueryProvider(auth_headers, auth_query_params)
            mock_trigger.return_value = [None, MagicMock(auth_data={"auth_provider": mock_auth_provider})]
            
            # Configure mock_sse_client
            mock_read, mock_write = AsyncMock(), AsyncMock()
            mock_sse_client_instance = MagicMock()
            mock_sse_client_instance.__aenter__.return_value = (mock_read, mock_write)
            mock_sse_client_class.return_value = mock_sse_client_instance
            
            # Configure mock_client_session
            mock_session_instance = MagicMock()
            mock_session_instance.initialize = AsyncMock()
            mock_client_session.return_value.__aenter__.return_value = mock_session_instance
            
            # Create SseClient and connect
            sse_client = SseClient(server_config)
            result = await sse_client.connect()
            
            # Verify results
            assert result is True
            mock_trigger.assert_called_once()
            mock_sse_client_class.assert_called_once_with(
                "http://127.0.0.1:8080/sse",
                timeout=60.0,
                auth=mock_auth_provider
            )
            mock_session_instance.initialize.assert_awaited_once()
    
    @staticmethod
    @pytest.mark.asyncio
    async def test_streamable_http_client_auth_validation():
        """Test StreamableHttpClient authentication validation functionality"""
        # Pre-set data
        auth_headers = {"Authorization": "Bearer test_token", "X-Custom-Header": "test_value"}
        auth_query_params = {"api_key": "test_key", "version": "v1"}
        server_config = McpServerConfig(
            server_name="test-streamable-server",
            server_path="http://127.0.0.1:8080/streamable",
            client_type="streamable-http",
            auth_headers=auth_headers,
            auth_query_params=auth_query_params,
            server_id="test-streamable-server-id"
        )
        
        # Mock configuration
        from openjiuwen.core.runner import Runner
        with (patch.object(Runner.callback_framework, "trigger") as mock_trigger,
              patch("mcp.client.streamable_http.streamablehttp_client") as mock_streamable_client_class,
              patch("mcp.ClientSession") as mock_client_session):
            
            # Configure mock_trigger return value
            mock_auth_provider = AuthHeaderAndQueryProvider(auth_headers, auth_query_params)
            mock_trigger.return_value = [None, MagicMock(auth_data={"auth_provider": mock_auth_provider})]
            
            # Configure mock_streamable_client
            mock_read, mock_write = AsyncMock(), AsyncMock()
            mock_streamable_client_instance = MagicMock()
            mock_streamable_client_instance.__aenter__.return_value = (mock_read, mock_write, None)
            mock_streamable_client_class.return_value = mock_streamable_client_instance
            
            # Configure mock_client_session
            mock_session_instance = MagicMock()
            mock_session_instance.initialize = AsyncMock()
            mock_client_session.return_value.__aenter__.return_value = mock_session_instance
            
            # Create StreamableHttpClient and connect
            streamable_client = StreamableHttpClient(server_config)
            result = await streamable_client.connect()
            
            # Verify results
            assert result is True
            mock_trigger.assert_called_once()
            mock_streamable_client_class.assert_called_once_with(
                "http://127.0.0.1:8080/streamable",
                timeout=60.0,
                auth=mock_auth_provider
            )
            mock_session_instance.initialize.assert_awaited_once()
    
    @staticmethod
    @pytest.mark.asyncio
    async def test_restful_api_card_validation():
        """Test RestfulApiCard validation functionality"""
        # Mock URL validation function to avoid real domain resolution
        with patch("openjiuwen.core.common.security.url_utils.UrlUtils.check_url_is_valid") as mock_check_url:
            # Mock URL validation function always returns True so we can test other validation logic
            mock_check_url.return_value = True
            
            # Test valid configuration
            valid_card = RestfulApiCard(
                name="test_api",
                url="https://api.example.com/users",
                method="GET",
                headers={"Content-Type": "application/json"},
                queries={"page": 1, "limit": 10},
                timeout=30.0,
                max_response_byte_size=10 * 1024 * 1024
            )
            assert valid_card.method == "GET"
            assert valid_card.url == "https://api.example.com/users"
            
            # Test invalid HTTP method
            with pytest.raises(ValueError):
                RestfulApiCard(
                    name="test_api",
                    url="https://api.example.com/users",
                    method="INVALID_METHOD",
                    headers={"Content-Type": "application/json"}
                )
            # Reset mock to test path parameter validation
            mock_check_url.return_value = True
            
            # Test valid configuration with path parameters
            valid_card_with_path = RestfulApiCard(
                name="test_api",
                url="https://api.example.com/users/{id}",
                method="GET",
                headers={"Content-Type": "application/json"},
                input_params={
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer", "description": "User ID", "location": "path"}
                    },
                    "required": ["id"]
                }
            )
            assert valid_card_with_path.url == "https://api.example.com/users/{id}"
        
        # Test invalid URL outside of mock
        with pytest.raises(BaseError):
            RestfulApiCard(
                name="test_api",
                url="http://invalid-url-test",
                method="GET",
                headers={"Content-Type": "application/json"}
            )
            
            
    
    @staticmethod
    @pytest.mark.asyncio
    async def test_restful_api_auth_validation():
        """Test RestfulApi authentication validation functionality"""
        # Mock URL validation function to avoid real domain resolution
        with patch("openjiuwen.core.common.security.url_utils.UrlUtils.check_url_is_valid"):
            # Pre-set data
            card = RestfulApiCard(
                name="test_api",
                url="https://api.example.com/users/{id}",
                method="GET",
                headers={"Content-Type": "application/json"},
                input_params={
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer", "description": "User ID", "location": "path"}
                    },
                    "required": ["id"]
                }
            )
            
            # Mock configuration
            from openjiuwen.core.runner import Runner
            with (patch.object(Runner.callback_framework, "trigger") as mock_trigger,
                  patch("openjiuwen.core.foundation.tool.service_api.restful_api.aiohttp.ClientSession") as mock_client_session,
                  patch("openjiuwen.core.foundation.tool.service_api.restful_api.RestfulApi._format_response") as mock_format_response):
                
                # Configure mock_trigger return value
                mock_connector = MagicMock()
                mock_trigger.return_value = [MagicMock(auth_data={"connector": mock_connector}), None]
                
                # Configure mock_response
                mock_response = MagicMock()
                mock_response.status = 200
                mock_response.headers = {"Content-Type": "application/json"}
                mock_response.raise_for_status = MagicMock()
                mock_response.reason = "OK"
                mock_response.url = MagicMock()
                mock_response.url.__str__.return_value = "https://api.example.com/users/1"

                # Create a mock async context manager object to wrap the response
                mock_context_manager = MagicMock()
                mock_context_manager.__aenter__ = AsyncMock(return_value=mock_response)
                mock_context_manager.__aexit__ = AsyncMock()
                
                # Configure mock_session_instance
                mock_session_instance = MagicMock()
                
                # Let session.request return mock_context_manager instead of mock_response directly
                mock_session_instance.request = MagicMock(return_value=mock_context_manager)
                mock_client_session.return_value.__aenter__.return_value = mock_session_instance
                
                # Mock _format_response method to return expected result
                expected_result = {
                    "code": 200,
                    "data": {"id": 1, "name": "test"},
                    "headers": {"Content-Type": "application/json"}
                }
                mock_format_response.return_value = expected_result
                
                # Create RestfulApi and invoke
                restful_api = RestfulApi(card)
                result = await restful_api.invoke({"id": 1})
                
                # Verify results
                assert result is not None
                assert result["code"] == 200
                assert result["data"]["id"] == 1
                mock_session_instance.request.assert_called_once()
                mock_format_response.assert_called_once()
    
    @staticmethod
    @pytest.mark.asyncio
    async def test_auth_header_and_query_provider():
        """Test AuthHeaderAndQueryProvider class"""
        # Pre-set data
        auth_headers = {"Authorization": "Bearer test_token", "X-Custom-Header": "test_value"}
        auth_query_params = {"api_key": "test_key", "version": "v1"}
        
        # Create AuthHeaderAndQueryProvider instance
        auth_provider = AuthHeaderAndQueryProvider(auth_headers, auth_query_params)
        
        # Create mock request
        mock_request = httpx.Request("GET", "https://api.example.com/users")
        
        # Call async_auth_flow
        flow = auth_provider.async_auth_flow(mock_request)
        modified_request = await flow.__anext__()
        
        # Verify modified request
        for key, value in auth_headers.items():
            assert modified_request.headers[key] == value
        
        # Verify query parameters
        assert "api_key=test_key" in str(modified_request.url)
        assert "version=v1" in str(modified_request.url)
    
    @staticmethod
    @pytest.mark.asyncio
    async def test_restful_api_parameter_mapping():
        """Test RestfulApi parameter mapping functionality"""
        # Mock URL validation function to avoid real domain resolution
        with patch("openjiuwen.core.common.security.url_utils.UrlUtils.check_url_is_valid"):
            # Pre-set data
            card = RestfulApiCard(
                name="test_api",
                url="https://api.example.com/users/{id}/posts/{post_id}",
                method="GET",
                headers={"Content-Type": "application/json"},
                queries={"default_param": "default_value"},
                input_params={
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer", "description": "User ID", "location": "path"},
                        "post_id": {"type": "integer", "description": "Post ID", "location": "path"},
                        "page": {"type": "integer", "description": "Page number", "location": "query"},
                        "limit": {"type": "integer", "description": "Items per page", "location": "query"},
                        "custom_header": {"type": "string", "description": "Custom header", "location": "header"}
                    },
                    "required": ["id", "post_id"]
                }
            )
            
            # Mock configuration
            from openjiuwen.core.runner import Runner
            with (patch.object(Runner.callback_framework, "trigger") as mock_trigger,
                  patch("openjiuwen.core.foundation.tool.service_api.restful_api.aiohttp.ClientSession") as mock_client_session,
                  patch("openjiuwen.core.foundation.tool.service_api.restful_api.RestfulApi._format_response") as mock_format_response):
                
                # Configure mock_trigger return value
                mock_connector = MagicMock()
                mock_trigger.return_value = [MagicMock(auth_data={"connector": mock_connector}), None]
                
                # Configure mock_response
                mock_response = MagicMock()
                mock_response.status = 200
                mock_response.headers = {"Content-Type": "application/json"}
                mock_response.raise_for_status = MagicMock()
                mock_response.reason = "OK"
                mock_response.url = MagicMock()
                mock_response.url.__str__.return_value = "https://api.example.com/users/1/posts/10?default_param=default_value&page=1&limit=10"

                # Create a mock async context manager object to wrap the response
                mock_context_manager = MagicMock()
                mock_context_manager.__aenter__ = AsyncMock(return_value=mock_response)
                mock_context_manager.__aexit__ = AsyncMock()
                
                # Configure mock_session_instance
                mock_session_instance = MagicMock()
                
                # Let session.request return mock_context_manager instead of mock_response directly
                mock_session_instance.request = MagicMock(return_value=mock_context_manager)
                mock_client_session.return_value.__aenter__.return_value = mock_session_instance
                
                # Mock _format_response method to return expected result
                expected_result = {
                    "code": 200,
                    "data": [],
                    "headers": {"Content-Type": "application/json"}
                }
                mock_format_response.return_value = expected_result
                
                # Create RestfulApi and invoke
                restful_api = RestfulApi(card)
                result = await restful_api.invoke({
                    "id": 1,
                    "post_id": 10,
                    "page": 1,
                    "limit": 10,
                    "custom_header": "test_header_value"
                })
                
                # Verify results
                assert result is not None
                mock_session_instance.request.assert_called_once()
                call_args = mock_session_instance.request.call_args
                
                # Verify path parameters are replaced
                assert "https://api.example.com/users/1/posts/10" in call_args[0][1]
                
                # Verify headers contain custom header
                headers = call_args[1]["headers"]
                assert headers["Content-Type"] == "application/json"
                assert headers["custom_header"] == "test_header_value"

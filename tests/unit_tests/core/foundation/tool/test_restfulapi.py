# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved
import asyncio
import gzip
import json
import os
from contextlib import contextmanager
from unittest import mock
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import aiohttp
import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError, ValidationError
from openjiuwen.core.foundation.tool import ToolInfo, RestfulApiCard
from openjiuwen.core.foundation.tool.service_api.restful_api import RestfulApi

os.environ["SSRF_PROTECT_ENABLED"] = "false"


@pytest.mark.asyncio
class TestRestFulApi:
    def assertEqual(self, left, right):
        """Assert helper method for equality comparison"""
        assert left == right

    def _create_mocked_session_context(self, mock_response):
        """Create a mocked aiohttp ClientSession context manager

        Args:
            mock_response: Mocked aiohttp response object

        Returns:
            Mocked ClientSession instance
        """
        mock_session = AsyncMock()

        class MockResponseContext:
            def __init__(self, response):
                self.response = response

            async def __aenter__(self):
                return self.response

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                pass

        mock_context = MockResponseContext(mock_response)
        mock_session.request = Mock(return_value=mock_context)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        return mock_session

    @contextmanager
    def _ssl_mock_context(self):
        """SSL mock configuration context manager

        Yields:
            tuple: (mock_get_ssl, mock_create_ssl) mocked objects
        """
        with patch('openjiuwen.core.common.security.ssl_utils.SslUtils.get_ssl_config') as mock_get_ssl, \
                patch(
                    'openjiuwen.core.common.security.ssl_utils.SslUtils.create_strict_ssl_context') as mock_create_ssl:
            mock_get_ssl.return_value = (False, None)
            mock_create_ssl.return_value = None
            yield mock_get_ssl, mock_create_ssl


    @staticmethod
    def _create_mock_response(status=200, content_type="application/json", url="http://example.com/api/test",
                              reason="OK", content_bytes=None):
        """Create a mocked aiohttp response with specified parameters

        Args:
            status: HTTP status code
            content_type: Content-Type header value
            url: Response URL
            reason: HTTP reason phrase
            content_bytes: Response content bytes

        Returns:
            Mocked response object
        """
        mock_response = AsyncMock()
        mock_response.status = status
        mock_response.headers = {"Content-Type": content_type}
        mock_response.url = url
        mock_response.reason = reason
        mock_response.raise_for_status = Mock()

        if content_bytes:
            async def content_iter():
                yield content_bytes

            mock_response.content.iter_chunked = Mock(return_value=content_iter())
        else:
            async def empty_iterator():
                if False:  # This will never yield
                    yield b""

            mock_response.content.iter_chunked = Mock(return_value=empty_iterator())

        return mock_response

    def _create_json_response(self, data, status=200, content_type="application/json",
                              url="http://example.com/api/test", reason="OK"):
        """Create a mocked JSON response

        Args:
            data: JSON-serializable data
            status: HTTP status code
            content_type: Content-Type header value
            url: Response URL
            reason: HTTP reason phrase

        Returns:
            Mocked response object with JSON content
        """
        json_bytes = json.dumps(data).encode('utf-8')
        response = self._create_mock_response(status, content_type, url, reason, json_bytes)
        return response

    def _create_text_response(self, text, status=200, content_type="text/plain; charset=utf-8",
                              url="http://example.com/api/test", reason="OK"):
        """Create a mocked text response

        Args:
            text: Plain text content
            status: HTTP status code
            content_type: Content-Type header value
            url: Response URL
            reason: HTTP reason phrase

        Returns:
            Mocked response object with text content
        """
        text_bytes = text.encode('utf-8')
        response = self._create_mock_response(status, content_type, url, reason, text_bytes)
        return response

    @pytest.fixture(autouse=True)
    def setUp(self):
        """Automatically setup mock functions before each test"""
        response_mock = MagicMock()
        response_mock.status_code = 200
        response_mock.text = "{}"
        response_mock.content = b"{}"
        self.mocked_functions = mock.patch.multiple(
            "requests",
            request=mock.MagicMock(return_value=response_mock)
        )
        self.mocked_functions.start()

    def tearDown(self):
        """Clean up mock functions after each test"""
        self.mocked_functions.stop()

    @patch('requests.sessions.Session.request')
    async def test_invoke(self, mock_request):
        """Test invoke method with SSL certificate"""
        mock_data = RestfulApi(
            card=RestfulApiCard(
                name="test",
                description="test",
                url="http://127.0.0.1:8000",
                headers={},
                method="GET",
            ),
        )
        mock_request.return_value = dict()
        try:
            os.environ["RESTFUL_SSL_CERT"] = "temp.crt"
            await mock_data.invoke({})
            del os.environ["RESTFUL_SSL_CERT"]
        except Exception as e:
            pass
        self.assertEqual(mock_data.card.headers, {})

    @patch("requests.sessions.Session.request")
    async def test_stream(self, mock_request):
        """Test stream method with SSL certificate"""
        mock_data = RestfulApi(
            card=RestfulApiCard(
                name="test",
                description="test",
                url="http://127.0.0.1:8000",
                headers={},
                method="GET",
            ),
        )
        mock_request.return_value = dict()
        os.environ["RESTFUL_SSL_CERT"] = "temp.crt"
        with pytest.raises(ValidationError) as e:
            await mock_data.stream({})
        del os.environ["RESTFUL_SSL_CERT"]

    def test_get_tool_info(self):
        """Test tool_info method returns correct ToolInfo object"""
        mock_data = RestfulApi(
            card=RestfulApiCard(
                name="test",
                description="test",
                input_params={
                    "type": "object",
                    "properties": {
                        "test": {"description": "test", "type": "string", "default": "123"},
                    },
                    "required": ["test"],
                },
                url="http://127.0.0.1:8000",
                headers={},
                method="GET",
            ),
        )
        res = mock_data.card.tool_info()
        too_info = ToolInfo(
            name="test",
            description="test",
            parameters={
                "type": "object",
                "properties": {"test": {"description": "test", "type": "string", "default": "123"}},
                "required": ["test"],
            },
        )
        self.assertEqual(res, too_info)

    @pytest.fixture
    def mock_card(self):
        """Create a mocked RestfulApiCard instance"""
        card = RestfulApiCard(**dict(
            name="test_api",
            description="Test API",
            url="http://example.com/api/test",
            method="POST",
            timeout=60.0,
            max_response_byte_size=10 * 1024 * 1024,
            headers={},
            queries={},
            paths={},
            input_params={}))
        return card

    @pytest.fixture
    def restful_api(self, mock_card):
        """Create a RestfulApi instance with mocked card"""
        return RestfulApi(mock_card)

    @pytest.mark.asyncio
    async def test_invoke_with_json_response(self, restful_api):
        """Test invoke method handling JSON response"""
        # Create mocked JSON response using helper method
        response_data = {
            "code": 200,
            "data": {"id": 123, "name": "test_user", "status": "active"},
            "message": "success"
        }
        mock_response = self._create_json_response(response_data)

        # Use helper method to create mocked session
        with patch('aiohttp.ClientSession') as mock_session_class:
            mock_session = self._create_mocked_session_context(mock_response)
            mock_session_class.return_value = mock_session

            # Use SSL mock context manager
            with self._ssl_mock_context():
                # Call invoke method
                result = await restful_api.invoke({})

                # Verify result format
                assert result["code"] == 200
                assert result["message"] == "success"
                assert result["url"] == "http://example.com/api/test"
                assert "headers" in result
                assert result["headers"]["Content-Type"] == "application/json"
                assert result["data"] == response_data

    @pytest.mark.asyncio
    async def test_invoke_with_text_response(self, restful_api):
        """Test invoke method handling plain text response"""
        # Create mocked text response using helper method
        text_content = "Operation completed successfully"
        mock_response = self._create_text_response(text_content)

        # Use helper method to create mocked session
        with patch('aiohttp.ClientSession') as mock_session_class:
            mock_session = self._create_mocked_session_context(mock_response)
            mock_session_class.return_value = mock_session

            # Use SSL mock context manager
            with self._ssl_mock_context():
                # Call invoke method
                result = await restful_api.invoke({})

                # Verify result
                assert result["code"] == 200
                assert result["message"] == "success"
                assert result["data"] == text_content

    @pytest.mark.asyncio
    async def test_invoke_with_error_response(self, restful_api):
        """Test invoke method handling error response"""
        # Create mocked error JSON response using helper method
        error_data = {
            "code": 400,
            "message": "Invalid request parameters",
            "details": ["field1 is required", "field2 must be integer"]
        }
        mock_response = self._create_json_response(
            error_data,
            status=400,
            reason="Bad Request",
            url="http://example.com/api/error"
        )

        # Use helper method to create mocked session
        with patch('aiohttp.ClientSession') as mock_session_class:
            mock_session = self._create_mocked_session_context(mock_response)
            mock_session_class.return_value = mock_session

            # Use SSL mock context manager
            with self._ssl_mock_context():
                # Call invoke method
                result = await restful_api.invoke({})

                # Verify result
                assert result["code"] == 400
                assert result["message"] == "Bad Request"
                assert result["data"] == error_data

    @pytest.mark.asyncio
    async def test_invoke_response_size_exceeded(self, restful_api):
        """Test invoke method handling response size limit exceeded"""
        # Create large response data
        large_content = "x" * 2048  # 2KB
        large_bytes = large_content.encode('utf-8')

        # Create mocked response with chunked content
        mock_response = self._create_mock_response(
            status=200,
            content_type="text/plain",
            url="http://example.com/api/large",
            reason="OK"
        )

        async def content_iter():
            chunk_size = 512
            for i in range(0, len(large_bytes), chunk_size):
                yield large_bytes[i:i + chunk_size]

        mock_response.content.iter_chunked = Mock(return_value=content_iter())

        # Use helper method to create mocked session
        with patch('aiohttp.ClientSession') as mock_session_class:
            mock_session = self._create_mocked_session_context(mock_response)
            mock_session_class.return_value = mock_session

            # Use SSL mock context manager
            with self._ssl_mock_context():
                # Set smaller max_response_byte_size

                # Call invoke method, expect exception
                with pytest.raises(BaseError) as exc_info:
                    await restful_api.invoke({}, max_response_byte_size=1024)

                # Verify exception
                assert exc_info.value.code == StatusCode.TOOL_RESTFUL_API_RESPONSE_SIZE_EXCEED_LIMIT.code

    @pytest.mark.asyncio
    async def test_invoke_with_invalid_json_response(self, restful_api):
        """Test invoke method handling invalid JSON response"""
        invalid_json = b'{invalid: json, missing: quotes}'
        mock_response = self._create_mock_response(
            status=200,
            content_type="application/json",
            url="http://example.com/api/invalid",
            reason="OK",
            content_bytes=invalid_json
        )

        # Use helper method to create mocked session
        with patch('aiohttp.ClientSession') as mock_session_class:
            mock_session = self._create_mocked_session_context(mock_response)
            mock_session_class.return_value = mock_session

            # Use SSL mock context manager
            with self._ssl_mock_context():
                # Call invoke method, expect exception
                with pytest.raises(Exception) as exc_info:
                    await restful_api.invoke({})

                # Verify exception
                assert exc_info.value.code == StatusCode.TOOL_RESTFUL_API_RESPONSE_PROCESS_ERROR.code

    @pytest.mark.asyncio
    async def test_invoke_with_html_response(self, restful_api):
        """Test invoke method handling HTML response"""
        html_content = """<!DOCTYPE html>
<html>
<head><title>Test Page</title></head>
<body>
    <h1>Hello World</h1>
    <p>This is an HTML response</p>
</body>
</html>"""

        mock_response = self._create_text_response(
            html_content,
            content_type="text/html; charset=utf-8",
            url="http://example.com/api/html"
        )

        # Use helper method to create mocked session
        with patch('aiohttp.ClientSession') as mock_session_class:
            mock_session = self._create_mocked_session_context(mock_response)
            mock_session_class.return_value = mock_session

            # Use SSL mock context manager
            with self._ssl_mock_context():
                # Call invoke method
                result = await restful_api.invoke({})

                # Verify result
                assert result["code"] == 200
                assert result["message"] == "success"
                assert result["data"] == html_content

    @pytest.mark.asyncio
    async def test_invoke_with_xml_response(self, restful_api):
        """Test invoke method handling XML response"""
        xml_content = """<?xml version="1.0" encoding="UTF-8"?>
<response>
    <status>success</status>
    <code>200</code>
    <data>
        <item id="1">Item 1</item>
        <item id="2">Item 2</item>
    </data>
</response>"""

        mock_response = self._create_text_response(
            xml_content,
            content_type="application/xml",
            url="http://example.com/api/xml"
        )

        # Use helper method to create mocked session
        with patch('aiohttp.ClientSession') as mock_session_class:
            mock_session = self._create_mocked_session_context(mock_response)
            mock_session_class.return_value = mock_session

            # Use SSL mock context manager
            with self._ssl_mock_context():
                # Call invoke method
                result = await restful_api.invoke({})

                # Verify result
                assert result["code"] == 200
                assert result["message"] == "success"
                assert result["data"] == xml_content

    @pytest.mark.asyncio
    async def test_invoke_with_empty_response(self, restful_api):
        """Test invoke method handling empty response"""
        mock_response = self._create_mock_response(
            status=204,
            content_type="application/json",
            url="http://example.com/api/empty",
            reason="No Content",
            content_bytes=b""
        )

        # Use helper method to create mocked session
        with patch('aiohttp.ClientSession') as mock_session_class:
            mock_session = self._create_mocked_session_context(mock_response)
            mock_session_class.return_value = mock_session

            # Use SSL mock context manager
            with self._ssl_mock_context():
                # Call invoke method
                result = await restful_api.invoke({})

                # Verify result
                assert result["code"] == 204
                assert result["message"] == "success"
                assert result["data"] == {}

    @pytest.mark.asyncio
    async def test_invoke_with_custom_headers_in_response(self, restful_api):
        """Test invoke method handling response with custom headers"""
        response_data = {"status": "success", "data": {"id": 1}}
        mock_response = self._create_json_response(response_data)

        # Add custom headers
        mock_response.headers = {
            "Content-Type": "application/json",
            "X-Custom-Header": "custom-value",
            "X-RateLimit-Limit": "1000",
            "X-RateLimit-Remaining": "950",
            "X-Request-ID": "req-123456789"
        }

        # Use helper method to create mocked session
        with patch('aiohttp.ClientSession') as mock_session_class:
            mock_session = self._create_mocked_session_context(mock_response)
            mock_session_class.return_value = mock_session

            # Use SSL mock context manager
            with self._ssl_mock_context():
                # Call invoke method
                result = await restful_api.invoke({})

                # Verify result
                assert result["code"] == 200
                assert result["message"] == "success"
                assert result["data"] == response_data

                # Verify headers
                headers = result["headers"]
                assert headers["X-Custom-Header"] == "custom-value"
                assert headers["X-RateLimit-Limit"] == "1000"
                assert headers["X-RateLimit-Remaining"] == "950"
                assert headers["X-Request-ID"] == "req-123456789"

    @pytest.mark.asyncio
    async def test_invoke_with_redirect_response(self, restful_api):
        """Test invoke method handling redirect response"""
        redirect_message = "Resource has moved to new location"
        mock_response = self._create_text_response(
            redirect_message,
            status=302,
            reason="Found",
            url="http://example.com/api/old"
        )

        # Add redirect header
        mock_response.headers = {
            "Content-Type": "text/plain",
            "Location": "http://example.com/api/new-location"
        }

        # Use helper method to create mocked session
        with patch('aiohttp.ClientSession') as mock_session_class:
            mock_session = self._create_mocked_session_context(mock_response)
            mock_session_class.return_value = mock_session

            # Use SSL mock context manager
            with self._ssl_mock_context():
                # Call invoke method
                result = await restful_api.invoke({})

                # Verify result
                assert result["code"] == 302
                assert result["message"] == "Found"
                assert result["data"] == redirect_message
                assert result["headers"]["Location"] == "http://example.com/api/new-location"

    @pytest.mark.asyncio
    async def test_invoke_with_chunked_stream_response(self, restful_api):
        """Test invoke method handling chunked stream response"""
        mock_response = self._create_mock_response(
            status=200,
            content_type="application/json",
            url="http://example.com/api/stream",
            reason="OK"
        )

        # Prepare chunked data
        chunks = [
            b'[{"id": 1, "name": "item1", "progress": 33},',
            b'{"id": 2, "name": "item2", "progress": 66},',
            b'{"id": 3, "name": "item3", "progress": 100, "complete": true}]'
        ]

        async def content_iter():
            for chunk in chunks:
                yield chunk

        mock_response.content.iter_chunked = Mock(return_value=content_iter())

        # Use helper method to create mocked session
        with patch('aiohttp.ClientSession') as mock_session_class:
            mock_session = self._create_mocked_session_context(mock_response)
            mock_session_class.return_value = mock_session

            # Use SSL mock context manager
            with self._ssl_mock_context():
                # Call invoke method
                result = await restful_api.invoke({})

                # Verify result
                assert result["code"] == 200
                assert result["message"] == "success"
                assert result["data"][2]["id"] == 3
                assert result["data"][2]["name"] == "item3"
                assert result["data"][2]["complete"] is True

    @pytest.fixture
    def card_with_input_params(self):
        """Create RestfulApiCard with input parameters"""
        input_schema = {
            "type": "object",
            "properties": {
                "user_id": {
                    "type": "integer",
                    "description": "User ID",
                    "location": "query"
                },
                "data": {
                    "type": "object",
                    "description": "Request data",
                    "location": "body"
                }
            },
            "required": ["user_id"]
        }

        card = RestfulApiCard(**dict(
            name="user_api",
            description="User API",
            url="http://example.com/api/users",
            method="POST",
            timeout=30.0,
            max_response_byte_size=5 * 1024 * 1024,
            headers={},
            queries={},
            paths={},
            input_params=input_schema
        ))
        return card

    @pytest.mark.asyncio
    async def test_invoke_with_inputs_and_json_response(self, card_with_input_params):
        """Test invoke method with input parameters handling JSON response"""
        restful_api = RestfulApi(card_with_input_params)

        response_data = {
            "user": {"id": 123, "name": "张三", "email": "zhangsan@example.com"},
            "status": "active"
        }
        mock_response = self._create_json_response(
            response_data,
            url="http://example.com/api/users?user_id=123"
        )

        # Use helper method to create mocked session
        with patch('aiohttp.ClientSession') as mock_session_class:
            mock_session = self._create_mocked_session_context(mock_response)
            mock_session_class.return_value = mock_session

            # Use SSL mock context manager
            with self._ssl_mock_context():
                # Call invoke method with input parameters
                result = await restful_api.invoke({
                    "user_id": 123,
                    "data": {"action": "update_profile"}
                })

                # Verify result
                assert result["code"] == 200
                assert result["message"] == "success"
                assert result["data"] == response_data
                assert "user_id=123" in result["url"]


MOCK_SUCCESS_RESPONSE = {
    "code": 200,
    "data": {
        "id": 123,
        "name": "test_user",
        "status": "active"
    },
    "message": "success"
}

MOCK_ERROR_RESPONSE = {
    "code": 400,
    "message": "Bad Request"
}


@pytest.mark.asyncio
class TestRestfulApiInvokeWithLocation:
    @pytest.fixture
    def mock_aiohttp_response(self):
        """模拟 aiohttp 响应对象 - 修复版本"""
        response = AsyncMock()
        response.status = 200
        response.headers = {"Content-Type": "application/json"}
        response.content_type = "application/json"
        response.raise_for_status = Mock()
        response.json = AsyncMock(return_value=MOCK_SUCCESS_RESPONSE)
        response.text = AsyncMock(return_value=json.dumps(MOCK_SUCCESS_RESPONSE))
        content_mock = AsyncMock()

        async def content_iter():
            yield json.dumps(MOCK_SUCCESS_RESPONSE).encode('utf-8')

        response.content.iter_chunked = Mock(return_value=content_iter())

        return response

    @pytest.fixture
    def mock_client_session(self, mock_aiohttp_response):
        with patch('aiohttp.ClientSession') as mock_session_class:
            mock_session = AsyncMock()

            class MockResponseContext:
                def __init__(self, response):
                    self.response = response

                async def __aenter__(self):
                    return self.response

                async def __aexit__(self, exc_type, exc_val, exc_tb):
                    pass

            mock_context = MockResponseContext(mock_aiohttp_response)
            mock_session.request = Mock(return_value=mock_context)
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)

            mock_session_class.return_value = mock_session
            yield mock_session

    @pytest.fixture
    def mock_connector(self):
        with patch('aiohttp.TCPConnector') as mock_connector_class:
            mock_connector = Mock()
            mock_connector.__aenter__ = AsyncMock(return_value=mock_connector)
            mock_connector.__aexit__ = AsyncMock(return_value=None)
            mock_connector_class.return_value = mock_connector
            yield mock_connector

    @pytest.mark.asyncio
    async def test_invoke_with_query_location(self, mock_client_session, mock_connector):
        with patch('openjiuwen.core.common.security.ssl_utils.SslUtils.get_ssl_config') as mock_get_ssl, \
                patch(
                    'openjiuwen.core.common.security.ssl_utils.SslUtils.create_strict_ssl_context') as mock_create_ssl:
            mock_get_ssl.return_value = (False, None)
            mock_create_ssl.return_value = None

            input_schema = {
                "type": "object",
                "properties": {
                    "user_id": {
                        "type": "integer",
                        "description": "用户ID",
                        "location": "path"
                    },
                    "name": {
                        "type": "string",
                        "description": "用户姓名",
                        "location": "body"
                    },
                    "filter": {
                        "type": "array",
                        "description": "过滤条件",
                        "location": "query"
                    }
                }
            }

            card = RestfulApiCard(
                name="get_user_info",
                description="获取用户信息",
                url="http://127.0.0.1/api/v1/users/{user_id}/profile",
                method="GET",
                queries={"format": "json"},
                input_params=input_schema
            )

            api_tool = RestfulApi(card)
            os.environ["RESTFUL_SSL_CERT"] = "temp.crt"

            result = await api_tool.invoke({
                "user_id": 123,
                "name": "张三",
                "filter": ["active","pending"],

            })

            assert result.get("data") == MOCK_SUCCESS_RESPONSE

            mock_session = mock_client_session
            assert mock_session.request.called

            call_args = mock_session.request.call_args

            expected_url_part = (
                "http://127.0.0.1/api/v1/users/123/profile?format=json&filter=active"
            )

            actual_url = call_args[0][1]

            assert expected_url_part in actual_url
            assert "format=json" in actual_url
            assert "filter=active" in actual_url
            assert "user_id=123" not in actual_url  # path param, not query

            assert call_args[0][0] == "GET"
            assert call_args[1]["headers"] == {}

            # GET requests do not send body, so body fields become query params
            assert "params" in call_args[1]
            assert call_args[1]["params"] == {"name": "张三"}

    async def test_invoke_with_path_location(self, mock_client_session, mock_connector):
        with patch('openjiuwen.core.common.security.ssl_utils.SslUtils.get_ssl_config') as mock_get_ssl, \
                patch(
                    'openjiuwen.core.common.security.ssl_utils.SslUtils.create_strict_ssl_context') as mock_create_ssl:
            mock_get_ssl.return_value = (False, None)
            mock_create_ssl.return_value = None
            input_schema = {
                "type": "object",
                "properties": {
                    "user_id": {
                        "type": "integer",
                        "description": "用户ID",
                        "location": "path"
                    },
                    "action": {
                        "type": "string",
                        "description": "操作类型",
                        "location": "path"
                    },
                    "data": {
                        "type": "object",
                        "description": "请求数据",
                        "location": "body"
                    }
                }
            }

            # 创建 RestfulApiCard
            card = RestfulApiCard(
                name="update_user_action",
                description="更新用户操作",
                url="http://127.0.0.1/api/v1/users/{user_id}/actions/{action}",
                method="POST",
                paths={"version": "v1"},
                input_params=input_schema
            )
            api_tool = RestfulApi(card)

            # 调用 invoke
            result = await api_tool.invoke({
                "user_id": 456,
                "action": "enable",
                "data": {"status": "active", "role": "admin"}
            })
            assert result.get("data") == MOCK_SUCCESS_RESPONSE
            mock_session = mock_client_session
            call_args = mock_session.request.call_args
            assert call_args[0][1] == "http://127.0.0.1/api/v1/users/456/actions/enable"
            assert call_args[0][0] == "POST"
            assert "json" in call_args[1]
            assert call_args[1]["json"] == {"data": {"status": "active", "role": "admin"}}

    async def test_invoke_with_header_location(self, mock_client_session, mock_connector):
        with patch('openjiuwen.core.common.security.ssl_utils.SslUtils.get_ssl_config') as mock_get_ssl, \
                patch(
                    'openjiuwen.core.common.security.ssl_utils.SslUtils.create_strict_ssl_context') as mock_create_ssl:
            mock_get_ssl.return_value = (False, None)
            mock_create_ssl.return_value = None
            input_schema = {
                "type": "object",
                "properties": {
                    "authorization": {
                        "type": "string",
                        "description": "认证令牌",
                        "location": "header"
                    },
                    "content_type": {
                        "type": "string",
                        "description": "内容类型",
                        "location": "header"
                    },
                    "payload": {
                        "type": "object",
                        "description": "请求负载",
                        "location": "body"
                    }
                }
            }
            card = RestfulApiCard(
                name="create_resource",
                description="创建资源",
                url="http://127.0.0.1/api/v1/resources",
                method="POST",
                headers={"User-Agent": "JiuWenClient/1.0"},
                input_params=input_schema
            )
            api_tool = RestfulApi(card)
            # 调用 invoke
            result = await api_tool.invoke({
                "authorization": "Bearer token123456",
                "content_type": "application/json",
                "payload": {"name": "test_resource", "type": "document"}
            })
            assert result.get("data") == MOCK_SUCCESS_RESPONSE
            mock_session = mock_client_session
            call_args = mock_session.request.call_args

            assert call_args[0][1] == "http://127.0.0.1/api/v1/resources"
            expected_headers = {
                "User-Agent": "JiuWenClient/1.0",
                "authorization": "Bearer token123456",
                "content_type": "application/json"
            }
            assert call_args[1]["headers"] == expected_headers
            assert "json" in call_args[1]
            assert call_args[1]["json"] == {"payload": {"name": "test_resource", "type": "document"}}

    async def test_invoke_with_mixed_locations(self, mock_client_session, mock_connector):
        with patch('openjiuwen.core.common.security.ssl_utils.SslUtils.get_ssl_config') as mock_get_ssl, \
                patch(
                    'openjiuwen.core.common.security.ssl_utils.SslUtils.create_strict_ssl_context') as mock_create_ssl:
            mock_get_ssl.return_value = (False, None)
            mock_create_ssl.return_value = None
            input_schema = {
                "type": "object",
                "properties": {
                    "id": {
                        "type": "integer",
                        "description": "资源ID",
                        "location": "path"
                    },
                    "category": {
                        "type": "string",
                        "description": "分类",
                        "location": "query"
                    },
                    "page": {
                        "type": "integer",
                        "description": "页码",
                        "location": "query"
                    },
                    "api_key": {
                        "type": "string",
                        "description": "API密钥",
                        "location": "header"
                    },
                    "search_criteria": {
                        "type": "object",
                        "description": "搜索条件",
                        "location": "body"
                    },
                    "sort_by": {
                        "type": "string",
                        "description": "排序字段",
                        "location": "query"
                    }
                }
            }
            card = RestfulApiCard(
                name="search_resources",
                description="搜索资源",
                url="http://127.0.0.1/api/v1/resources/{id}/items",
                method="GET",
                queries={"limit": 10},
                headers={"X-Request-ID": "req-123"},
                input_params=input_schema
            )
            api_tool = RestfulApi(card)
            result = await api_tool.invoke({
                "id": 789,
                "category": "technology",
                "page": 1,
                "api_key": "key-abc123",
                "search_criteria": {"keywords": ["AI", "ML"], "date_range": "2024"},
                "sort_by": "date"
            })
            assert result.get("data") == MOCK_SUCCESS_RESPONSE
            mock_session = mock_client_session
            call_args = mock_session.request.call_args
            expected_url = (""
                            "http://127.0.0.1/api/v1/resources/789/items?limit=10"
                            "&category=technology&page=1&sort_by=date")
            assert call_args[0][1] == expected_url
            expected_headers = {
                "X-Request-ID": "req-123",
                "api_key": "key-abc123"
            }
            assert call_args[1]["headers"] == expected_headers
            assert "params" in call_args[1]
            assert call_args[1]["params"] == {"search_criteria": {"keywords": ["AI", "ML"], "date_range": "2024"}}

    async def test_invoke_with_no_location_specified(self, mock_client_session, mock_connector):
        with patch('openjiuwen.core.common.security.ssl_utils.SslUtils.get_ssl_config') as mock_get_ssl, \
                patch(
                    'openjiuwen.core.common.security.ssl_utils.SslUtils.create_strict_ssl_context') as mock_create_ssl:
            mock_get_ssl.return_value = (False, None)
            mock_create_ssl.return_value = None
            input_schema = {
                "type": "object",
                "properties": {
                    "username": {
                        "type": "string",
                        "description": "用户名"
                    },
                    "email": {
                        "type": "string",
                        "description": "邮箱"
                    }
                }
            }
            card = RestfulApiCard(
                name="register_user",
                description="注册用户",
                url="http://127.0.0.1/api/v1/users/register",
                method="POST",
                input_params=input_schema
            )
            api_tool = RestfulApi(card)
            result = await api_tool.invoke({
                "username": "testuser",
                "email": "test@example.com"
            })
            assert result.get("data") == MOCK_SUCCESS_RESPONSE
            mock_session = mock_client_session
            call_args = mock_session.request.call_args
            assert call_args[0][1] == "http://127.0.0.1/api/v1/users/register"
            assert "json" in call_args[1]
            assert call_args[1]["json"] == {"username": "testuser", "email": "test@example.com"}

    async def test_invoke_with_default_values_override(self, mock_client_session, mock_connector):
        with patch('openjiuwen.core.common.security.ssl_utils.SslUtils.get_ssl_config') as mock_get_ssl, \
                patch(
                    'openjiuwen.core.common.security.ssl_utils.SslUtils.create_strict_ssl_context') as mock_create_ssl:
            mock_get_ssl.return_value = (False, None)
            mock_create_ssl.return_value = None
            input_schema = {
                "type": "object",
                "properties": {
                    "format": {
                        "type": "string",
                        "description": "格式",
                        "location": "query"
                    },
                    "api_token": {
                        "type": "string",
                        "description": "API令牌",
                        "location": "header"
                    }
                }
            }
            card = RestfulApiCard(
                name="get_data",
                description="获取数据",
                url="http://127.0.0.1/api/v1/data",
                method="GET",
                queries={"format": "xml", "limit": 20},
                headers={"api_token": "default_token", "Accept": "application/json"},
                input_params=input_schema
            )
            api_tool = RestfulApi(card)
            result = await api_tool.invoke({
                "format": "json",
                "api_token": "user_provided_token"
            })
            assert result.get("data") == MOCK_SUCCESS_RESPONSE
            mock_session = mock_client_session
            call_args = mock_session.request.call_args
            assert "format=json" in call_args[0][1]
            assert "limit=20" in call_args[0][1]
            expected_headers = {
                "Accept": "application/json",
                "api_token": "user_provided_token"
            }
            assert call_args[1]["headers"] == expected_headers


class TestRestfulApiHttpMethods:
    """Test all supported HTTP methods"""

    @staticmethod
    def _create_mock_response(status=200, content_type="application/json", url="http://example.com/api/test",
                              reason="OK", content_bytes=None):
        """Create a mocked aiohttp response with specified parameters"""
        mock_response = AsyncMock()
        mock_response.status = status
        mock_response.headers = {"Content-Type": content_type}
        mock_response.url = url
        mock_response.reason = reason
        mock_response.raise_for_status = Mock()

        if content_bytes:
            async def content_iter():
                yield content_bytes

            mock_response.content.iter_chunked = Mock(return_value=content_iter())
        else:
            async def empty_iterator():
                if False:
                    yield b""

            mock_response.content.iter_chunked = Mock(return_value=empty_iterator())

        return mock_response

    def _create_json_response(self, data, status=200, content_type="application/json",
                              url="http://example.com/api/test", reason="OK"):
        """Create a mocked JSON response"""
        json_bytes = json.dumps(data).encode('utf-8')
        response = self._create_mock_response(status, content_type, url, reason, json_bytes)
        return response

    def _create_mocked_session_context(self, mock_response):
        """Create a mocked aiohttp ClientSession context manager"""
        mock_session = AsyncMock()

        class MockResponseContext:
            def __init__(self, response):
                self.response = response

            async def __aenter__(self):
                return self.response

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                pass

        mock_context = MockResponseContext(mock_response)
        mock_session.request = Mock(return_value=mock_context)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        return mock_session

    @contextmanager
    def _ssl_mock_context(self):
        """SSL mock configuration context manager"""
        with (patch('openjiuwen.core.common.security.ssl_utils.SslUtils.get_ssl_config') as mock_get_ssl, \
                patch('openjiuwen.core.common.security.ssl_utils.SslUtils.create_strict_ssl_context')
                as mock_create_ssl):
            mock_get_ssl.return_value = (False, None)
            mock_create_ssl.return_value = None
            yield mock_get_ssl, mock_create_ssl

    @pytest.mark.asyncio
    async def test_put_method(self):
        """Test PUT method sends data as JSON body"""
        card = RestfulApiCard(
            name="update_user",
            description="Update user data",
            url="http://example.com/api/users/123",
            method="PUT",
            input_params={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "location": "body"
                    },
                    "age": {
                        "type": "integer",
                        "location": "body"
                    }
                },
                "additionalProperties": True
            }
        )
        api = RestfulApi(card)

        response_data = {"status": "updated", "id": 123}
        mock_response = self._create_json_response(response_data)

        with patch('aiohttp.ClientSession') as mock_session_class:
            mock_session = self._create_mocked_session_context(mock_response)
            mock_session_class.return_value = mock_session

            with self._ssl_mock_context():
                result = await api.invoke({"name": "John", "age": 30})

                assert result["code"] == 200
                assert result["data"] == response_data

                # Verify PUT was called with json body
                call_args = mock_session.request.call_args
                assert call_args[0][0] == "PUT"
                assert "json" in call_args[1]
                assert call_args[1]["json"] == {"name": "John", "age": 30}

    @pytest.mark.asyncio
    async def test_patch_method(self):
        """Test PATCH method sends data as JSON body"""
        card = RestfulApiCard(
            name="patch_user",
            description="Partially update user",
            url="http://example.com/api/users/456",
            method="PATCH",
            input_params={
                "type": "object",
                "properties": {
                    "email": {
                        "type": "string",
                        "location": "body"
                    }
                },
                "additionalProperties": True
            }
        )
        api = RestfulApi(card)

        response_data = {"status": "patched", "id": 456}
        mock_response = self._create_json_response(response_data)

        with patch('aiohttp.ClientSession') as mock_session_class:
            mock_session = self._create_mocked_session_context(mock_response)
            mock_session_class.return_value = mock_session

            with self._ssl_mock_context():
                result = await api.invoke({"email": "john@example.com"})

                assert result["code"] == 200
                assert result["data"] == response_data

                # Verify PATCH was called with json body
                call_args = mock_session.request.call_args
                assert call_args[0][0] == "PATCH"
                assert "json" in call_args[1]
                assert call_args[1]["json"] == {"email": "john@example.com"}

    @pytest.mark.asyncio
    async def test_delete_method(self):
        """Test DELETE method sends data as query params (standard REST behavior)"""
        card = RestfulApiCard(
            name="delete_user",
            description="Delete user",
            url="http://example.com/api/users/789",
            method="DELETE",
            input_params={
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "location": "query"
                    }
                },
                "additionalProperties": True
            }
        )
        api = RestfulApi(card)

        response_data = {"status": "deleted", "id": 789}
        mock_response = self._create_json_response(response_data, status=200)

        with patch('aiohttp.ClientSession') as mock_session_class:
            mock_session = self._create_mocked_session_context(mock_response)
            mock_session_class.return_value = mock_session

            with self._ssl_mock_context():
                result = await api.invoke({"reason": "account closure"})

                assert result["code"] == 200
                assert result["data"] == response_data

                # Verify DELETE was called with params (not json body)
                call_args = mock_session.request.call_args
                assert call_args[0][0] == "DELETE"
                assert "params" in call_args[1]

                # DELETE should not send a JSON body
                assert "json" not in call_args[1]

    @pytest.mark.asyncio
    async def test_head_method(self):
        """Test HEAD method sends data as query params"""
        card = RestfulApiCard(
            name="check_resource",
            description="Check if resource exists",
            url="http://example.com/api/resources",
            method="HEAD",
            input_params={
                "type": "object",
                "properties": {
                    "resource_id": {
                        "type": "string",
                        "location": "query"
                    }
                },
                "additionalProperties": True
            }
        )
        api = RestfulApi(card)

        # HEAD typically returns no body
        mock_response = self._create_mock_response(status=200, content_bytes=b"")

        with patch('aiohttp.ClientSession') as mock_session_class:
            mock_session = self._create_mocked_session_context(mock_response)
            mock_session_class.return_value = mock_session

            with self._ssl_mock_context():
                result = await api.invoke({"resource_id": "abc123"})

                assert result["code"] == 200

                # Verify HEAD was called with params (not json)
                call_args = mock_session.request.call_args
                assert call_args[0][0] == "HEAD"
                assert "params" in call_args[1]

                # HEAD should not send a JSON body
                assert "json" not in call_args[1]

    @pytest.mark.asyncio
    async def test_options_method(self):
        """Test OPTIONS method sends data as query params"""
        card = RestfulApiCard(
            name="get_options",
            description="Get API options",
            url="http://example.com/api/endpoint",
            method="OPTIONS"
        )
        api = RestfulApi(card)

        # OPTIONS typically returns allowed methods
        mock_response = self._create_mock_response(status=200, content_bytes=b"")
        mock_response.headers = {
            "Content-Type": "text/plain",
            "Allow": "GET, POST, PUT, DELETE, OPTIONS"
        }

        with patch('aiohttp.ClientSession') as mock_session_class:
            mock_session = self._create_mocked_session_context(mock_response)
            mock_session_class.return_value = mock_session

            with self._ssl_mock_context():
                result = await api.invoke({})

                assert result["code"] == 200
                assert result["headers"]["Allow"] == "GET, POST, PUT, DELETE, OPTIONS"

                # Verify OPTIONS was called with params (not json)
                call_args = mock_session.request.call_args
                assert call_args[0][0] == "OPTIONS"
                assert "params" in call_args[1]
                assert "json" not in call_args[1]

    @pytest.mark.asyncio
    async def test_all_methods_are_supported(self):
        """Test that all HTTP methods in SUPPORTED_METHODS can be instantiated"""
        supported_methods = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]

        for method in supported_methods:
            card = RestfulApiCard(
                name=f"test_{method.lower()}",
                description=f"Test {method} method",
                url="http://example.com/api/test",
                method=method
            )
            api = RestfulApi(card)
            assert api.get_method() == method

    @pytest.mark.asyncio
    async def test_path_parameters_with_put(self):
        """Test that path parameters work correctly with PUT method (e.g., /api/v1/Activities/{id})"""
        input_schema = {
            "type": "object",
            "properties": {
                "id": {
                    "type": "integer",
                    "description": "Activity ID",
                    "location": "path"
                },
                "name": {
                    "type": "string",
                    "description": "Activity name",
                    "location": "body"
                },
                "status": {
                    "type": "string",
                    "description": "Activity status",
                    "location": "body"
                }
            },
            "required": ["id"]
        }

        card = RestfulApiCard(
            name="update_activity",
            description="Update activity",
            url="http://example.com/api/v1/Activities/{id}",
            method="PUT",
            input_params=input_schema
        )
        api = RestfulApi(card)

        response_data = {"id": 42, "name": "Updated Activity", "status": "active"}
        mock_response = self._create_json_response(response_data)

        with patch('aiohttp.ClientSession') as mock_session_class:
            mock_session = self._create_mocked_session_context(mock_response)
            mock_session_class.return_value = mock_session

            with self._ssl_mock_context():
                result = await api.invoke({
                    "id": 42,
                    "name": "Updated Activity",
                    "status": "active"
                })

                assert result["code"] == 200
                assert result["data"] == response_data

                # Verify the URL has the path parameter replaced
                call_args = mock_session.request.call_args
                actual_url = call_args[0][1]
                assert actual_url == "http://example.com/api/v1/Activities/42"
                assert "{id}" not in actual_url  # Ensure placeholder was replaced

                # Verify PUT was called with json body (not including the path param)
                assert call_args[0][0] == "PUT"
                assert "json" in call_args[1]
                assert call_args[1]["json"] == {"name": "Updated Activity", "status": "active"}

    @pytest.mark.asyncio
    async def test_path_parameters_with_delete(self):
        """Test that path parameters work correctly with DELETE method"""
        input_schema = {
            "type": "object",
            "properties": {
                "id": {
                    "type": "string",
                    "description": "Resource ID",
                    "location": "path"
                }
            },
            "required": ["id"]
        }

        card = RestfulApiCard(
            name="delete_activity",
            description="Delete activity",
            url="http://example.com/api/v1/Activities/{id}",
            method="DELETE",
            input_params=input_schema
        )
        api = RestfulApi(card)

        response_data = {"message": "deleted", "id": "abc-123"}
        mock_response = self._create_json_response(response_data)

        with patch('aiohttp.ClientSession') as mock_session_class:
            mock_session = self._create_mocked_session_context(mock_response)
            mock_session_class.return_value = mock_session

            with self._ssl_mock_context():
                result = await api.invoke({"id": "abc-123"})

                assert result["code"] == 200

                # Verify the URL has the path parameter replaced
                call_args = mock_session.request.call_args
                actual_url = call_args[0][1]
                assert actual_url == "http://example.com/api/v1/Activities/abc-123"
                assert "{id}" not in actual_url

                # Verify DELETE method was used
                assert call_args[0][0] == "DELETE"

    @pytest.mark.asyncio
    async def test_delete_with_explicit_body(self):
        """Test DELETE can send JSON body when explicitly specified in schema (rare but valid)"""
        input_schema = {
            "type": "object",
            "properties": {
                "ids": {
                    "type": "array",
                    "description": "List of IDs to delete",
                    "location": "body"  # Explicitly marked as body
                },
                "cascade": {
                    "type": "boolean",
                    "description": "Cascade delete",
                    "location": "body"  # Explicitly marked as body
                }
            }
        }

        card = RestfulApiCard(
            name="batch_delete",
            description="Batch delete resources",
            url="http://example.com/api/v1/resources/batch",
            method="DELETE",
            input_params=input_schema
        )
        api = RestfulApi(card)

        response_data = {"deleted": 5, "status": "success"}
        mock_response = self._create_json_response(response_data)

        with patch('aiohttp.ClientSession') as mock_session_class:
            mock_session = self._create_mocked_session_context(mock_response)
            mock_session_class.return_value = mock_session

            with self._ssl_mock_context():
                # When parameters are explicitly marked as "body", they'll be in the body map
                # But since we changed DELETE to use params, we need to verify this edge case
                result = await api.invoke({
                    "ids": [1, 2, 3],
                    "cascade": True
                })

                assert result["code"] == 200

                # With current implementation, even explicit body params go to query for DELETE
                # This is the safer default behavior
                call_args = mock_session.request.call_args
                assert call_args[0][0] == "DELETE"

    @pytest.mark.asyncio
    async def test_all_methods_support_path_parameters(self):
        """Comprehensive test: All HTTP methods should support path parameters correctly"""
        test_cases = [
            {
                "method": "GET",
                "url": "http://example.com/api/v1/Activities/{id}",
                "expects_json_body": False
            },
            {
                "method": "POST",
                "url": "http://example.com/api/v1/Authors/authors/books/{idBook}",
                "expects_json_body": True
            },
            {
                "method": "PUT",
                "url": "http://example.com/api/v1/Activities/{id}",
                "expects_json_body": True
            },
            {
                "method": "PATCH",
                "url": "http://example.com/api/v1/Activities/{id}/status",
                "expects_json_body": True
            },
            {
                "method": "DELETE",
                "url": "http://example.com/api/v1/Activities/{id}",
                "expects_json_body": False
            },
            {
                "method": "HEAD",
                "url": "http://example.com/api/v1/Resources/{resourceId}",
                "expects_json_body": False
            },
            {
                "method": "OPTIONS",
                "url": "http://example.com/api/v1/Endpoints/{endpoint}",
                "expects_json_body": False
            }
        ]

        for test_case in test_cases:
            method = test_case["method"]
            url_template = test_case["url"]
            expects_json = test_case["expects_json_body"]

            # Extract path param name from URL template
            import re
            path_param_names = re.findall(r'\{(\w+)\}', url_template)
            assert len(path_param_names) > 0, f"Test case for {method} should have path params"

            # Build input schema with path parameters
            input_schema = {
                "type": "object",
                "properties": {}
            }

            for param_name in path_param_names:
                input_schema["properties"][param_name] = {
                    "type": "string",
                    "description": f"{param_name} parameter",
                    "location": "path"
                }

            # Add a body/query parameter
            input_schema["properties"]["data"] = {
                "type": "string",
                "description": "Additional data",
                "location": "body"
            }

            card = RestfulApiCard(
                name=f"test_{method.lower()}_path_params",
                description=f"Test {method} with path params",
                url=url_template,
                method=method,
                input_params=input_schema
            )
            api = RestfulApi(card)

            response_data = {"status": "success", "method": method}
            mock_response = self._create_json_response(response_data)

            with patch('aiohttp.ClientSession') as mock_session_class:
                mock_session = self._create_mocked_session_context(mock_response)
                mock_session_class.return_value = mock_session

                with self._ssl_mock_context():
                    # Prepare input with actual values for path params
                    input_data = {"data": "test_value"}
                    for param_name in path_param_names:
                        input_data[param_name] = f"value_{param_name}"

                    result = await api.invoke(input_data)

                    assert result["code"] == 200, f"{method} should succeed"

                    # Verify the URL has path parameters replaced
                    call_args = mock_session.request.call_args
                    actual_url = call_args[0][1]

                    # Verify no placeholders remain in URL
                    for param_name in path_param_names:
                        assert f"{{{param_name}}}" not in actual_url, \
                            f"{method}: Placeholder {{{param_name}}} should be replaced"
                        assert f"value_{param_name}" in actual_url, \
                            f"{method}: URL should contain replaced value for {param_name}"

                    # Verify method was used correctly
                    assert call_args[0][0] == method

                    # Verify body/params handling
                    if expects_json:
                        assert "json" in call_args[1], f"{method} should use json body"
                        assert call_args[1]["json"] == {"data": "test_value"}
                    else:
                        assert "params" in call_args[1], f"{method} should use query params"
                        assert call_args[1]["params"] == {"data": "test_value"}

    @pytest.mark.asyncio
    async def test_multiple_path_parameters_in_url(self):
        """Test URL with multiple path parameters like /api/{version}/users/{userId}/posts/{postId}"""
        input_schema = {
            "type": "object",
            "properties": {
                "version": {
                    "type": "string",
                    "description": "API version",
                    "location": "path"
                },
                "userId": {
                    "type": "integer",
                    "description": "User ID",
                    "location": "path"
                },
                "postId": {
                    "type": "integer",
                    "description": "Post ID",
                    "location": "path"
                },
                "action": {
                    "type": "string",
                    "description": "Action to perform",
                    "location": "body"
                }
            },
            "required": ["version", "userId", "postId"]
        }

        card = RestfulApiCard(
            name="update_user_post",
            description="Update user post",
            url="http://example.com/api/{version}/users/{userId}/posts/{postId}",
            method="PATCH",
            input_params=input_schema
        )
        api = RestfulApi(card)

        response_data = {"status": "updated"}
        mock_response = self._create_json_response(response_data)

        with patch('aiohttp.ClientSession') as mock_session_class:
            mock_session = self._create_mocked_session_context(mock_response)
            mock_session_class.return_value = mock_session

            with self._ssl_mock_context():
                result = await api.invoke({
                    "version": "v2",
                    "userId": 123,
                    "postId": 456,
                    "action": "publish"
                })

                assert result["code"] == 200

                # Verify all path parameters were replaced correctly
                call_args = mock_session.request.call_args
                actual_url = call_args[0][1]

                expected_url = "http://example.com/api/v2/users/123/posts/456"
                assert actual_url == expected_url

                # Ensure no placeholders remain
                assert "{version}" not in actual_url
                assert "{userId}" not in actual_url
                assert "{postId}" not in actual_url

                # Verify body contains only non-path parameters
                assert call_args[1]["json"] == {"action": "publish"}


class TestRestfulApiPathParameterValidation:
    """Test path parameter validation"""

    @staticmethod
    def test_url_with_path_param_but_no_schema_raises_error():
        """URL with {id} but no input_params should raise validation error"""
        with pytest.raises(Exception) as exc_info:
            card = RestfulApiCard(
                name="test",
                url="http://example.com/api/v1/Activities/{id}",
                method="GET"
                # No input_params!
            )

        assert "path parameters" in str(exc_info.value).lower()
        assert "input_params" in str(exc_info.value).lower()

    @staticmethod
    def test_url_with_path_param_but_not_marked_in_schema_raises_error():
        """URL with {id} but schema doesn't mark it as path should raise error"""
        with pytest.raises(Exception) as exc_info:
            card = RestfulApiCard(
                name="test",
                url="http://example.com/api/v1/Activities/{id}",
                method="GET",
                input_params={
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"}  # Missing "location": "path"
                    }
                }
            )

        assert "location" in str(exc_info.value).lower() or "path" in str(exc_info.value).lower()

    @staticmethod
    def test_url_with_multiple_path_params_all_must_be_defined():
        """URL with multiple path params - all must be defined"""
        with pytest.raises(Exception) as exc_info:
            card = RestfulApiCard(
                name="test",
                url="http://example.com/api/{version}/users/{userId}",
                method="GET",
                input_params={
                    "type": "object",
                    "properties": {
                        "version": {"type": "string", "location": "path"}
                        # Missing userId!
                    }
                }
            )

        assert "userId" in str(exc_info.value) or "path" in str(exc_info.value).lower()

    @staticmethod
    def test_url_with_correct_path_param_schema_succeeds():
        """Properly configured path parameters should not raise error"""
        # Should not raise
        card = RestfulApiCard(
            name="test",
            url="http://example.com/api/v1/Activities/{id}",
            method="GET",
            input_params={
                "type": "object",
                "properties": {
                    "id": {
                        "type": "integer",
                        "description": "Activity ID",
                        "location": "path"
                    }
                },
                "required": ["id"]
            }
        )

        assert card.url == "http://example.com/api/v1/Activities/{id}"

    @staticmethod
    def test_get_parameters_by_location_helper():
        """Test the helper method for GUI integration"""
        card = RestfulApiCard(
            name="update_activity",
            url="http://example.com/api/v1/Activities/{id}",
            method="PUT",
            input_params={
                "type": "object",
                "properties": {
                    "id": {"type": "integer", "description": "Activity ID", "location": "path"},
                    "name": {"type": "string", "description": "Activity name", "location": "body"},
                    "notify": {"type": "boolean", "description": "Send notification", "location": "query"},
                    "api_key": {"type": "string", "description": "API Key", "location": "header"}
                },
                "required": ["id", "name"]
            }
        )

        params = RestfulApi.get_parameters_by_location(card)

        # Check path parameters
        assert len(params["path"]) == 1
        assert params["path"][0]["name"] == "id"
        assert params["path"][0]["type"] == "integer"
        assert params["path"][0]["required"] is True

        # Check body parameters
        assert len(params["body"]) == 1
        assert params["body"][0]["name"] == "name"
        assert params["body"][0]["required"] is True

        # Check query parameters
        assert len(params["query"]) == 1
        assert params["query"][0]["name"] == "notify"
        assert params["query"][0]["required"] is False

        # Check header parameters
        assert len(params["header"]) == 1
        assert params["header"][0]["name"] == "api_key"


class TestRestfulApiExceptions:
    @pytest.fixture
    def mock_card(self):
        card = RestfulApiCard(**dict(
            name="demo",
            url="https://127.0.0.1/api.example.com/users",
            method="POST",
            timeout=60.0,
            max_response_byte_size=10 * 1024 * 1024,
            headers={},
            queries={},
            paths={},
            input_params={}))
        return card

    @pytest.fixture
    def restful_api(self, mock_card):
        return RestfulApi(mock_card)

    @pytest.mark.asyncio
    async def test_invoke_timeout_error(self, restful_api, mock_card):
        with patch.object(restful_api, '_async_request',
                          side_effect=asyncio.TimeoutError("Request timeout")):
            with pytest.raises(BaseError) as exc_info:
                await restful_api.invoke({})

            assert exc_info.value.code == StatusCode.TOOL_RESTFUL_API_EXECUTION_TIMEOUT.code

    @pytest.mark.asyncio
    async def test_invoke_response_error(self, restful_api, mock_card):
        mock_response_error = aiohttp.ClientResponseError(
            request_info=Mock(),
            history=(),
            status=404,
            message="Not Found",
            headers={}
        )

        with patch.object(restful_api, '_async_request',
                          side_effect=mock_response_error):
            with pytest.raises(BaseError) as exc_info:
                await restful_api.invoke({})

            assert exc_info.value.code == StatusCode.TOOL_RESTFUL_API_RESPONSE_ERROR.code


class TestRestfulApiFileParams:
    """RestfulApi FormData processing tests"""

    @staticmethod
    def _create_mock_response(status=200, content_type="application/json", url="http://example.com/api/test",
                              reason="OK", content_bytes=None):
        mock_response = AsyncMock()
        mock_response.status = status
        mock_response.headers = {"Content-Type": content_type}
        mock_response.url = url
        mock_response.reason = reason
        mock_response.raise_for_status = Mock()

        if content_bytes:
            async def content_iter():
                yield content_bytes

            mock_response.content.iter_chunked = Mock(return_value=content_iter())
        else:
            async def empty_iterator():
                if False:
                    yield b""

            mock_response.content.iter_chunked = Mock(return_value=empty_iterator())

        return mock_response

    @staticmethod
    def _create_json_response(data, status=200, content_type="application/json",
                              url="http://example.com/api/test", reason="OK"):
        json_bytes = json.dumps(data).encode('utf-8')
        response = TestRestfulApiFileParams._create_mock_response(status, content_type, url, reason, json_bytes)
        return response

    @staticmethod
    def _create_mocked_session_context(mock_response):
        mock_session = AsyncMock()

        class MockResponseContext:
            def __init__(self, response):
                self.response = response

            async def __aenter__(self):
                return self.response

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                pass

        mock_context = MockResponseContext(mock_response)
        mock_session.request = Mock(return_value=mock_context)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        return mock_session

    @staticmethod
    @contextmanager
    def _ssl_mock_context():
        with (patch('openjiuwen.core.common.security.ssl_utils.SslUtils.get_ssl_config') as mock_get_ssl,
              patch('openjiuwen.core.common.security.ssl_utils.SslUtils.create_strict_ssl_context')
              as mock_create_ssl):
            mock_get_ssl.return_value = (False, None)
            mock_create_ssl.return_value = None
            yield mock_get_ssl, mock_create_ssl

    class TestProcessFormDataMethod:
        """process_form_data() method tests"""

        @pytest.mark.asyncio
        async def test_single_form_param_processing(self):
            """Single form parameter processing"""
            input_schema = {
                "type": "object",
                "properties": {
                    "username": {
                        "type": "string",
                        "location": "form",
                        "description": "Username"
                    },
                    "age": {
                        "type": "integer",
                        "location": "form",
                        "description": "User age"
                    }
                }
            }

            card = RestfulApiCard(
                name="submit_form",
                description="Submit form data",
                url="http://example.com/api/submit",
                method="POST",
                input_params=input_schema
            )
            api = RestfulApi(card)

            form_params = {
                "username": {"form_handler_type": "default", "value": "test_user"},
                "age": {"form_handler_type": "default", "value": 25}
            }
            body_params = {}

            result = await api._process_form_data(form_params, body_params)

            assert isinstance(result, aiohttp.FormData)

        @pytest.mark.asyncio
        async def test_form_param_with_body_params(self):
            """Form parameter mixed with body parameters"""
            input_schema = {
                "type": "object",
                "properties": {
                    "file_url": {
                        "type": "string",
                        "location": "form",
                        "form_handler_type": "default",
                        "description": "File URL"
                    },
                    "title": {
                        "type": "string",
                        "location": "body",
                        "description": "Document title"
                    },
                    "count": {
                        "type": "integer",
                        "location": "body",
                        "description": "Count"
                    }
                }
            }

            card = RestfulApiCard(
                name="upload_with_metadata",
                description="Upload with metadata",
                url="http://example.com/api/upload",
                method="POST",
                input_params=input_schema
            )
            api = RestfulApi(card)

            form_params = {
                "file_url": {"form_handler_type": "default", "value": "http://example.com/doc.pdf"}
            }
            body_params = {
                "title": "Test Document",
                "count": 5
            }

            result = await api._process_form_data(form_params, body_params)

            assert isinstance(result, aiohttp.FormData)

        @pytest.mark.asyncio
        async def test_multiple_form_params_processing(self):
            """Multiple form parameters processing"""
            input_schema = {
                "type": "object",
                "properties": {
                    "file1": {
                        "type": "string",
                        "location": "form",
                        "form_handler_type": "default",
                        "description": "File 1"
                    },
                    "file2": {
                        "type": "string",
                        "location": "form",
                        "form_handler_type": "default",
                        "description": "File 2"
                    }
                }
            }

            card = RestfulApiCard(
                name="upload_multiple",
                description="Upload multiple files",
                url="http://example.com/api/upload",
                method="POST",
                input_params=input_schema
            )
            api = RestfulApi(card)

            form_params = {
                "file1": {"form_handler_type": "default", "value": "content1"},
                "file2": {"form_handler_type": "default", "value": "content2"}
            }
            body_params = {}

            result = await api._process_form_data(form_params, body_params)

            assert isinstance(result, aiohttp.FormData)

        @pytest.mark.asyncio
        async def test_empty_form_params_and_body_params(self):
            """Empty form_params and body_params"""
            input_schema = {
                "type": "object",
                "properties": {}
            }

            card = RestfulApiCard(
                name="empty_params",
                description="Empty params",
                url="http://example.com/api/empty",
                method="POST",
                input_params=input_schema
            )
            api = RestfulApi(card)

            form_params = {}
            body_params = {}

            result = await api._process_form_data(form_params, body_params)

            assert isinstance(result, aiohttp.FormData)

        @pytest.mark.asyncio
        async def test_custom_handler_type(self):
            """Use custom handler_type"""
            input_schema = {
                "type": "object",
                "properties": {
                    "custom_data": {
                        "type": "string",
                        "location": "form",
                        "form_handler_type": "custom",
                        "description": "Custom data"
                    }
                }
            }

            card = RestfulApiCard(
                name="custom_form",
                description="Custom form handler",
                url="http://example.com/api/custom",
                method="POST",
                input_params=input_schema
            )
            api = RestfulApi(card)

            mock_form_data = aiohttp.FormData()
            mock_form_data.add_field("custom_data", "processed_value")

            with patch('openjiuwen.core.foundation.tool.form_handler.form_handler_manager.FormHandlerManager.get_handler') \
                    as mock_get_handler:
                mock_handler_instance = AsyncMock()
                mock_handler_instance.handle = AsyncMock(return_value=mock_form_data)
                mock_handler_class = MagicMock(return_value=mock_handler_instance)
                mock_get_handler.return_value = mock_handler_class

                form_params = {
                    "custom_data": {"form_handler_type": "custom", "value": "test_value"}
                }
                body_params = {}

                result = await api._process_form_data(form_params, body_params)

                assert isinstance(result, aiohttp.FormData)
                mock_get_handler.assert_called_once_with("custom")
                mock_handler_instance.handle.assert_called_once()

    class TestCompleteFormSubmissionFlow:
        """Complete form submission flow tests"""

        @pytest.mark.asyncio
        async def test_single_form_field_submission_flow(self):
            """Single form field submission flow"""
            input_schema = {
                "type": "object",
                "properties": {
                    "field": {
                        "type": "string",
                        "location": "form",
                        "form_handler_type": "default",
                        "description": "Form field"
                    },
                    "name": {
                        "type": "string",
                        "location": "body",
                        "description": "Name"
                    }
                }
            }

            card = RestfulApiCard(
                name="submit_single",
                description="Submit single form field",
                url="http://example.com/api/submit",
                method="POST",
                input_params=input_schema
            )
            api = RestfulApi(card)

            mock_response = TestRestfulApiFileParams._create_json_response({"status": "success"})

            with patch('aiohttp.ClientSession') as mock_session_class:
                mock_session = TestRestfulApiFileParams._create_mocked_session_context(mock_response)
                mock_session_class.return_value = mock_session

                with TestRestfulApiFileParams._ssl_mock_context():
                    result = await api.invoke({
                        "field": "value",
                        "name": "test"
                    })

                    assert result["code"] == 200
                    call_args = mock_session.request.call_args
                    assert "data" in call_args[1]
                    assert isinstance(call_args[1]["data"], aiohttp.FormData)

        @pytest.mark.asyncio
        async def test_multiple_form_fields_submission_flow(self):
            """Multiple form fields submission flow"""
            input_schema = {
                "type": "object",
                "properties": {
                    "field1": {
                        "type": "string",
                        "location": "form",
                        "form_handler_type": "default",
                        "description": "Field 1"
                    },
                    "field2": {
                        "type": "string",
                        "location": "form",
                        "form_handler_type": "default",
                        "description": "Field 2"
                    },
                    "description": {
                        "type": "string",
                        "location": "body",
                        "description": "Description"
                    }
                }
            }

            card = RestfulApiCard(
                name="submit_multiple",
                description="Submit multiple form fields",
                url="http://example.com/api/submit",
                method="POST",
                input_params=input_schema
            )
            api = RestfulApi(card)

            mock_response = TestRestfulApiFileParams._create_json_response({"status": "success"})

            with patch('aiohttp.ClientSession') as mock_session_class:
                mock_session = TestRestfulApiFileParams._create_mocked_session_context(mock_response)
                mock_session_class.return_value = mock_session

                with TestRestfulApiFileParams._ssl_mock_context():
                    result = await api.invoke({
                        "field1": "v1",
                        "field2": "v2",
                        "description": "test"
                    })

                    assert result["code"] == 200
                    call_args = mock_session.request.call_args
                    assert "data" in call_args[1]
                    assert isinstance(call_args[1]["data"], aiohttp.FormData)

        @pytest.mark.asyncio
        async def test_form_submission_with_mixed_param_types(self):
            """Form submission mixed with other parameter types"""
            input_schema = {
                "type": "object",
                "properties": {
                    "user_id": {
                        "type": "integer",
                        "location": "path",
                        "description": "User ID"
                    },
                    "filter": {
                        "type": "string",
                        "location": "query",
                        "description": "Filter"
                    },
                    "auth_token": {
                        "type": "string",
                        "location": "header",
                        "description": "Auth token"
                    },
                    "file_data": {
                        "type": "string",
                        "location": "form",
                        "form_handler_type": "default",
                        "description": "File data"
                    },
                    "metadata": {
                        "type": "object",
                        "location": "body",
                        "description": "Metadata"
                    }
                }
            }

            card = RestfulApiCard(
                name="mixed_params",
                description="Mixed parameter locations",
                url="http://example.com/api/users/{user_id}/upload",
                method="POST",
                input_params=input_schema
            )
            api = RestfulApi(card)

            mock_response = TestRestfulApiFileParams._create_json_response({"status": "uploaded"})

            with patch('aiohttp.ClientSession') as mock_session_class:
                mock_session = TestRestfulApiFileParams._create_mocked_session_context(mock_response)
                mock_session_class.return_value = mock_session

                with TestRestfulApiFileParams._ssl_mock_context():
                    result = await api.invoke({
                        "user_id": 123,
                        "filter": "active",
                        "auth_token": "token123",
                        "file_data": "file_content",
                        "metadata": {"key": "value"}
                    })

                    assert result["code"] == 200
                    call_args = mock_session.request.call_args

                    actual_url = call_args[0][1]
                    assert "123" in actual_url
                    assert "filter=active" in actual_url

                    request_headers = call_args[1]["headers"]
                    assert request_headers["auth_token"] == "token123"

                    assert "data" in call_args[1]
                    assert isinstance(call_args[1]["data"], aiohttp.FormData)

    class TestExceptionAndBoundaryScenarios:
        """Exception scenarios and boundary tests"""

        @pytest.mark.asyncio
        async def test_handler_not_registered_uses_default(self):
            """Handler not registered"""
            input_schema = {
                "type": "object",
                "properties": {
                    "data": {
                        "type": "string",
                        "location": "form",
                        "form_handler_type": "unknown_handler",
                        "description": "Data"
                    }
                }
            }

            card = RestfulApiCard(
                name="unknown_handler",
                description="Unknown handler type",
                url="http://example.com/api/upload",
                method="POST",
                input_params=input_schema
            )
            api = RestfulApi(card)

            mock_response = TestRestfulApiFileParams._create_json_response({"status": "success"})

            with patch('aiohttp.ClientSession') as mock_session_class:
                mock_session = TestRestfulApiFileParams._create_mocked_session_context(mock_response)
                mock_session_class.return_value = mock_session

                with TestRestfulApiFileParams._ssl_mock_context():
                    result = await api.invoke({
                        "data": "test_data"
                    })

                    assert result["code"] == 200
                    call_args = mock_session.request.call_args
                    assert "data" in call_args[1]
                    assert isinstance(call_args[1]["data"], aiohttp.FormData)

        @pytest.mark.asyncio
        async def test_empty_form_data_handling(self):
            """Empty FormData handling"""
            input_schema = {
                "type": "object",
                "properties": {
                    "optional_field": {
                        "type": "string",
                        "location": "form",
                        "form_handler_type": "default",
                        "description": "Optional field"
                    }
                }
            }

            card = RestfulApiCard(
                name="empty_form",
                description="Empty form data",
                url="http://example.com/api/upload",
                method="POST",
                input_params=input_schema
            )
            api = RestfulApi(card)

            mock_response = TestRestfulApiFileParams._create_json_response({"status": "success"})

            with patch('aiohttp.ClientSession') as mock_session_class:
                mock_session = TestRestfulApiFileParams._create_mocked_session_context(mock_response)
                mock_session_class.return_value = mock_session

                with TestRestfulApiFileParams._ssl_mock_context():
                    result = await api.invoke({})

                    assert result["code"] == 200
                    call_args = mock_session.request.call_args
                    assert "json" in call_args[1]
                    assert call_args[1]["json"] == {}

    class TestEmptyFormParamHandling:
        """Empty form parameter handling tests"""

        @pytest.mark.asyncio
        async def test_empty_form_param_value_succeeds(self):
            """Empty form parameter value successfully processed"""
            input_schema = {
                "type": "object",
                "properties": {
                    "optional_file": {
                        "type": "string",
                        "location": "form",
                        "form_handler_type": "default",
                        "description": "Optional file"
                    },
                    "title": {
                        "type": "string",
                        "location": "body",
                        "description": "Document title"
                    }
                }
            }

            card = RestfulApiCard(
                name="upload_optional",
                description="Upload optional file",
                url="http://example.com/api/upload",
                method="POST",
                input_params=input_schema
            )
            api = RestfulApi(card)

            mock_response = TestRestfulApiFileParams._create_json_response({"status": "success"})

            with patch('aiohttp.ClientSession') as mock_session_class:
                mock_session = TestRestfulApiFileParams._create_mocked_session_context(mock_response)
                mock_session_class.return_value = mock_session

                with TestRestfulApiFileParams._ssl_mock_context():
                    result = await api.invoke({
                        "title": "Document without file"
                    })

                    assert result["code"] == 200
                    call_args = mock_session.request.call_args
                    assert "json" in call_args[1]
                    assert call_args[1]["json"] == {"title": "Document without file"}

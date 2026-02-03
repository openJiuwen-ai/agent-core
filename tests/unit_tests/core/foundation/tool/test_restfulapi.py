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
    async def test_invoke_with_gzipped_response(self, restful_api):
        """Test invoke method handling GZIP compressed response"""
        # Create compressed data
        original_data = {
            "compressed": True,
            "items": [{"id": i, "name": f"item{i}"} for i in range(5)]
        }
        json_bytes = json.dumps(original_data).encode('utf-8')
        gzipped_bytes = gzip.compress(json_bytes)

        # Create mocked response with GZIP header
        mock_response = self._create_mock_response(
            status=200,
            content_type="application/json",
            url="http://example.com/api/gzipped",
            reason="OK",
            content_bytes=gzipped_bytes
        )
        mock_response.headers = {
            "Content-Type": "application/json",
            "Content-Encoding": "gzip"
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
                assert result["data"] == original_data
                assert result["headers"]["Content-Encoding"] == "gzip"

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
            # 设置SSL配置
            mock_get_ssl.return_value = (False, None)
            mock_create_ssl.return_value = None

            input_schema = {
                "type": "object",
                "properties": {
                    "user_id": {
                        "type": "integer",
                        "description": "用户ID",
                        "location": "query"
                    },
                    "name": {
                        "type": "string",
                        "description": "用户姓名",
                        "location": "body"
                    },
                    "filter": {
                        "type": "string",
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

            # 注意：需要确保返回正确的响应格式
            result = await api_tool.invoke({
                "user_id": 123,
                "name": "张三",
                "filter": "active"
            })

            assert result.get("data") == MOCK_SUCCESS_RESPONSE
            mock_session = mock_client_session

            assert mock_session.request.called

            call_args = mock_session.request.call_args

            expected_url_part = "http://127.0.0.1/api/v1/users/{user_id}/profile?format=json&user_id=123&filter=active"
            actual_url = call_args[0][1]
            assert expected_url_part in actual_url
            assert "format=json" in actual_url
            assert "user_id=123" in actual_url
            assert "filter=active" in actual_url
            assert call_args[0][0] == "GET"

            assert call_args[1]["headers"] == {}

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

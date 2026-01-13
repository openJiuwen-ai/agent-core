#!/usr/bin/python3.11
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025. All rights reserved
import asyncio
import json
import os
from unittest import mock
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import aiohttp
import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import ValidationError
from openjiuwen.core.foundation.tool import ToolInfo, RestfulApiCard
from openjiuwen.core.foundation.tool.service_api.restful_api import RestfulApi

os.environ["SSRF_PROTECT_ENABLED"] = "false"


@pytest.mark.asyncio
class TestRestFulApi:
    def assertEqual(self, left, right):
        assert left == right

    @pytest.fixture(autouse=True)
    def setUp(self):
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
        self.mocked_functions.stop()

    @patch('requests.sessions.Session.request')
    async def test_invoke(self, mock_request):
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
        response.headers = {"content-type": "application/json"}
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

            assert result == MOCK_SUCCESS_RESPONSE
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
            assert result == MOCK_SUCCESS_RESPONSE
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
            assert result == MOCK_SUCCESS_RESPONSE
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
            assert result == MOCK_SUCCESS_RESPONSE
            mock_session = mock_client_session
            call_args = mock_session.request.call_args
            expected_url = (""
                "http://127.0.0.1/api/v1/resources/789/items?limit=10&category=technology&page=1&sort_by=date")
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
            assert result == MOCK_SUCCESS_RESPONSE
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
            assert result == MOCK_SUCCESS_RESPONSE
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
    """测试 RestfulApi 的异常场景"""

    @pytest.fixture
    def mock_card(self):
        """创建模拟的 RestfulApiCard"""
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
        """创建 RestfulApi 实例"""
        return RestfulApi(mock_card)

    @pytest.mark.asyncio
    async def test_invoke_timeout_error(self, restful_api, mock_card):
        """测试请求超时异常"""
        # 模拟超时异常
        with patch.object(restful_api, '_async_request',
                          side_effect=asyncio.TimeoutError("Request timeout")):
            with pytest.raises(Exception) as exc_info:
                await restful_api.invoke({})

            assert exc_info.value.code == StatusCode.TOOL_RESTFUL_API_TIMEOUT.code


    @pytest.mark.asyncio
    async def test_invoke_response_error(self, restful_api, mock_card):
        """测试 HTTP 响应错误异常"""
        # 模拟 aiohttp.ClientResponseError
        mock_response_error = aiohttp.ClientResponseError(
            request_info=Mock(),
            history=(),
            status=404,
            message="Not Found",
            headers={}
        )

        with patch.object(restful_api, '_async_request',
                          side_effect=mock_response_error):
            with pytest.raises(Exception) as exc_info:
                await restful_api.invoke({})

            assert exc_info.value.code == StatusCode.TOOL_RESTFUL_API_RESPONSE_ERROR.code

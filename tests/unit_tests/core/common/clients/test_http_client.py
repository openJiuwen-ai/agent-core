# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from typing import Dict
from unittest.mock import Mock, AsyncMock, patch
import pytest

import aiohttp
from aiohttp import ClientSession, ClientTimeout

from openjiuwen.core.common.clients import get_connector_pool_manager
from openjiuwen.core.common.clients.http_client import HttpSession, HttpSessionManager, SessionConfig, HttpClient


class TestSessionConfig:
    def test_custom_values(self):
        config = SessionConfig(
            timeout=30.0,
            connect_timeout=10.0,
            raise_for_status=True,
            headers={"User-Agent": "Test"},
            proxy="http://proxy:8080"
        )
        assert config.timeout == 30.0
        assert config.connect_timeout == 10.0
        assert config.raise_for_status is True
        assert config.headers == {"User-Agent": "Test"}
        assert config.proxy == "http://proxy:8080"

    def test_generate_key(self):
        """测试生成唯一键"""
        config1 = SessionConfig(
            timeout=30.0,
            headers={"User-Agent": "Test"},
        )

        config2 = SessionConfig(
            timeout=30.0,
            headers={"User-Agent": "Test"},
        )

        config3 = SessionConfig(
            timeout=60.0,
            headers={"User-Agent": "Test"},
        )
        assert config1.generate_key() == config2.generate_key()
        assert config1.generate_key() != config3.generate_key()

    def test_generate_key_with_complex_types(self):
        """测试复杂类型的键生成"""
        config = SessionConfig(
            headers={"b": "2", "a": "1"},  # 无序字典
            timeout_args={"sock_read": "30", "sock_connect": "10"}
        )
        key = config.generate_key()
        assert isinstance(key, str)
        assert len(key) > 0


class TestHttpSession:
    @pytest.fixture
    def mock_session(self):
        session = AsyncMock(spec=ClientSession)
        session.close.return_value = None
        return session

    @pytest.fixture
    def config(self):
        return SessionConfig(timeout=30.0)

    def test_init(self, mock_session, config):
        http_session = HttpSession(mock_session, config)
        assert http_session.session() == mock_session
        assert not http_session.closed
        assert http_session.ref_count == 1

    def test_session_method(self, mock_session, config):
        http_session = HttpSession(mock_session, config)
        result = http_session.session()
        assert result == mock_session

    @pytest.mark.asyncio
    async def test_session_method_closed(self, mock_session, config):
        """测试已经关闭的HttpSession, 获取内部session失败"""
        http_session = HttpSession(mock_session, config)
        await http_session.close()

        with pytest.raises(RuntimeError, match="Session is closed"):
            http_session.session()

    @pytest.mark.asyncio
    async def test_do_close(self, mock_session, config):
        mock_session.close.return_value = None
        http_session = HttpSession(mock_session, config)

        await http_session.close()
        mock_session.close.assert_called_once()


class TestHttpSessionManager:
    @pytest.fixture
    def manager(self):
        return HttpSessionManager()

    @pytest.fixture
    def config(self):
        return SessionConfig(timeout=30.0)

    @pytest.fixture
    def mock_connector_pool(self):
        mock_pool = AsyncMock()
        mock_conn = Mock()
        mock_pool.conn.return_value = mock_conn
        return mock_pool

    @pytest.mark.asyncio
    async def test_create_resource(self, manager, config, mock_connector_pool):
        with (patch('openjiuwen.core.common.clients.get_connector_pool_manager')
              as mock_get_pool_manager):
            mock_manager = Mock()
            mock_get_pool_manager.return_value = mock_manager
            mock_manager.get_connector_pool = AsyncMock(return_value=mock_connector_pool)

            with patch('openjiuwen.core.common.clients.http_client.ClientSession') as mock_client_session:
                mock_session = AsyncMock(spec=ClientSession)
                mock_client_session.return_value = mock_session
                http_session, _ = await manager.acquire(config)
                assert isinstance(http_session, HttpSession)
                mock_client_session.assert_called_once()
                await manager.release_session(config)

    @pytest.mark.asyncio
    async def test_acquire_new_session(self, manager, config, mock_connector_pool):
        with (patch('openjiuwen.core.common.clients.get_connector_pool_manager')
              as mock_get_pool_manager):
            mock_manager = Mock()
            mock_get_pool_manager.return_value = mock_manager
            mock_manager.get_connector_pool = AsyncMock(return_value=mock_connector_pool)

            with patch('openjiuwen.core.common.clients.http_client.ClientSession') as mock_client_session:
                mock_session = AsyncMock(spec=ClientSession)
                mock_client_session.return_value = mock_session

                session, is_new = await manager.acquire(config)

                assert is_new
                assert isinstance(session, HttpSession)
                await manager.release_session(config)

    @pytest.mark.asyncio
    async def test_acquire_existing_session(self, manager, config, mock_connector_pool):
        with (patch('openjiuwen.core.common.clients.get_connector_pool_manager')
              as mock_get_pool_manager):
            mock_manager = Mock()
            mock_get_pool_manager.return_value = mock_manager
            mock_manager.get_connector_pool = AsyncMock(return_value=mock_connector_pool)
            with patch('openjiuwen.core.common.clients.http_client.ClientSession') as mock_client_session:
                mock_session = AsyncMock(spec=ClientSession)
                mock_client_session.return_value = mock_session
                session1, is_new1 = await manager.acquire(config)
                assert is_new1
                session2, is_new2 = await manager.acquire(config)
                assert not is_new2
                assert session1 is session2

                await manager.release_session(config)
                await manager.release_session(config)

    @pytest.mark.asyncio
    async def test_release_session(self, manager, config, mock_connector_pool):
        with (patch('openjiuwen.core.common.clients.get_connector_pool_manager')
              as mock_get_pool_manager):
            mock_manager = Mock()
            mock_get_pool_manager.return_value = mock_manager
            mock_manager.get_connector_pool = AsyncMock(return_value=mock_connector_pool)

            with patch('openjiuwen.core.common.clients.http_client.ClientSession') as mock_client_session:
                mock_session = AsyncMock(spec=ClientSession)
                mock_client_session.return_value = mock_session

                session, _ = await manager.acquire(config)
                await manager.release_session(config)

                session2, _ = await manager.acquire(config)
                assert session is not session2
                await manager.release_session(config)

    @pytest.mark.asyncio
    async def test_get_session_context_manager(self, manager, config, mock_connector_pool):
        with (patch('openjiuwen.core.common.clients.get_connector_pool_manager')
              as mock_get_pool_manager):
            mock_manager = Mock()
            mock_get_pool_manager.return_value = mock_manager
            mock_manager.get_connector_pool = AsyncMock(return_value=mock_connector_pool)

            with patch('openjiuwen.core.common.clients.http_client.ClientSession') as mock_client_session:
                mock_session = AsyncMock(spec=ClientSession)
                mock_client_session.return_value = mock_session

                async with manager.get_session(config) as session:
                    assert isinstance(session, HttpSession)


class MockHttpClient(HttpClient):
    __client_type__ = "mock"

    async def acquire_session(self) -> HttpSession:
        return await self._acquire_session()

    async def release_session(self, session: HttpSession):
        return await self._release_session(session)

    def build_request_kwargs(self, **kwargs) -> Dict:
        return self._build_request_kwargs(**kwargs)


class TestHttpClient:
    @pytest.fixture
    def config(self):
        return SessionConfig(timeout=30.0, headers={"User-Agent": "Test"})

    @pytest.fixture
    def mock_session_manager(self):
        manager = AsyncMock(spec=HttpSessionManager)
        return manager

    @pytest.fixture
    def mock_http_session(self):
        mock_response = AsyncMock(spec=aiohttp.ClientResponse)

        # 设置response的属性和方法
        mock_response.status = 200
        mock_response.headers = {"Content-Type": "application/json"}
        mock_response.url = "http://example.com"
        mock_response.reason = "OK"
        mock_response.json = AsyncMock(return_value={"data": "test"})
        mock_response.text = AsyncMock(return_value="test")
        mock_response.read = AsyncMock(return_value=b"test")

        async def chunk_generator(**kwargs):
            yield b"chunk1"
            yield b"chunk2"

        # Set up the content mock
        mock_content = Mock()
        # When iter_chunked is called, return the async generator itself
        mock_content.iter_chunked.return_value = chunk_generator()

        mock_response.content = mock_content
        mock_response.__aenter__.return_value = mock_response

        mock_request_cm = AsyncMock()
        mock_request_cm.__aenter__.return_value = mock_response
        mock_request_cm.__aexit__.return_value = None
        # 设置session的request方法
        mock_client_session = AsyncMock(spec=ClientSession)
        mock_client_session.request.return_value = mock_request_cm

        session = Mock(spec=HttpSession)
        session.session.return_value = mock_client_session
        session.closed = False
        return session

    @pytest.mark.asyncio
    async def test_context_manager(self, config, mock_session_manager, mock_http_session):
        with patch('openjiuwen.core.common.clients.http_client.get_http_session_manager',
                   return_value=mock_session_manager):
            mock_session_manager.acquire = AsyncMock(return_value=(mock_http_session, True))
            mock_session_manager.release = AsyncMock()

            async with HttpClient(config) as client:
                assert not client.closed

            assert client.closed

    @pytest.mark.asyncio
    async def test_acquire_session_reusable(self, config, mock_session_manager, mock_http_session):
        with patch('openjiuwen.core.common.clients.http_client.get_http_session_manager',
                   return_value=mock_session_manager):
            mock_session_manager.acquire = AsyncMock(return_value=(mock_http_session, True))

            client = MockHttpClient(config, reuse_session=True)
            session1 = await client.acquire_session()
            session2 = await client.acquire_session()

            assert session1 is session2
            assert mock_session_manager.acquire.call_count == 1

    @pytest.mark.asyncio
    async def test_acquire_session_closed_client(self, config, mock_session_manager):
        with patch('openjiuwen.core.common.clients.http_client.get_http_session_manager',
                   return_value=mock_session_manager):
            client = MockHttpClient(config, reuse_session=True)
            await client.close()

            with pytest.raises(RuntimeError, match="HttpClient is closed"):
                await client.acquire_session()

    @pytest.mark.asyncio
    async def test_release_session_reusable(self, config, mock_session_manager, mock_http_session):
        """测试释放可重用会话"""
        with patch('openjiuwen.core.common.clients.http_client.get_http_session_manager',
                   return_value=mock_session_manager):
            mock_session_manager.acquire = AsyncMock(return_value=(mock_http_session, True))
            mock_session_manager.release = AsyncMock()

            client = MockHttpClient(config, reuse_session=True)
            session = await client.acquire_session()
            await client.release_session(session)

            # 可重用会话不应该立即释放
            mock_session_manager.release.assert_not_called()

    @pytest.mark.asyncio
    async def test_release_session_non_reusable(self, config, mock_session_manager, mock_http_session):
        """测试释放一次性会话"""
        with patch('openjiuwen.core.common.clients.http_client.get_http_session_manager',
                   return_value=mock_session_manager):
            mock_session_manager.acquire = AsyncMock(return_value=(mock_http_session, True))
            mock_session_manager.release = AsyncMock()

            client = MockHttpClient(config, reuse_session=False)
            session = await client.acquire_session()
            await client.release_session(session)

            mock_session_manager.release.assert_called_once_with(session)

    @pytest.mark.asyncio
    async def test_close(self, config, mock_session_manager, mock_http_session):
        """测试关闭客户端"""
        with patch('openjiuwen.core.common.clients.http_client.get_http_session_manager',
                   return_value=mock_session_manager):
            mock_session_manager.acquire = AsyncMock(return_value=(mock_http_session, True))
            mock_session_manager.release = AsyncMock()

            client = MockHttpClient(config, reuse_session=True)
            await client.acquire_session()

            await client.close()
            assert client.closed
            mock_session_manager.release.assert_called_once()

    @pytest.mark.asyncio
    async def test_build_request_kwargs(self, config):
        client = MockHttpClient(config)
        kwargs = client.build_request_kwargs()
        assert 'headers' in kwargs

        # 带headers
        kwargs = client.build_request_kwargs(headers={"X-Custom": "value"})
        assert kwargs['headers'] == {"X-Custom": "value", "User-Agent": "Test"}

        # 带timeout
        kwargs = client.build_request_kwargs(timeout=10.0)
        assert isinstance(kwargs['timeout'], ClientTimeout)
        assert kwargs['timeout'].total == 10.0

        # 带timeout_args
        kwargs = client.build_request_kwargs(timeout_args={"total": 20.0, "connect": 5.0})
        assert kwargs['timeout'].total == 20.0
        assert kwargs['timeout'].connect == 5.0

    @pytest.mark.asyncio
    async def test_request_success(self, config, mock_session_manager, mock_http_session):
        with patch('openjiuwen.core.common.clients.http_client.get_http_session_manager',
                   return_value=mock_session_manager):
            mock_session_manager.acquire = AsyncMock(return_value=(mock_http_session, True))
            mock_session_manager.release = AsyncMock()

            client = HttpClient(config)
            result = await client.get('http://example.com')

            assert result['code'] == 200
            assert result['data'] == {"data": "test"}
            assert result['url'] == "http://example.com"
            assert result['reason'] == "OK"

    @pytest.mark.asyncio
    async def test_request_with_chunked(self, config, mock_session_manager, mock_http_session):
        with patch('openjiuwen.core.common.clients.http_client.get_http_session_manager',
                   return_value=mock_session_manager):
            mock_session_manager.acquire = AsyncMock(return_value=(mock_http_session, True))
            mock_session_manager.release = AsyncMock()

            with patch('openjiuwen.core.common.clients.http_client.ParserRegistry') as mock_parser_registry:
                mock_parser = Mock()
                mock_parser.parse.return_value = "parsed_content"
                mock_parser_registry.return_value = mock_parser

                client = HttpClient(config)
                result = await client.get('http://example.com', chunked=True)

                assert result['code'] == 200
                mock_parser.parse.assert_called_once()

    @pytest.mark.asyncio
    async def test_request_with_size_limit(self, config, mock_session_manager, mock_http_session):
        """测试请求大小限制"""
        with patch('openjiuwen.core.common.clients.http_client.get_http_session_manager',
                   return_value=mock_session_manager):
            mock_session_manager.acquire = AsyncMock(return_value=(mock_http_session, True))
            mock_session_manager.release = AsyncMock()

            # 模拟大响应
            large_chunks = [b"x" * 1024 * 1024] * 20  # 20MB

            async def iter_chunks(*args, **kwargs):
                for chunk in large_chunks:
                    yield chunk

            response = mock_http_session.session.return_value.request.return_value.__aenter__.return_value
            response.content.iter_chunked.return_value = iter_chunks()
            client = HttpClient(config)
            with pytest.raises(ValueError, match="Response too large"):
                await client.get('http://example.com', chunked=True,
                                 response_bytes_size_limit=10 * 1024 * 1024)

    @pytest.mark.asyncio
    async def test_stream_request(self, config, mock_session_manager, mock_http_session):
        """测试流式请求"""
        with patch('openjiuwen.core.common.clients.http_client.get_http_session_manager',
                   return_value=mock_session_manager):
            mock_session_manager.acquire = AsyncMock(return_value=(mock_http_session, True))
            mock_session_manager.release = AsyncMock()

            client = HttpClient(config)
            chunks = []
            async for chunk in client.stream_get('http://example.com', chunked=True):
                chunks.append(chunk)

            assert len(chunks) == 2
            assert chunks[0] == b"chunk1"
            assert chunks[1] == b"chunk2"

    @pytest.mark.asyncio
    async def test_stream_request_with_callback(self, config, mock_session_manager, mock_http_session):
        """测试带回调的流式请求"""
        with patch('openjiuwen.core.common.clients.http_client.get_http_session_manager',
                   return_value=mock_session_manager):
            mock_session_manager.acquire = AsyncMock(return_value=(mock_http_session, True))
            mock_session_manager.release = AsyncMock()

            def sync_callback(data):
                return data.upper()

            client = HttpClient(config)
            chunks = []
            async for chunk in client.stream_get('http://example.com', chunked=True,
                                                 on_stream_received=sync_callback):
                chunks.append(chunk)
            assert chunks == [b"CHUNK1", b"CHUNK2"]

    @pytest.mark.asyncio
    async def test_stream_request_with_async_callback(self, config, mock_session_manager, mock_http_session):
        """测试带回调的流式请求"""
        with patch('openjiuwen.core.common.clients.http_client.get_http_session_manager',
                   return_value=mock_session_manager):
            mock_session_manager.acquire = AsyncMock(return_value=(mock_http_session, True))
            mock_session_manager.release = AsyncMock()

            async def async_callback(data):
                return data.upper()

            client = HttpClient(config)
            chunks = []
            async for chunk in client.stream_get('http://example.com', chunked=True,
                                                 on_stream_received=async_callback):
                chunks.append(chunk)
            assert chunks == [b"CHUNK1", b"CHUNK2"]

    @pytest.mark.asyncio
    async def test_http_methods(self, config, mock_session_manager, mock_http_session):
        """测试HTTP方法"""
        with patch('openjiuwen.core.common.clients.http_client.get_http_session_manager',
                   return_value=mock_session_manager):
            mock_session_manager.acquire = AsyncMock(return_value=(mock_http_session, True))
            mock_session_manager.release = AsyncMock()

            client = HttpClient(config)

            # GET
            result = await client.get('http://example.com', params={"q": "test"})
            assert result['code'] == 200

            # POST
            result = await client.post('http://example.com', body={"key": "value"})
            assert result['code'] == 200

            # PUT
            result = await client.put('http://example.com', body={"key": "value"})
            assert result['code'] == 200

            # DELETE
            result = await client.delete('http://example.com')
            assert result['code'] == 200

            # PATCH
            result = await client.patch('http://example.com', body={"key": "value"})
            assert result['code'] == 200

            # HEAD
            result = await client.head('http://example.com')
            assert result['code'] == 200

            # OPTIONS
            result = await client.options('http://example.com')
            assert result['code'] == 200

    @pytest.mark.asyncio
    async def test_is_closed(self, config):
        """测试is_closed方法"""
        client = HttpClient(config)
        assert not client.closed

        await client.close()
        assert client.closed


@pytest.mark.asyncio
class TestHttpClientIntegration:

    async def test_real_request_mock(self):
        """测试模拟的真实请求"""
        # 创建模拟响应
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.headers = {"Content-Type": "application/json"}
        mock_response.json = AsyncMock(return_value={"success": True})
        mock_response.__aenter__.return_value = mock_response

        # 创建模拟的request上下文管理器
        mock_request_cm = AsyncMock()
        mock_request_cm.__aenter__.return_value = mock_response
        mock_request_cm.__aexit__.return_value = None

        # 创建模拟session - request返回上下文管理器
        mock_session = Mock()
        mock_session.request.return_value = mock_request_cm

        # 创建模拟HttpSession
        mock_http_session = Mock()
        mock_http_session.session.return_value = mock_session
        mock_http_session.closed = False

        with patch('openjiuwen.core.common.clients.http_client.get_http_session_manager') as mock_get_manager:
            mock_manager = AsyncMock(spec=HttpSessionManager)
            mock_manager.acquire = AsyncMock(return_value=(mock_http_session, True))
            mock_get_manager.return_value = mock_manager
            client = HttpClient()
            result = await client.get("http://example.com")

            assert result["code"] == 200
            assert result["data"] == {"success": True}

    async def test_error_handling(self):
        """测试错误处理"""
        # 创建模拟的request上下文管理器，但request方法抛出异常
        mock_session = Mock()

        # 设置request方法抛出异常 - 返回的上下文管理器在__aenter__时抛出异常
        mock_request_cm = AsyncMock()
        mock_request_cm.__aenter__.side_effect = aiohttp.ClientError("Connection error")
        mock_session.request.return_value = mock_request_cm

        # 创建模拟HttpSession
        mock_http_session = Mock()
        mock_http_session.session.return_value = mock_session
        mock_http_session.closed = False

        with patch('openjiuwen.core.common.clients.http_client.get_http_session_manager') as mock_get_manager:
            mock_manager = AsyncMock(spec=HttpSessionManager)
            mock_manager.acquire = AsyncMock(return_value=(mock_http_session, True))
            mock_get_manager.return_value = mock_manager
            client = HttpClient()
            with pytest.raises(aiohttp.ClientError, match="Connection error"):
                await client.get("http://example.com")

    async def test_request_with_http_error(self):
        """测试HTTP错误响应"""
        # 创建模拟响应
        mock_response = AsyncMock()
        mock_response.status = 404
        mock_response.headers = {"Content-Type": "text/plain"}
        mock_response.reason = "Not Found"
        mock_response.text = AsyncMock(return_value="Not Found")
        mock_response.__aenter__.return_value = mock_response

        # 创建模拟的request上下文管理器
        mock_request_cm = AsyncMock()
        mock_request_cm.__aenter__.return_value = mock_response
        mock_request_cm.__aexit__.return_value = None

        # 创建模拟session
        mock_session = Mock()
        mock_session.request.return_value = mock_request_cm

        # 创建模拟HttpSession
        mock_http_session = Mock()
        mock_http_session.session.return_value = mock_session
        mock_http_session.closed = False

        with patch('openjiuwen.core.common.clients.http_client.get_http_session_manager') as mock_get_manager:
            mock_manager = AsyncMock(spec=HttpSessionManager)
            mock_manager.acquire = AsyncMock(return_value=(mock_http_session, True))
            mock_get_manager.return_value = mock_manager
            client = HttpClient()
            result = await client.get("http://example.com")

            assert result["code"] == 404
            assert result["data"] == "Not Found"
            assert result["reason"] == "Not Found"

    async def test_request_with_timeout(self):
        """测试请求超时"""
        # 创建模拟的request上下文管理器，在__aenter__时抛出超时异常
        mock_session = Mock()
        import asyncio
        mock_request_cm = AsyncMock()
        mock_request_cm.__aenter__.side_effect = asyncio.TimeoutError("Request timeout")
        mock_session.request.return_value = mock_request_cm

        # 创建模拟HttpSession
        mock_http_session = Mock()
        mock_http_session.session.return_value = mock_session
        mock_http_session.closed = False

        with patch('openjiuwen.core.common.clients.http_client.get_http_session_manager') as mock_get_manager:
            mock_manager = AsyncMock(spec=HttpSessionManager)
            mock_manager.acquire = AsyncMock(return_value=(mock_http_session, True))
            mock_get_manager.return_value = mock_manager
            client = HttpClient()
            with pytest.raises(asyncio.TimeoutError):
                await client.get("http://example.com")

    async def test_different_content_types(self):
        """测试不同的内容类型"""
        test_cases = [
            ("application/json", {"key": "value"}, "json"),
            ("text/plain", "Hello World", "text"),
            ("application/octet-stream", b"binary data", "read"),
        ]

        for content_type, content, method in test_cases:
            # 创建模拟响应
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.headers = {"Content-Type": content_type}

            if method == "json":
                mock_response.json = AsyncMock(return_value=content)
            elif method == "text":
                mock_response.text = AsyncMock(return_value=content)
            else:
                mock_response.read = AsyncMock(return_value=content)

            mock_response.__aenter__.return_value = mock_response

            # 创建模拟的request上下文管理器
            mock_request_cm = AsyncMock()
            mock_request_cm.__aenter__.return_value = mock_response
            mock_request_cm.__aexit__.return_value = None

            # 创建模拟session
            mock_session = Mock()
            mock_session.request.return_value = mock_request_cm

            # 创建模拟HttpSession
            mock_http_session = Mock()
            mock_http_session.session.return_value = mock_session
            mock_http_session.closed = False

            with patch('openjiuwen.core.common.clients.http_client.get_http_session_manager') as mock_get_manager:
                mock_manager = AsyncMock(spec=HttpSessionManager)
                mock_manager.acquire = AsyncMock(return_value=(mock_http_session, True))
                mock_get_manager.return_value = mock_manager
                client = HttpClient()
                result = await client.get("http://example.com")

                assert result["code"] == 200
                assert result["data"] == content

    async def test_session_release_on_error(self):
        """测试错误发生时释放会话"""
        # 创建模拟的request上下文管理器，在__aenter__时抛出异常
        mock_session = Mock()

        mock_request_cm = AsyncMock()
        mock_request_cm.__aenter__.side_effect = aiohttp.ClientError("Connection error")
        mock_session.request.return_value = mock_request_cm

        # 创建模拟HttpSession
        mock_http_session = Mock()
        mock_http_session.session.return_value = mock_session
        mock_http_session.closed = False

        with patch('openjiuwen.core.common.clients.http_client.get_http_session_manager') as mock_get_manager:
            mock_manager = AsyncMock(spec=HttpSessionManager)
            mock_manager.acquire = AsyncMock(return_value=(mock_http_session, True))
            mock_get_manager.return_value = mock_manager
            mock_release = AsyncMock()
            mock_manager.release = mock_release
            client = HttpClient(reuse_session=False)

            with pytest.raises(aiohttp.ClientError):
                await client.get("http://example.com")

            # 验证release被调用
            mock_release.assert_called_once_with(mock_http_session)

    async def test_concurrent_requests(self):
        """测试并发请求"""
        # 创建模拟响应
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.headers = {"Content-Type": "application/json"}
        mock_response.json = AsyncMock(return_value={"success": True})
        mock_response.__aenter__.return_value = mock_response

        # 创建模拟的request上下文管理器
        mock_request_cm = AsyncMock()
        mock_request_cm.__aenter__.return_value = mock_response
        mock_request_cm.__aexit__.return_value = None

        # 创建模拟session
        mock_session = Mock()
        mock_session.request.return_value = mock_request_cm

        # 创建模拟HttpSession
        mock_http_session = Mock()
        mock_http_session.session.return_value = mock_session
        mock_http_session.closed = False
        mock_http_session.increment_ref = Mock(return_value=2)
        mock_http_session.decrement_ref = Mock(return_value=False)

        with patch('openjiuwen.core.common.clients.http_client.get_http_session_manager') as mock_get_manager:
            mock_manager = AsyncMock(spec=HttpSessionManager)
            mock_manager.acquire = AsyncMock(return_value=(mock_http_session, True))
            mock_get_manager.return_value = mock_manager

            client = HttpClient(reuse_session=True)

            # 并发执行多个请求
            tasks = [client.get("http://example.com") for _ in range(5)]
            import asyncio
            results = await asyncio.gather(*tasks)

            assert len(results) == 5
            for result in results:
                assert result["code"] == 200

    async def test_custom_headers_and_params(self):
        """测试自定义headers和参数"""
        # 创建模拟响应
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.headers = {"Content-Type": "application/json"}
        mock_response.json = AsyncMock(return_value={"success": True})
        mock_response.__aenter__.return_value = mock_response

        # 创建模拟的request上下文管理器
        mock_request_cm = AsyncMock()
        mock_request_cm.__aenter__.return_value = mock_response
        mock_request_cm.__aexit__.return_value = None

        # 创建模拟session
        mock_session = Mock()
        mock_session.request.return_value = mock_request_cm

        # 创建模拟HttpSession
        mock_http_session = Mock()
        mock_http_session.session.return_value = mock_session
        mock_http_session.closed = False

        with patch('openjiuwen.core.common.clients.http_client.get_http_session_manager') as mock_get_manager:
            mock_manager = AsyncMock(spec=HttpSessionManager)
            mock_manager.acquire = AsyncMock(return_value=(mock_http_session, True))
            mock_get_manager.return_value = mock_manager

            client = HttpClient()

            # 测试自定义headers
            await client.get(
                "http://example.com",
                headers={"X-Custom": "value"},
                params={"q": "test"}
            )

            # 验证request被正确调用
            mock_session.request.assert_called_with(
                'GET',
                'http://example.com',
                headers={'X-Custom': 'value'},
                params={'q': 'test'}
            )

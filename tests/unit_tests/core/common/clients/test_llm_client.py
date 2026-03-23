# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from unittest.mock import AsyncMock, MagicMock, patch, ANY, Mock
import pytest

from openjiuwen.core.common.clients.llm_client import (
    HttpXConnectorPoolConfig,
    HttpXConnectorPool,
    create_httpx_client,
    create_async_openai_client,
    create_openai_client,
)
from openjiuwen.core.common.clients.client_registry import get_client_registry
from openjiuwen.core.foundation.llm import ModelClientConfig


class TestHttpXConnectorPoolConfig:
    """Test cases for HttpXConnectorPoolConfig."""

    def test_default_values(self):
        """Test default configuration values."""
        config = HttpXConnectorPoolConfig()
        assert config.max_keepalive_connections == 20
        assert config.local_address is None
        assert config.proxy is None
        assert config.limit == 100
        assert config.limit_per_host == 30
        assert config.ssl_verify is True

    def test_custom_values(self):
        """Test custom configuration values."""
        config = HttpXConnectorPoolConfig(
            max_keepalive_connections=50,
            local_address="192.168.1.100",
            proxy="http://proxy.example.com:8080",
            limit=200,
            limit_per_host=50,
            ssl_verify=False,
        )
        assert config.max_keepalive_connections == 50
        assert config.local_address == "192.168.1.100"
        assert config.proxy == "http://proxy.example.com:8080"
        assert config.limit == 200
        assert config.limit_per_host == 50
        assert config.ssl_verify is False

    def test_validation_positive_values(self):
        """Test validation of positive integer values."""
        # Should raise ValueError for non-positive values
        with pytest.raises(ValueError):
            HttpXConnectorPoolConfig(max_keepalive_connections=0)

        with pytest.raises(ValueError):
            HttpXConnectorPoolConfig(max_keepalive_connections=-5)

    def test_key_generation(self):
        """Test that configuration generates unique keys."""
        config1 = HttpXConnectorPoolConfig(
            max_keepalive_connections=20,
            proxy=None
        )
        config2 = HttpXConnectorPoolConfig(
            max_keepalive_connections=30,
            proxy=None
        )
        config3 = HttpXConnectorPoolConfig(
            max_keepalive_connections=20,
            proxy="http://proxy:8080"
        )

        assert config1.generate_key() != config2.generate_key()
        assert config1.generate_key() != config3.generate_key()
        assert config2.generate_key() != config3.generate_key()

        # Same config should generate same key
        config4 = HttpXConnectorPoolConfig(
            max_keepalive_connections=20,
            proxy=None
        )
        assert config1.generate_key() == config4.generate_key()


class TestHttpXConnectorPool:
    """Test cases for HttpXConnectorPool."""

    @pytest.fixture
    def mock_async_connection_pool(self):
        """Mock AsyncConnectionPool."""
        with patch('httpcore.AsyncConnectionPool') as mock_pool:
            mock_instance = AsyncMock()
            mock_pool.return_value = mock_instance
            yield mock_pool, mock_instance

    @pytest.fixture
    def basic_config(self):
        """Create a basic configuration."""
        return HttpXConnectorPoolConfig(
            limit=100,
            limit_per_host=30,
            max_keepalive_connections=20,
            keepalive_timeout=60,
        )

    @pytest.mark.asyncio
    async def test_initialization_basic(self, mock_async_connection_pool, basic_config):
        """Test basic initialization of HttpXConnectorPool."""
        mock_pool_class, mock_instance = mock_async_connection_pool

        pool = HttpXConnectorPool(basic_config)

        # Verify AsyncConnectionPool was created with correct arguments
        mock_pool_class.assert_called_once()
        call_kwargs = mock_pool_class.call_args[1]
        assert call_kwargs['max_connections'] == 100
        assert call_kwargs['max_keepalive_connections'] == 20
        assert call_kwargs['keepalive_expiry'] == 60
        assert 'proxy' not in call_kwargs or call_kwargs['proxy'] is None

        # Test conn() method
        assert pool.conn() == mock_instance

        # Test close
        await pool.close()

    @pytest.mark.asyncio
    async def test_initialization_with_proxy(self, mock_async_connection_pool):
        """Test initialization with proxy configuration."""
        mock_pool_class, mock_instance = mock_async_connection_pool

        config = HttpXConnectorPoolConfig(
            proxy="http://proxy.example.com:8080",
            ssl_verify=True
        )

        with patch('httpx.Proxy') as mock_proxy_class:
            mock_proxy = MagicMock()
            mock_proxy_class.return_value = mock_proxy

            pool = HttpXConnectorPool(config)

            mock_proxy_class.assert_called_once_with("http://proxy.example.com:8080")
            call_kwargs = mock_pool_class.call_args[1]
            assert call_kwargs['proxy'] == mock_proxy

    @pytest.mark.asyncio
    async def test_initialization_with_local_address(self, mock_async_connection_pool):
        """Test initialization with local address binding."""
        mock_pool_class, mock_instance = mock_async_connection_pool

        config = HttpXConnectorPoolConfig(
            local_address="192.168.1.100"
        )

        pool = HttpXConnectorPool(config)

        call_kwargs = mock_pool_class.call_args[1]
        assert call_kwargs['local_address'] == "192.168.1.100"

    @pytest.mark.asyncio
    async def test_initialization_with_ssl_context(self, mock_async_connection_pool):
        """Test initialization with SSL context."""
        mock_pool_class, mock_instance = mock_async_connection_pool

        config = HttpXConnectorPoolConfig(
            ssl_verify=True,
            ssl_cert="/path/to/cert.pem"
        )

        with patch('openjiuwen.core.common.security.ssl_utils.SslUtils.create_strict_ssl_context') as mock_ssl:
            mock_ssl_context = MagicMock()
            mock_ssl.return_value = mock_ssl_context

            pool = HttpXConnectorPool(config)

            mock_ssl.assert_called_once_with("/path/to/cert.pem")
            call_kwargs = mock_pool_class.call_args[1]
            assert call_kwargs['ssl_context'] == mock_ssl_context

    @pytest.mark.asyncio
    async def test_initialization_with_extend_params(self, mock_async_connection_pool):
        """Test initialization with extended parameters."""
        mock_pool_class, mock_instance = mock_async_connection_pool

        config = HttpXConnectorPoolConfig(
            extend_params={
                'http2': True,
                'ud': 'extra_param'
            }
        )

        pool = HttpXConnectorPool(config)

        call_kwargs = mock_pool_class.call_args[1]
        assert call_kwargs['http2'] is True
        assert call_kwargs['ud'] == 'extra_param'

    @pytest.mark.asyncio
    async def test_inherited_methods(self, basic_config):
        """Test inherited methods from ConnectorPool."""
        with patch('httpcore.AsyncConnectionPool'):
            pool = HttpXConnectorPool(basic_config)

            # Test is_expired
            assert not pool.is_expired()

            # Test stat
            stats = pool.stat()
            assert 'closed' in stats
            assert 'created_at' in stats['ref_detail']


class TestCreateHttpxClient:
    """Test cases for create_httpx_client factory function."""

    @pytest.fixture
    def mock_connector_pool_manager(self):
        """Mock connector_pool_manager.get_connector_pool."""
        with (patch('openjiuwen.core.common.clients.llm_client.get_connector_pool_manager')
              as mock_get_pool_manager):
            mock_manager = MagicMock()
            mock_get_pool_manager.return_value = mock_manager
            # Create a mock pool that's NOT an AsyncMock for the conn() method
            mock_pool = MagicMock()  # Use MagicMock instead of AsyncMock
            mock_pool.conn.return_value = MagicMock()  # conn() returns a MagicMock

            # Make get_connector_pool async but return the sync mock
            mock_manager.get_connector_pool = AsyncMock(return_value=mock_pool)
            yield mock_manager, mock_pool

    @pytest.mark.asyncio
    async def test_create_sync_client_with_dict_config(self, mock_connector_pool_manager):
        """Test creating synchronous client with dictionary configuration."""
        mock_manager, mock_pool = mock_connector_pool_manager

        config_dict = {
            "proxy": "http://proxy:8080",
            "ssl_verify": False,
            "limit": 200
        }

        # Mock httpx.Client directly
        with patch('httpx.Client') as mock_httpx_client:
            mock_client_instance = MagicMock()
            mock_httpx_client.return_value = mock_client_instance

            client = await create_httpx_client(config_dict, need_async=False)

            # Verify connector pool was requested
            mock_manager.get_connector_pool.assert_called_once_with(
                "httpx",
                config=ANY
            )

            # Verify config was converted to HttpXConnectorPoolConfig
            call_args = mock_manager.get_connector_pool.call_args[1]['config']
            assert isinstance(call_args, HttpXConnectorPoolConfig)
            assert call_args.proxy == "http://proxy:8080"
            assert call_args.ssl_verify is False
            assert call_args.limit == 200

            # Verify client was created with transport - now this will be a MagicMock, not a coroutine
            mock_httpx_client.assert_called_once_with(
                transport=mock_pool.conn.return_value,
                verify=False,
                proxy="http://proxy:8080"
            )
            assert client == mock_client_instance

    @pytest.mark.asyncio
    async def test_create_async_client_with_config_object(self, mock_connector_pool_manager):
        """Test creating asynchronous client with HttpXConnectorPoolConfig object."""
        mock_manager, mock_pool = mock_connector_pool_manager

        config_obj = HttpXConnectorPoolConfig(
            proxy="http://proxy:8080",
            ssl_verify=False,
            max_keepalive_connections=50
        )

        # Mock httpx.AsyncClient directly
        with patch('httpx.AsyncClient') as mock_async_client:
            mock_client_instance = MagicMock()
            mock_async_client.return_value = mock_client_instance

            client = await create_httpx_client(config_obj, need_async=True)

            # Verify connector pool was requested with same config object
            mock_manager.get_connector_pool.assert_called_once_with(
                "httpx",
                config=config_obj
            )

            # Verify async client was created - now this will be a MagicMock, not a coroutine
            mock_async_client.assert_called_once_with(
                transport=mock_pool.conn.return_value,
                verify=False,
                proxy="http://proxy:8080"
            )
            assert client == mock_client_instance


class TestCreateOpenAIClients:
    """Test cases for OpenAI client factory functions."""

    @pytest.fixture
    def mock_create_httpx_client(self):
        """Mock create_httpx_client function."""
        with patch('openjiuwen.core.common.clients.llm_client.create_httpx_client') as mock:
            mock_async_http_client = MagicMock()
            mock_sync_http_client = MagicMock()

            async def async_side_effect(config, need_async=False):
                if need_async:
                    return mock_async_http_client
                return mock_sync_http_client

            mock.side_effect = async_side_effect
            yield mock, mock_async_http_client, mock_sync_http_client

    @pytest.fixture
    def mock_openai(self):
        """Mock openai module."""
        with patch('openai.OpenAI') as mock_openai, \
                patch('openai.AsyncOpenAI') as mock_async_openai:
            mock_sync_instance = MagicMock()
            mock_async_instance = MagicMock()
            mock_openai.return_value = mock_sync_instance
            mock_async_openai.return_value = mock_async_instance

            yield {
                'OpenAI': mock_openai,
                'AsyncOpenAI': mock_async_openai,
                'sync_instance': mock_sync_instance,
                'async_instance': mock_async_instance
            }

    @pytest.fixture
    def mock_url_utils(self):
        """Mock UrlUtils.get_global_proxy_url."""
        with patch('openjiuwen.core.common.clients.llm_client.UrlUtils.get_global_proxy_url') as mock:
            mock.return_value = "http://global-proxy:8080"
            yield mock

    @pytest.mark.asyncio
    async def test_create_async_openai_client_with_dict_config(self, mock_create_httpx_client, mock_openai,
                                                               mock_url_utils):
        """Test creating async OpenAI client with dictionary configuration."""
        mock_httpx, mock_async_http, mock_sync_http = mock_create_httpx_client

        config_dict = {
            "api_key": "test-api-key",
            "api_base": "https://api.openai.com/v1",
            "timeout": 30,
            "max_retries": 3,
            "verify_ssl": True,
            "ssl_cert": "/path/to/cert.pem",
            "client_provider": "openai"
        }

        client = await create_async_openai_client(config_dict, extra_param="value")

        # Verify URL utils was called
        mock_url_utils.assert_called_once_with("https://api.openai.com/v1")

        # Verify httpx client was created with correct config
        mock_httpx.assert_called_once()
        call_args = mock_httpx.call_args
        assert call_args[1]['need_async'] is True
        assert call_args[1]['config']['proxy'] == "http://global-proxy:8080"
        assert call_args[1]['config']['ssl_verify'] is True
        assert call_args[1]['config']['ssl_cert'] == "/path/to/cert.pem"
        assert call_args[1]['config']['extra_param'] == "value"

        # Verify AsyncOpenAI was created correctly
        mock_openai['AsyncOpenAI'].assert_called_once_with(
            api_key="test-api-key",
            base_url="https://api.openai.com/v1",
            http_client=mock_async_http,
            timeout=30,
            max_retries=3
        )
        assert client == mock_openai['async_instance']

    @pytest.mark.asyncio
    async def test_create_sync_openai_client_with_config_object(self, mock_create_httpx_client, mock_openai,
                                                                mock_url_utils):
        """Test creating sync OpenAI client with ModelClientConfig object."""
        mock_httpx, mock_async_http, mock_sync_http = mock_create_httpx_client

        config_obj = ModelClientConfig(
            api_key="test-api-key",
            api_base="https://api.openai.com/v1",
            timeout=30,
            max_retries=3,
            verify_ssl=False,
            client_provider="openai"
        )

        client = await create_openai_client(config_obj)

        # Verify httpx client was created with correct config
        mock_httpx.assert_called_once()

        # Verify OpenAI was created correctly
        mock_openai['OpenAI'].assert_called_once_with(
            api_key="test-api-key",
            base_url="https://api.openai.com/v1",
            http_client=mock_sync_http,
            timeout=30,
            max_retries=3
        )
        assert client == mock_openai['sync_instance']

    @pytest.mark.asyncio
    async def test_openai_client_without_proxy(self, mock_create_httpx_client, mock_openai, mock_url_utils):
        """Test OpenAI client creation when no proxy is available."""
        mock_url_utils.return_value = None
        mock_httpx, mock_async_http, mock_sync_http = mock_create_httpx_client

        config_dict = {
            "api_key": "test-api-key",
            "api_base": "https://api.openai.com/v1",
            "client_provider": "openai"
        }

        client = await create_async_openai_client(config_dict)

        # Verify httpx client was created without proxy
        call_args = mock_httpx.call_args
        assert call_args[1]['config']['proxy'] is None

    def test_registration_with_client_registry(self):
        """Test that OpenAI client factories are properly registered."""
        # Check if factories are registered
        registered_clients = get_client_registry().list_clients()
        factory_names = [name for name in registered_clients]
        assert any('async_openai' in name for name in factory_names)
        assert any('openai' in name for name in factory_names)


class TestIntegration:
    """Integration tests for llm_client with registry and pool manager."""

    @pytest.mark.asyncio
    async def test_get_httpx_client_via_registry(self):
        """Test getting httpx client through the client registry."""
        # This test verifies that the factory works when called through the registry
        config = HttpXConnectorPoolConfig(proxy="http://proxy:8080", ssl_verify=False)

        # Mock the connector pool manager to avoid actual connection creation
        with (patch('openjiuwen.core.common.clients.llm_client.get_connector_pool_manager') as mock_get_pool_manager):
            pool_manager = MagicMock()
            mock_get_pool_manager.return_value = pool_manager
            # Create a regular MagicMock for the pool, not AsyncMock
            mock_pool = MagicMock()  # Changed from AsyncMock to MagicMock
            mock_transport = MagicMock()
            mock_pool.conn.return_value = mock_transport
            pool_manager.get_connector_pool = AsyncMock(return_value=mock_pool)

            with patch('httpx.Client') as mock_httpx_client:
                mock_client_instance = MagicMock()
                mock_httpx_client.return_value = mock_client_instance

                # Get client through registry
                client = await get_client_registry().get_client(
                    "httpx",
                    client_type="common",
                    config=config,
                    need_async=False
                )

                # Verify the factory was called correctly
                mock_get_pool_manager.assert_called_once()
                mock_httpx_client.assert_called_once_with(
                    transport=mock_transport,  # Use the stored transport mock
                    verify=False,
                    proxy='http://proxy:8080'
                )

    @pytest.mark.asyncio
    async def test_connector_pool_lifecycle(self):
        """Test connector pool lifecycle with OpenAI client."""
        # This test verifies that connector pools are properly managed
        config_dict = {
            "api_key": "test-key",
            "api_base": "https://api.openai.com/v1",
            "proxy": "http://proxy:8080",
            "client_provider": "openai",
            "verify_ssl": False
        }

        with patch('openjiuwen.core.common.clients.llm_client.get_connector_pool_manager') as get_mock_manager, \
                patch('openjiuwen.core.common.clients.llm_client.UrlUtils.get_global_proxy_url') as mock_url, \
                patch('httpx.Client') as mock_httpx_client, \
                patch('openai.OpenAI') as mock_openai:
            mock_manager = MagicMock()
            get_mock_manager.return_value = mock_manager
            mock_url.return_value = "http://proxy:8080"

            # Create a regular MagicMock for the pool
            mock_pool = MagicMock()  # Changed from AsyncMock to MagicMock
            mock_transport = MagicMock()
            mock_pool.conn.return_value = mock_transport
            mock_manager.get_connector_pool = AsyncMock(return_value=mock_pool)

            # Mock httpx client
            mock_httpx_instance = MagicMock()
            mock_httpx_client.return_value = mock_httpx_instance

            # Mock OpenAI
            mock_openai_instance = MagicMock()
            mock_openai.return_value = mock_openai_instance

            # Create client
            client = await create_openai_client(config_dict)

            # Verify connector pool was acquired
            mock_manager.get_connector_pool.assert_called_once()

            # Verify httpx client was created with the transport from mock_pool.conn.return_value
            mock_httpx_client.assert_called_once_with(
                transport=mock_transport,  # Use the stored transport mock
                verify=False,
                proxy='http://proxy:8080'
            )

            # Verify OpenAI client was created
            mock_openai.assert_called_once_with(
                api_key="test-key",
                base_url="https://api.openai.com/v1",
                http_client=mock_httpx_instance,
                timeout=60,
                max_retries=3
            )


class TestErrorCases:
    """Test error cases for llm_client."""

    @pytest.mark.asyncio
    async def test_invalid_config_type(self):
        """Test handling of invalid configuration types."""
        with pytest.raises(Exception):  # Pydantic will raise validation error
            await create_httpx_client("invalid_config")  # type: ignore

    @pytest.mark.asyncio
    async def test_connector_pool_creation_failure(self):
        """Test handling of connector pool creation failure."""
        with patch('openjiuwen.core.common.clients.llm_client.get_connector_pool_manager') as mock_get_manager:
            mock_manager = MagicMock()
            mock_get_manager.return_value = mock_manager
            mock_manager.get_connector_pool = AsyncMock(side_effect=Exception("Pool creation failed"))

            config = HttpXConnectorPoolConfig()

            with pytest.raises(Exception, match="Pool creation failed"):
                await create_httpx_client(config)

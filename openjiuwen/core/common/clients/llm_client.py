# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from typing import TYPE_CHECKING, Any, Dict, NoReturn, Optional, Union
from pydantic import Field

from openjiuwen.core.common.clients.connector_pool import get_connector_pool_manager
from openjiuwen.core.common.security.url_utils import UrlUtils
from openjiuwen.core.common.clients.client_registry import get_client_registry
from openjiuwen.core.common.clients.connector_pool import ConnectorPool, ConnectorPoolConfig
from openjiuwen.core.common.utils.header_utils import sanitize_headers

if TYPE_CHECKING:
    import httpx
    from openai import AsyncOpenAI, OpenAI
    from openjiuwen.core.foundation.llm import ModelClientConfig


class HttpXConnectorPoolConfig(ConnectorPoolConfig):
    """
    Configuration class for HTTPX connector pool.

    Extends the base ConnectorPoolConfig with HTTPX-specific settings
    for connection pooling and network configuration.
    """

    max_keepalive_connections: int = Field(
        default=20,
        description="Maximum number of keep-alive connections to maintain in the pool. "
                    "These connections are kept open for reuse, reducing connection establishment overhead.",
        ge=1
    )

    local_address: Optional[str] = Field(
        default=None,
        description="Local IP address or hostname to bind to for outgoing connections. "
                    "Useful for multi-homed systems or when a specific network interface is required."
    )

    proxy: Optional[str] = Field(
        default=None,
        description="Proxy server URL to route HTTP requests through"
    )

    need_async: bool = Field(
        default=True,
        description="Enable asynchronous mode for the connector pool. When set to True, "
                    "the pool will use async HTTPX client methods, allowing for concurrent "
                    "request handling with asyncio."
    )


@get_connector_pool_manager().register('httpx')
class HttpXConnectorPool(ConnectorPool):
    """
    HTTPX-based connector pool implementation.

    This class provides a connection pool using HTTPX's AsyncConnectionPool,
    enabling efficient connection reuse for HTTP/1.1 and HTTP/2 requests.
    It integrates with the global connector pool manager for resource sharing.
    """

    def __init__(self, config: HttpXConnectorPoolConfig):
        """
        Initialize the HTTPX connector pool.

        Args:
            config: Configuration object containing pool settings
                   (connection limits, timeouts, proxy settings, etc.)
        """
        super().__init__(config)
        import httpx
        from httpcore import ConnectionPool, AsyncConnectionPool

        # Build connection pool arguments from configuration
        pool_kwargs = {
            'max_connections': config.limit,
            'max_keepalive_connections': config.max_keepalive_connections,
            'keepalive_expiry': config.keepalive_timeout,
            'local_address': config.local_address,
            'ssl_context': config.create_ssl_context(),
            'proxy': httpx.Proxy(config.proxy) if config.proxy else None,
            **config.extend_params
        }
        # Remove None values to avoid passing invalid arguments
        pool_kwargs = {k: v for k, v in pool_kwargs.items() if v is not None}
        if config.need_async:
            self._conn = AsyncConnectionPool(**pool_kwargs)
        else:
            self._conn = ConnectionPool(**pool_kwargs)

    async def _do_close(self) -> None:
        """
        Perform actual cleanup of the connection pool.

        Note: HTTPX's AsyncConnectionPool doesn't require explicit closing
        as it handles cleanup through context managers and garbage collection.
        This method is kept as a no-op for interface compatibility.
        """
        return

    def conn(self) -> Any:
        """
        Get the underlying connection pool instance.

        Returns:
            Any: The HTTPX AsyncConnectionPool instance
        """
        return self._conn


_HTTPX_ERROR_NAMES = frozenset({
    "ConnectTimeout", "ReadTimeout", "WriteTimeout", "PoolTimeout",
    "ConnectError", "ReadError", "WriteError", "NetworkError",
    "ProxyError", "UnsupportedProtocol", "ProtocolError",
    "LocalProtocolError", "RemoteProtocolError", "TimeoutException",
})


def _raise_as_httpx_error(exc: BaseException, request: Any) -> NoReturn:
    """Re-raise an httpcore failure as the httpx error type callers expect."""
    import httpx

    error_name = type(exc).__name__
    if error_name in _HTTPX_ERROR_NAMES:
        raise getattr(httpx, error_name)(str(exc), request=request) from exc
    raise exc


def _httpx_transport(pool: Any, need_async: bool) -> Any:
    """Wrap the shared httpcore pool in the transport interface httpx expects.

    An httpx client calls ``handle_request`` / ``handle_async_request`` with an
    ``httpx.Request`` and expects an ``httpx.Response``; an httpcore pool takes
    an ``httpcore.Request`` / ``httpcore.Response`` instead.  Passing the pool
    itself as ``transport=`` made the first request fail with
    ``AttributeError: 'str' object has no attribute 'decode'``.

    The pool stays owned (and closed) by the ConnectorPoolManager, so closing
    the transport is a no-op.
    """
    import httpcore
    import httpx

    def _core_request(request: Any) -> Any:
        return httpcore.Request(
            method=request.method,
            url=httpcore.URL(
                scheme=request.url.raw_scheme,
                host=request.url.raw_host,
                port=request.url.port,
                target=request.url.raw_path,
            ),
            headers=request.headers.raw,
            content=request.stream,
            extensions=request.extensions,
        )

    httpcore_errors = (
        httpcore.NetworkError,
        httpcore.TimeoutException,
        httpcore.ProtocolError,
        httpcore.ProxyError,
        httpcore.UnsupportedProtocol,
    )

    class _SyncStream(httpx.SyncByteStream):
        def __init__(self, core_stream: Any):
            self._core_stream = core_stream

        def __iter__(self) -> Any:
            try:
                for part in self._core_stream:
                    yield part
            except httpcore_errors as exc:
                _raise_as_httpx_error(exc, None)

        def close(self) -> None:
            close = getattr(self._core_stream, "close", None)
            if close is not None:
                close()

    class _AsyncStream(httpx.AsyncByteStream):
        def __init__(self, core_stream: Any):
            self._core_stream = core_stream

        async def __aiter__(self) -> Any:
            try:
                async for part in self._core_stream:
                    yield part
            except httpcore_errors as exc:
                _raise_as_httpx_error(exc, None)

        async def aclose(self) -> None:
            aclose = getattr(self._core_stream, "aclose", None)
            if aclose is not None:
                await aclose()

    def _httpx_response(core_response: Any, request: Any, async_stream: bool) -> Any:
        stream = _AsyncStream(core_response.stream) if async_stream else _SyncStream(core_response.stream)
        return httpx.Response(
            status_code=core_response.status,
            headers=core_response.headers,
            stream=stream,
            extensions=core_response.extensions,
            request=request,
        )

    if need_async:
        class _AsyncPoolTransport(httpx.AsyncBaseTransport):
            def __init__(self, httpcore_pool: Any):
                self._pool = httpcore_pool

            async def handle_async_request(self, request: Any) -> Any:
                try:
                    core_response = await self._pool.handle_async_request(_core_request(request))
                except httpcore_errors as exc:
                    _raise_as_httpx_error(exc, request)
                return _httpx_response(core_response, request, async_stream=True)

            async def aclose(self) -> None:
                return

        return _AsyncPoolTransport(pool)

    class _SyncPoolTransport(httpx.BaseTransport):
        def __init__(self, httpcore_pool: Any):
            self._pool = httpcore_pool

        def handle_request(self, request: Any) -> Any:
            try:
                core_response = self._pool.handle_request(_core_request(request))
            except httpcore_errors as exc:
                _raise_as_httpx_error(exc, request)
            return _httpx_response(core_response, request, async_stream=False)

        def close(self) -> None:
            return

    return _SyncPoolTransport(pool)


@get_client_registry().register_client("httpx")
async def create_httpx_client(config: Union[HttpXConnectorPoolConfig, Dict[str, Any]],
                              need_async: bool = False) -> Union['httpx.Client', 'httpx.AsyncClient']:
    """
    Create an HTTPX client with connection pooling.

    This factory function creates either synchronous or asynchronous HTTPX clients
    that share a common connection pool managed by the global connector pool manager.

    Args:
        config: Configuration for the connection pool. Can be either:
               - HttpXConnectorPoolConfig instance
               - Dictionary with configuration parameters
        need_async: If True, returns an async client; otherwise returns a sync client

    Returns:
        Union[httpx.Client, httpx.AsyncClient]: Configured HTTPX client instance

    Example:
        # Create sync client
        client = create_httpx_client({"proxy": "http://proxy:8080"})

        # Create async client
        async_client = create_httpx_client(config, need_async=True)
    """
    import httpx
    if not isinstance(config, HttpXConnectorPoolConfig):
        config["need_async"] = need_async
        config = HttpXConnectorPoolConfig(**config)
    if config.need_async != need_async:
        config = config.model_copy(update={"need_async": need_async})
    connector_pool = await get_connector_pool_manager().get_connector_pool("httpx", config=config)
    transport = _httpx_transport(connector_pool.conn(), need_async)

    if need_async:
        return httpx.AsyncClient(transport=transport)
    return httpx.Client(transport=transport)


@get_client_registry().register_client("async_openai")
async def create_async_openai_client(config: Union["ModelClientConfig", Dict[str, Any]],
                                     **kwargs) -> 'AsyncOpenAI':
    """
    Create an asynchronous OpenAI client with proper HTTP connection management.

    This factory function creates an AsyncOpenAI client configured with a shared
    HTTPX connection pool for efficient connection reuse and proxy support.

    Args:
        config: Configuration for the OpenAI client. Can be either:
               - ModelClientConfig instance
               - Dictionary with configuration parameters
        **kwargs: Additional keyword arguments passed to the HTTPX client configuration

    Returns:
        AsyncOpenAI: Configured asynchronous OpenAI client instance

    Example:
        config = {
            "api_key": "sk-...",
            "api_base": "https://api.openai.com/v1",
            "timeout": 30
        }
        client = create_async_openai_client(config)

        # Use with async context
        response = await client.chat.completions.create(...)
    """
    from openai import AsyncOpenAI
    from openjiuwen.core.foundation.llm import ModelClientConfig
    # Normalize configuration to ModelClientConfig
    if not isinstance(config, ModelClientConfig):
        config = ModelClientConfig(**config)

    # Create HTTPX client with connection pooling
    httpx_client = await create_httpx_client(
        config=dict(
            proxy=UrlUtils.get_global_proxy_url(config.api_base),
            ssl_verify=config.verify_ssl,
            ssl_cert=config.ssl_cert,
            **kwargs
        ),
        need_async=True
    )

    openai_kwargs = dict(
        api_key=config.api_key,
        base_url=config.api_base,
        http_client=httpx_client,
        timeout=config.timeout,
        max_retries=config.max_retries,
    )
    if custom_headers := sanitize_headers(getattr(config, "custom_headers", None)):
        openai_kwargs["default_headers"] = custom_headers

    return AsyncOpenAI(**openai_kwargs)


@get_client_registry().register_client("openai")
async def create_openai_client(config: Union["ModelClientConfig", Dict[str, Any]],
                               **kwargs) -> 'OpenAI':
    """
    Create a synchronous OpenAI client with proper HTTP connection management.

    This factory function creates an OpenAI client configured with a shared
    HTTPX connection pool for efficient connection reuse and proxy support.

    Args:
        config: Configuration for the OpenAI client. Can be either:
               - ModelClientConfig instance
               - Dictionary with configuration parameters
        **kwargs: Additional keyword arguments passed to the HTTPX client configuration

    Returns:
        OpenAI: Configured synchronous OpenAI client instance

    Example:
        config = {
            "api_key": "sk-...",
            "api_base": "https://api.openai.com/v1",
            "timeout": 30
        }
        client = create_openai_client(config)

        # Use for synchronous requests
        response = client.chat.completions.create(...)
    """
    from openai import OpenAI
    from openjiuwen.core.foundation.llm import ModelClientConfig

    # Normalize configuration to ModelClientConfig
    if not isinstance(config, ModelClientConfig):
        config = ModelClientConfig(**config)

    # Create HTTPX client with connection pooling
    httpx_client = await create_httpx_client(
        config=dict(
            proxy=UrlUtils.get_global_proxy_url(config.api_base),
            ssl_verify=config.verify_ssl,
            ssl_cert=config.ssl_cert,
            **kwargs
        ),
        need_async=False
    )

    openai_kwargs = dict(
        api_key=config.api_key,
        base_url=config.api_base,
        http_client=httpx_client,
        timeout=config.timeout,
        max_retries=config.max_retries,
    )
    if custom_headers := sanitize_headers(getattr(config, "custom_headers", None)):
        openai_kwargs["default_headers"] = custom_headers

    return OpenAI(**openai_kwargs)

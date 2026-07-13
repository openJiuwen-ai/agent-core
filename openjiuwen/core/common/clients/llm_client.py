# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from typing import TYPE_CHECKING, Any, Dict, Optional, Union
from pydantic import Field

from openjiuwen.core.common.clients.connector_pool import get_connector_pool_manager
from openjiuwen.core.common.security.url_utils import UrlUtils
from openjiuwen.core.common.clients.client_registry import get_client_registry
from openjiuwen.core.common.clients.connector_pool import ConnectorPool, ConnectorPoolConfig
from openjiuwen.core.common.utils.header_utils import sanitize_headers

if TYPE_CHECKING:
    import httpx
    from anthropic import AsyncAnthropic
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

    This class caches an HTTPX transport (``AsyncHTTPTransport`` /
    ``HTTPTransport``) per config. The transport owns an internal
    ``httpcore`` connection pool, enabling efficient connection reuse for
    HTTP/1.1 and HTTP/2 requests. It integrates with the global connector
    pool manager for resource sharing.
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

        # Build a proper httpx transport, not a raw ``httpcore`` pool. httpx
        # cannot drive a bare ``httpcore.AsyncConnectionPool`` as its
        # ``transport=``: it would skip the httpx<->httpcore request/response
        # conversion and every request would fail (surfaced as a connection
        # error). The transport owns its own internal httpcore pool, so caching
        # one transport per config via ``ConnectorPoolManager`` is what shares
        # connections across calls.
        transport_kwargs = {
            'verify': config.create_ssl_context(),
            'proxy': config.proxy,
            'limits': httpx.Limits(
                max_connections=config.limit,
                max_keepalive_connections=config.max_keepalive_connections,
                keepalive_expiry=config.keepalive_timeout,
            ),
            'local_address': config.local_address,
            **config.extend_params,
        }
        # Drop None values so we leave httpx defaults intact (e.g. proxy=None).
        transport_kwargs = {k: v for k, v in transport_kwargs.items() if v is not None}
        if config.need_async:
            self._conn = httpx.AsyncHTTPTransport(**transport_kwargs)
        else:
            self._conn = httpx.HTTPTransport(**transport_kwargs)

    async def _do_close(self) -> None:
        """
        Perform actual cleanup of the connection pool.

        Note: an HTTPX transport doesn't require explicit closing as it
        handles cleanup through context managers and garbage collection.
        This method is kept as a no-op for interface compatibility.
        """
        return

    def conn(self) -> Any:
        """
        Get the underlying connection pool instance.

        Returns:
            Any: The shared HTTPX transport instance (AsyncHTTPTransport/HTTPTransport)
        """
        return self._conn


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
    # Shared, process-lifetime transport: never released per call, so don't bump
    # the ref count on every request (keeps it at 1 for the pool's lifetime).
    connector_pool = await get_connector_pool_manager().get_connector_pool(
        "httpx", config=config, increment_ref=False)

    # The shared transport already owns SSL and proxy, so we must NOT pass
    # verify=/proxy= here — doing so would make httpx build a *second*
    # transport and ignore the shared one (defeating reuse).
    if need_async:
        return httpx.AsyncClient(transport=connector_pool.conn())
    else:
        return httpx.Client(transport=connector_pool.conn())


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


@get_client_registry().register_client("async_anthropic")
async def create_async_anthropic_client(config: Union["ModelClientConfig", Dict[str, Any]],
                                        *,
                                        base_url: Optional[str] = None,
                                        **kwargs) -> 'AsyncAnthropic':
    """
    Create an asynchronous Anthropic client with proper HTTP connection management.

    Mirrors :func:`create_async_openai_client`: the underlying HTTPX transport is
    drawn from the shared ``ConnectorPoolManager`` (keyed by ssl/proxy/pool config)
    so connections are reused across calls instead of being re-established per
    request. ``base_url`` may be supplied to apply Anthropic-specific
    normalization (e.g. stripping a trailing ``/v1``); when omitted the config's
    ``api_base`` is used as-is.

    Args:
        config: Configuration for the Anthropic client. Can be either:
               - ModelClientConfig instance
               - Dictionary with configuration parameters
        base_url: Optional override for the Anthropic ``base_url`` (after
                 provider-specific normalization).
        **kwargs: Additional keyword arguments passed to the HTTPX client configuration

    Returns:
        AsyncAnthropic: Configured asynchronous Anthropic client instance
    """
    from anthropic import AsyncAnthropic
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

    anthropic_kwargs = dict(
        api_key=config.api_key,
        base_url=base_url or config.api_base,
        http_client=httpx_client,
        timeout=config.timeout,
        max_retries=config.max_retries,
    )
    # Parity with the OpenAI factory: surface sanitized config-level custom
    # headers as SDK default_headers.
    if custom_headers := sanitize_headers(getattr(config, "custom_headers", None)):
        anthropic_kwargs["default_headers"] = custom_headers

    return AsyncAnthropic(**anthropic_kwargs)


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

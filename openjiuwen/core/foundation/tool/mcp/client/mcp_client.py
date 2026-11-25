# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import asyncio
from abc import abstractmethod
from typing import Any, Generator, List, NoReturn, Optional

from openjiuwen.core.common.clients import BaseClient
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.tool.mcp.base import NO_TIMEOUT, McpServerConfig
from openjiuwen.core.foundation.tool.mcp.errors import McpConnectionError

_CANCEL_SCOPE_MARKERS = (
    "cancel scope",
    "cancel-scope",
    "different task",
)


def _iter_exception_leaves(error: BaseException) -> Generator[BaseException, None, None]:
    if isinstance(error, BaseExceptionGroup):
        for child in error.exceptions:
            yield from _iter_exception_leaves(child)
    else:
        yield error


def _is_cancel_scope_error(error: BaseException) -> bool:
    return isinstance(error, RuntimeError) and any(
        marker in str(error).lower() for marker in _CANCEL_SCOPE_MARKERS
    )


def raise_mcp_operation_error(
    error: BaseException,
    *,
    operation: str,
    server_name: str,
    timeout: float = NO_TIMEOUT,
) -> NoReturn:
    """Raise a typed MCP transport error without hiding caller cancellation.

    SDK business errors remain unchanged. An explicit MCP connection error is
    also preserved so nested client wrappers cannot obscure its original type.
    """
    if isinstance(error, McpConnectionError):
        raise error
    if isinstance(error, asyncio.CancelledError):
        raise error
    cause = error
    if isinstance(error, BaseExceptionGroup):
        cancelled, remaining = error.split(asyncio.CancelledError)
        if remaining is None:
            raise error
        if cancelled is not None:
            current = asyncio.current_task()
            if current is not None and current.cancelling() > 0:
                raise error
        timeout_group, non_timeout = remaining.split(
            lambda item: isinstance(item, TimeoutError)
        )
        if timeout_group is not None:
            error = timeout_group
        elif non_timeout is not None:
            error = non_timeout

    reason = f"{type(error).__name__}: {error}"
    leaves = tuple(_iter_exception_leaves(error))
    has_timeout = any(isinstance(item, TimeoutError) for item in leaves)
    if has_timeout:
        raise build_error(
            StatusCode.TOOL_MCP_EXECUTION_TIMEOUT,
            cause=cause,
            operation=operation,
            server_name=server_name,
            timeout=timeout,
            reason=reason,
        ) from error

    from openjiuwen.core.foundation.tool.mcp.client.reconnect import (
        is_retryable_transport_error,
    )

    is_not_connected = isinstance(error, RuntimeError) and "not connected" in str(error).lower()
    has_cancel_scope_error = any(_is_cancel_scope_error(item) for item in leaves)
    has_transport_error = (
        is_retryable_transport_error(error)
        or any(isinstance(item, OSError) for item in leaves)
    )
    if any((has_transport_error, has_cancel_scope_error, is_not_connected)):
        raise build_error(
            StatusCode.TOOL_MCP_CONNECTION_ERROR,
            cause=cause,
            operation=operation,
            server_name=server_name,
            reason=reason,
        ) from error
    raise error


def coerce_mcp_connect_error(
    error: BaseException,
    *,
    server_name: str,
    timeout: float = NO_TIMEOUT,
) -> BaseException:
    """Return a typed transport error while leaving cancellation for the caller."""
    try:
        raise_mcp_operation_error(
            error,
            operation="connect",
            server_name=server_name,
            timeout=timeout,
        )
    except McpConnectionError as mcp_error:
        return mcp_error
    except Exception as original_error:
        return original_error
    except BaseException as cancellation:
        return cancellation


def _contains_caller_cancellation(error: BaseException) -> bool:
    """Whether ``error`` itself carries a caller cancellation.

    Only a direct ``CancelledError`` or an exception group with a
    cancellation leaf counts (review feedback: a plain RuntimeError with a
    pending owner-task cancellation must NOT be treated as cancellation,
    or the connect path would re-raise the raw error and bypass
    ``coerce_mcp_connect_error``'s unified contract)."""
    if isinstance(error, BaseExceptionGroup):
        cancelled, _ = error.split(asyncio.CancelledError)
        if cancelled is None:
            return False
    elif not isinstance(error, asyncio.CancelledError):
        return False
    current = asyncio.current_task()
    if current is None:
        logger.debug("MCP cancellation has no current task; preserving cancellation")
        return True
    cancelling = getattr(current, "cancelling", None)
    return cancelling is None or cancelling() > 0


class McpClient(BaseClient):
    __client_type__ = "mcp"

    def __init__(self, config: McpServerConfig):
        super().__init__()
        self._server_path = config.server_path
        self._include_image_content = bool(getattr(config, "include_image_content", False))
        self._last_connect_error: Optional[BaseException] = None

    @abstractmethod
    async def connect(self, *, retry_times: int = 1, timeout: float = NO_TIMEOUT) -> bool:
        pass

    @abstractmethod
    async def disconnect(self, *, timeout: float = NO_TIMEOUT) -> bool:
        pass

    @abstractmethod
    async def list_tools(self, *, timeout: float = NO_TIMEOUT) -> List[Any]:
        pass

    @abstractmethod
    async def call_tool(self, tool_name, arguments: dict, *, timeout: float = NO_TIMEOUT) -> Any:
        pass

    @abstractmethod
    async def get_tool_info(self, tool_name: str, *, timeout: float = NO_TIMEOUT) -> Optional[Any]:
        pass

    @abstractmethod
    async def list_resources(self, *, timeout: float = NO_TIMEOUT) -> List[Any]:
        pass

    @abstractmethod
    async def read_resource(self, uri: str, *, timeout: float = NO_TIMEOUT) -> Any:
        pass

    async def close(self) -> bool:
        return await self.disconnect()

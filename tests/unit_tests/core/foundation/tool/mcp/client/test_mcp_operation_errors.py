# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Public MCP transport error types preserve retry and cancellation semantics."""

import asyncio
from unittest.mock import patch

import pytest
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import ExecutionError, build_error
from openjiuwen.core.foundation.tool import McpConnectionError, McpTimeoutError
from openjiuwen.core.foundation.tool.mcp.client.mcp_client import (
    _contains_caller_cancellation,
    raise_mcp_operation_error,
)
from openjiuwen.core.foundation.tool.mcp.client.reconnect import (
    is_retryable_transport_error,
)


def test_timeout_error_uses_public_type_and_unified_status() -> None:
    cause = TimeoutError("handshake timed out")

    with pytest.raises(McpTimeoutError) as exc_info:
        raise_mcp_operation_error(
            cause,
            operation="connect",
            server_name="local-server",
            timeout=0.25,
        )

    error = exc_info.value
    assert isinstance(error, McpConnectionError)
    assert isinstance(error, TimeoutError)
    assert isinstance(error, ExecutionError)
    assert error.status is StatusCode.TOOL_MCP_EXECUTION_TIMEOUT
    assert error.code == 182302
    assert error.cause is cause
    assert error.__cause__ is cause
    assert error.recoverable is True
    assert error.fatal is False
    assert error.params["server_name"] == "local-server"
    assert error.params["timeout"] == 0.25


def test_connection_error_preserves_transport_cause() -> None:
    cause = ConnectionRefusedError("connection refused")

    with pytest.raises(McpConnectionError) as exc_info:
        raise_mcp_operation_error(
            cause,
            operation="call_tool",
            server_name="local-server",
        )

    error = exc_info.value
    assert not isinstance(error, McpTimeoutError)
    assert error.status is StatusCode.TOOL_MCP_CONNECTION_ERROR
    assert error.code == 182303
    assert error.cause is cause


def test_timeout_inside_mixed_exception_group_is_normalized() -> None:
    cause = TimeoutError("read timed out")
    group = ExceptionGroup(
        "transport teardown",
        [RuntimeError("cancel scope exited"), cause],
    )

    with pytest.raises(McpTimeoutError) as exc_info:
        raise_mcp_operation_error(
            group,
            operation="call_tool",
            server_name="local-server",
            timeout=1.0,
        )

    assert exc_info.value.cause is group


@pytest.mark.parametrize("transport_error", [TimeoutError("read timed out"), ConnectionRefusedError("refused")])
def test_nested_exception_group_preserves_transport_classification(transport_error: Exception) -> None:
    group = ExceptionGroup("outer", [ExceptionGroup("inner", [transport_error])])

    with pytest.raises(McpConnectionError) as exc_info:
        raise_mcp_operation_error(
            group,
            operation="connect",
            server_name="local-server",
            timeout=1.0,
        )

    error = exc_info.value
    assert isinstance(error, McpTimeoutError) is isinstance(transport_error, TimeoutError)
    assert error.cause is group


def test_single_cancel_scope_error_group_is_not_retried() -> None:
    group = ExceptionGroup(
        "teardown",
        [RuntimeError("Attempted to exit cancel scope in a different task")],
    )

    with pytest.raises(McpConnectionError) as exc_info:
        raise_mcp_operation_error(
            group,
            operation="call_tool",
            server_name="local-server",
        )

    assert not isinstance(exc_info.value, McpTimeoutError)
    assert exc_info.value.cause is group
    assert not is_retryable_transport_error(exc_info.value)


def test_nested_retryable_transport_group_can_reconnect() -> None:
    cause = ExceptionGroup(
        "outer",
        [ExceptionGroup("inner", [RuntimeError("connection closed")])],
    )
    error = build_error(
        StatusCode.TOOL_MCP_CONNECTION_ERROR,
        operation="call_tool",
        server_name="local-server",
        cause=cause,
        reason="nested transport failure",
    )

    assert is_retryable_transport_error(error)


@pytest.mark.asyncio
async def test_caller_cancellation_wins_over_nested_timeout() -> None:
    group = BaseExceptionGroup(
        "cancelled transport",
        [ExceptionGroup("inner", [TimeoutError("read timed out")]), asyncio.CancelledError()],
    )
    task = asyncio.current_task()
    assert task is not None
    with patch.object(task, "cancelling", return_value=1):
        with pytest.raises(BaseExceptionGroup) as exc_info:
            raise_mcp_operation_error(
                group,
                operation="call_tool",
                server_name="local-server",
                timeout=1.0,
            )
    assert exc_info.value is group


def test_business_mcp_error_is_not_relabelled_as_transport_failure() -> None:
    cause = McpError(ErrorData(code=-32602, message="Invalid params"))

    with pytest.raises(McpError) as exc_info:
        raise_mcp_operation_error(
            cause,
            operation="call_tool",
            server_name="local-server",
        )

    assert exc_info.value is cause


def test_caller_cancellation_is_not_relabelled_as_timeout() -> None:
    cause = asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError) as exc_info:
        raise_mcp_operation_error(
            cause,
            operation="call_tool",
            server_name="local-server",
            timeout=1.0,
        )

    assert exc_info.value is cause


def test_cancellation_only_group_is_not_relabelled() -> None:
    cause = BaseExceptionGroup("cancelled", [asyncio.CancelledError()])

    with pytest.raises(BaseExceptionGroup) as exc_info:
        raise_mcp_operation_error(
            cause,
            operation="connect",
            server_name="local-server",
        )

    assert exc_info.value is cause


def test_build_error_uses_the_same_public_exception_classes() -> None:
    timeout = build_error(
        StatusCode.TOOL_MCP_EXECUTION_TIMEOUT,
        operation="connect",
        server_name="local-server",
        timeout=1.0,
        reason="timed out",
    )
    connection = build_error(
        StatusCode.TOOL_MCP_CONNECTION_ERROR,
        operation="connect",
        server_name="local-server",
        reason="refused",
    )

    assert type(timeout) is McpTimeoutError
    assert type(connection) is McpConnectionError


@pytest.mark.asyncio
async def test_plain_error_with_pending_cancellation_is_not_treated_as_cancellation() -> None:
    """Review regression: a plain connect error (RuntimeError) racing an
    owner task with a pending cancellation must NOT be classified as
    caller cancellation; the connect path stays on the normalized
    McpConnectionError contract instead of re-raising the raw error."""
    task = asyncio.current_task()
    assert task is not None
    with patch.object(task, "cancelling", return_value=1):
        # 修复前：非组异常直接赋给 cancelled -> 误判 True -> 原始异常逃逸
        assert _contains_caller_cancellation(RuntimeError("connection refused")) is False
        # 直接 CancelledError 在同一 pending cancellation 下仍判定为取消
        assert _contains_caller_cancellation(asyncio.CancelledError()) is True
        # 含取消叶子的异常组保持取消判定
        group = BaseExceptionGroup("mixed", [RuntimeError("refused"), asyncio.CancelledError()])
        assert _contains_caller_cancellation(group) is True
        # 不含取消叶子的异常组（纯连接错误）不判定为取消
        plain_group = ExceptionGroup("errors", [RuntimeError("refused")])
        assert _contains_caller_cancellation(plain_group) is False
        # 归一化路径：可重试传输异常 + pending cancellation 仍产出 McpConnectionError
        # （修复前该场景会被误判为取消，原始异常直接逃逸）
        with pytest.raises(McpConnectionError):
            raise_mcp_operation_error(
                OSError("connection refused"),
                operation="connect",
                server_name="local-server",
            )

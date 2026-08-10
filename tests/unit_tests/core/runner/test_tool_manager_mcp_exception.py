# -*- coding: UTF-8 -*-
"""Tests for ToolMgr.add_tool_server exception handling (issue #1497).

``asyncio.CancelledError`` and ``BaseExceptionGroup`` both inherit from
``BaseException``, NOT ``Exception``. A bare ``except Exception`` in
``add_tool_server`` therefore let MCP connection failures wrapped in a
group escape silently (and broke cooperative cancellation). These tests
pin down the expected behavior after the fix.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.common.exception.errors import WorkflowError
from openjiuwen.core.foundation.tool import McpServerConfig
from openjiuwen.core.runner.resources_manager.tool_manager import ToolMgr


def _make_server_config(server_id: str = "srv-err", server_name: str = "err-srv") -> McpServerConfig:
    return McpServerConfig(
        server_id=server_id,
        server_name=server_name,
        server_path="",
        client_type="stdio",
        params={"command": "python", "args": ["-m", "broken"]},
    )


def _fake_client(connect_impl) -> MagicMock:
    fake = MagicMock()
    fake.connect = AsyncMock(side_effect=connect_impl)
    fake.list_tools = AsyncMock(return_value=[])
    fake.disconnect = AsyncMock(return_value=True)
    return fake


@pytest.mark.asyncio
async def test_add_tool_server_surfaces_base_exception_group_as_add_error() -> None:
    """A BaseExceptionGroup from connect() must become RESOURCE_MCP_SERVER_ADD_ERROR."""
    mgr = ToolMgr()
    cfg = _make_server_config()

    group = BaseExceptionGroup("connect failed", [RuntimeError("boom")])
    fake_client = _fake_client(lambda: (_ for _ in ()).throw(group))

    with patch.object(ToolMgr, "_create_client", staticmethod(lambda c: fake_client)):
        with pytest.raises(WorkflowError) as exc_info:
            await mgr.add_tool_server(cfg)

    assert exc_info.value.code == 110512  # RESOURCE_MCP_SERVER_ADD_ERROR
    assert "boom" in str(exc_info.value)


@pytest.mark.asyncio
async def test_add_tool_server_cancellation_propagates_unwrapped() -> None:
    """CancelledError must NOT be wrapped into a business error — it must propagate."""
    mgr = ToolMgr()
    cfg = _make_server_config()

    async def cancel_connect() -> bool:
        raise asyncio.CancelledError("shutdown")

    fake_client = _fake_client(cancel_connect)

    with patch.object(ToolMgr, "_create_client", staticmethod(lambda c: fake_client)):
        with pytest.raises(asyncio.CancelledError):
            await mgr.add_tool_server(cfg)


@pytest.mark.asyncio
async def test_add_tool_server_plain_exception_still_wrapped() -> None:
    """Ordinary exceptions keep the pre-existing wrapping behavior."""
    mgr = ToolMgr()
    cfg = _make_server_config()

    async def fail_connect() -> bool:
        raise ConnectionError("refused")

    fake_client = _fake_client(fail_connect)

    with patch.object(ToolMgr, "_create_client", staticmethod(lambda c: fake_client)):
        with pytest.raises(WorkflowError) as exc_info:
            await mgr.add_tool_server(cfg)

    assert exc_info.value.code == 110512
    assert "refused" in str(exc_info.value)

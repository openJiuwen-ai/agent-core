# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""单元测试：ListMcpResourcesTool 和 ReadMcpResourceTool 的 invoke 逻辑。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.harness.tools.mcp_tools import ListMcpResourcesTool, ReadMcpResourceTool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_resource(uri: str, name: str = "", mime: str | None = None, desc: str | None = None):
    r = MagicMock()
    r.uri = uri
    r.name = name
    r.mimeType = mime
    r.description = desc
    return r


def _make_content(uri: str, mime: str | None = None, text: str | None = None):
    c = MagicMock()
    c.uri = uri
    c.mimeType = mime
    c.text = text
    return c


def _make_tool(cls, language: str = "cn"):
    """构造工具实例，跳过 build_tool_card 的注册依赖。"""
    with patch("openjiuwen.harness.tools.mcp_tools.build_tool_card", return_value=MagicMock()):
        return cls(language)


# ===========================================================================
# ListMcpResourcesTool
# ===========================================================================

class TestListMcpResourcesToolInvoke:
    @pytest.mark.asyncio
    async def test_returns_mapped_resource_list(self):
        tool = _make_tool(ListMcpResourcesTool)
        resources = [
            _make_resource("res://a", name="Alpha", mime="text/plain", desc="first"),
            _make_resource("res://b", name="Beta"),
        ]
        with patch("openjiuwen.harness.tools.mcp_tools.Runner") as MockRunner:
            MockRunner.resource_mgr.list_mcp_resources = AsyncMock(return_value=resources)
            result = await tool.invoke({"server_id": "my-server"})

        assert result.success is True
        assert len(result.data) == 2
        assert result.data[0] == {"uri": "res://a", "name": "Alpha", "mimeType": "text/plain", "description": "first"}
        assert result.data[1] == {"uri": "res://b", "name": "Beta", "mimeType": None, "description": None}

    @pytest.mark.asyncio
    async def test_empty_resource_list_returns_empty_data(self):
        tool = _make_tool(ListMcpResourcesTool)
        with patch("openjiuwen.harness.tools.mcp_tools.Runner") as MockRunner:
            MockRunner.resource_mgr.list_mcp_resources = AsyncMock(return_value=[])
            result = await tool.invoke({"server_id": "my-server"})

        assert result.success is True
        assert result.data == []

    @pytest.mark.asyncio
    async def test_none_resources_returns_empty_data(self):
        tool = _make_tool(ListMcpResourcesTool)
        with patch("openjiuwen.harness.tools.mcp_tools.Runner") as MockRunner:
            MockRunner.resource_mgr.list_mcp_resources = AsyncMock(return_value=None)
            result = await tool.invoke({"server_id": "my-server"})

        assert result.success is True
        assert result.data == []

    @pytest.mark.asyncio
    async def test_missing_server_id_returns_error(self):
        tool = _make_tool(ListMcpResourcesTool)
        result = await tool.invoke({})

        assert result.success is False
        assert "server_id" in result.error

    @pytest.mark.asyncio
    async def test_empty_server_id_returns_error(self):
        tool = _make_tool(ListMcpResourcesTool)
        result = await tool.invoke({"server_id": ""})

        assert result.success is False
        assert "server_id" in result.error

    @pytest.mark.asyncio
    async def test_resource_mgr_exception_returns_error(self):
        tool = _make_tool(ListMcpResourcesTool)
        with patch("openjiuwen.harness.tools.mcp_tools.Runner") as MockRunner:
            MockRunner.resource_mgr.list_mcp_resources = AsyncMock(
                side_effect=RuntimeError("connection refused")
            )
            result = await tool.invoke({"server_id": "bad-server"})

        assert result.success is False
        assert "connection refused" in result.error

    @pytest.mark.asyncio
    async def test_passes_server_id_to_resource_mgr(self):
        tool = _make_tool(ListMcpResourcesTool)
        with patch("openjiuwen.harness.tools.mcp_tools.Runner") as MockRunner:
            MockRunner.resource_mgr.list_mcp_resources = AsyncMock(return_value=[])
            await tool.invoke({"server_id": "target-server"})

        MockRunner.resource_mgr.list_mcp_resources.assert_awaited_once_with("target-server")

    @pytest.mark.asyncio
    async def test_resource_without_attributes_falls_back_to_str(self):
        """资源对象没有 uri 属性时，回退到 str(r)。"""
        tool = _make_tool(ListMcpResourcesTool)
        plain = object()  # 没有 uri/name/mimeType/description 属性
        with patch("openjiuwen.harness.tools.mcp_tools.Runner") as MockRunner:
            MockRunner.resource_mgr.list_mcp_resources = AsyncMock(return_value=[plain])
            result = await tool.invoke({"server_id": "s"})

        assert result.success is True
        assert result.data[0]["uri"] == str(plain)
        assert result.data[0]["name"] == ""
        assert result.data[0]["mimeType"] is None
        assert result.data[0]["description"] is None


# ===========================================================================
# ReadMcpResourceTool
# ===========================================================================

class TestReadMcpResourceToolInvoke:
    @pytest.mark.asyncio
    async def test_returns_mapped_content_list(self):
        tool = _make_tool(ReadMcpResourceTool)
        contents = [
            _make_content("res://a", mime="text/plain", text="hello"),
            _make_content("res://b"),
        ]
        with patch("openjiuwen.harness.tools.mcp_tools.Runner") as MockRunner:
            MockRunner.resource_mgr.read_mcp_resource = AsyncMock(return_value=contents)
            result = await tool.invoke({"server_id": "my-server", "uri": "res://a"})

        assert result.success is True
        assert len(result.data) == 2
        assert result.data[0] == {"uri": "res://a", "mimeType": "text/plain", "text": "hello"}
        assert result.data[1] == {"uri": "res://b", "mimeType": None, "text": None}

    @pytest.mark.asyncio
    async def test_empty_contents_returns_empty_data(self):
        tool = _make_tool(ReadMcpResourceTool)
        with patch("openjiuwen.harness.tools.mcp_tools.Runner") as MockRunner:
            MockRunner.resource_mgr.read_mcp_resource = AsyncMock(return_value=[])
            result = await tool.invoke({"server_id": "s", "uri": "res://x"})

        assert result.success is True
        assert result.data == []

    @pytest.mark.asyncio
    async def test_none_contents_returns_empty_data(self):
        tool = _make_tool(ReadMcpResourceTool)
        with patch("openjiuwen.harness.tools.mcp_tools.Runner") as MockRunner:
            MockRunner.resource_mgr.read_mcp_resource = AsyncMock(return_value=None)
            result = await tool.invoke({"server_id": "s", "uri": "res://x"})

        assert result.success is True
        assert result.data == []

    @pytest.mark.asyncio
    async def test_missing_server_id_returns_error(self):
        tool = _make_tool(ReadMcpResourceTool)
        result = await tool.invoke({"uri": "res://x"})

        assert result.success is False
        assert "server_id" in result.error

    @pytest.mark.asyncio
    async def test_missing_uri_returns_error(self):
        tool = _make_tool(ReadMcpResourceTool)
        result = await tool.invoke({"server_id": "my-server"})

        assert result.success is False
        assert "uri" in result.error

    @pytest.mark.asyncio
    async def test_empty_uri_returns_error(self):
        tool = _make_tool(ReadMcpResourceTool)
        result = await tool.invoke({"server_id": "my-server", "uri": ""})

        assert result.success is False
        assert "uri" in result.error

    @pytest.mark.asyncio
    async def test_resource_mgr_exception_returns_error(self):
        tool = _make_tool(ReadMcpResourceTool)
        with patch("openjiuwen.harness.tools.mcp_tools.Runner") as MockRunner:
            MockRunner.resource_mgr.read_mcp_resource = AsyncMock(
                side_effect=RuntimeError("server not found")
            )
            result = await tool.invoke({"server_id": "bad-server", "uri": "res://x"})

        assert result.success is False
        assert "server not found" in result.error

    @pytest.mark.asyncio
    async def test_passes_server_id_and_uri_to_resource_mgr(self):
        tool = _make_tool(ReadMcpResourceTool)
        with patch("openjiuwen.harness.tools.mcp_tools.Runner") as MockRunner:
            MockRunner.resource_mgr.read_mcp_resource = AsyncMock(return_value=[])
            await tool.invoke({"server_id": "target-server", "uri": "res://doc"})

        MockRunner.resource_mgr.read_mcp_resource.assert_awaited_once_with("target-server", "res://doc")

    @pytest.mark.asyncio
    async def test_content_without_attributes_falls_back_to_str(self):
        """内容对象没有 uri 属性时，回退到 str(c)。"""
        tool = _make_tool(ReadMcpResourceTool)
        plain = object()
        with patch("openjiuwen.harness.tools.mcp_tools.Runner") as MockRunner:
            MockRunner.resource_mgr.read_mcp_resource = AsyncMock(return_value=[plain])
            result = await tool.invoke({"server_id": "s", "uri": "res://x"})

        assert result.success is True
        assert result.data[0]["uri"] == str(plain)
        assert result.data[0]["mimeType"] is None
        assert result.data[0]["text"] is None

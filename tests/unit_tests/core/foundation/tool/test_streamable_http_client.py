#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from openjiuwen.core.foundation.tool import McpServerConfig, McpToolCard
from openjiuwen.core.foundation.tool.auth.auth_callback import AuthHeaderAndQueryProvider
from openjiuwen.core.foundation.tool.mcp.base import extract_mcp_tool_result_content
from openjiuwen.core.foundation.tool.mcp.client.streamable_http_client import (
    StreamableHttpClient,
)

from openjiuwen.core.runner.resources_manager.resource_manager import ResourceMgr


class TestStreamableHttpClient(unittest.IsolatedAsyncioTestCase):
    async def test_connect_list_call_disconnect_lifecycle(self):
        call_args = {}

        class FakeTransportContext:
            async def __aenter__(self):
                return "reader", "writer", "unused"

            async def __aexit__(self, exc_type, exc, tb):
                return False

        class FakeClientSession:
            def __init__(self, read, write, sampling_callback=None):
                self._read = read
                self._write = write
                self._sampling_callback = sampling_callback
                self._initialized = False

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def initialize(self):
                self._initialized = True

            async def list_tools(self):
                return SimpleNamespace(
                    tools=[
                        SimpleNamespace(
                            name="browser_navigate",
                            description="Navigate page",
                            inputSchema={"type": "object"},
                        ),
                        SimpleNamespace(
                            name="browser_extract_text",
                            description="Extract page text",
                            inputSchema={"type": "object"},
                        ),
                    ]
                )

            async def call_tool(self, tool_name, arguments):
                return SimpleNamespace(
                    content=[
                        SimpleNamespace(text=f"ok:{tool_name}:{arguments.get('url', '')}")
                    ]
                )

        def fake_streamablehttp_client(server_path, timeout, auth=None):
            call_args["server_path"] = server_path
            call_args["timeout"] = timeout
            call_args["auth"] = auth
            return FakeTransportContext()

        fake_mcp = types.ModuleType("mcp")
        fake_mcp.ClientSession = FakeClientSession
        fake_mcp_client = types.ModuleType("mcp.client")
        fake_streamable_http = types.ModuleType("mcp.client.streamable_http")
        fake_streamable_http.streamable_http_client = fake_streamablehttp_client
        fake_mcp_client.streamable_http = fake_streamable_http

        with patch.dict(
            sys.modules,
            {
                "mcp": fake_mcp,
                "mcp.client": fake_mcp_client,
                "mcp.client.streamable_http": fake_streamable_http,
            },
            clear=False,
        ):
            client = StreamableHttpClient(
                "http://127.0.0.1:8930/mcp",
                "test-server",
                auth_headers={"Authorization": "Bearer token"},
                auth_query_params={"ak": "demo-ak"},
            )
            connected = await client.connect()
            self.assertTrue(connected)
            self.assertEqual(call_args["server_path"], "http://127.0.0.1:8930/mcp")
            self.assertEqual(call_args["timeout"], 60.0)
            self.assertIsInstance(call_args["auth"], AuthHeaderAndQueryProvider)

            tools = await client.list_tools()
            self.assertEqual(len(tools), 2)
            self.assertEqual(tools[0].name, "browser_navigate")
            self.assertEqual(tools[1].description, "Extract page text")

            result = await client.call_tool("browser_navigate", {"url": "https://example.com"})
            self.assertEqual(result, "ok:browser_navigate:https://example.com")

            tool_info = await client.get_tool_info("browser_extract_text")
            self.assertIsNotNone(tool_info)
            self.assertEqual(tool_info.name, "browser_extract_text")

            missing_info = await client.get_tool_info("missing")
            self.assertIsNone(missing_info)

            disconnected = await client.disconnect()
            self.assertTrue(disconnected)

    async def test_connect_returns_false_on_error(self):
        def fake_streamablehttp_client(*args, **kwargs):
            raise RuntimeError("connection failed")

        fake_mcp = types.ModuleType("mcp")
        fake_mcp.ClientSession = object
        fake_mcp_client = types.ModuleType("mcp.client")
        fake_streamable_http = types.ModuleType("mcp.client.streamable_http")
        fake_streamable_http.streamable_http_client = fake_streamablehttp_client
        fake_mcp_client.streamable_http = fake_streamable_http

        with patch.dict(
            sys.modules,
            {
                "mcp": fake_mcp,
                "mcp.client": fake_mcp_client,
                "mcp.client.streamable_http": fake_streamable_http,
            },
            clear=False,
        ):
            client = StreamableHttpClient("http://127.0.0.1:8930/mcp", "test-server")
            connected = await client.connect(timeout=10.0)
            self.assertFalse(connected)

    async def test_auth_provider_adds_headers_and_query(self):
        provider = AuthHeaderAndQueryProvider(
            auth_headers={"Authorization": "Bearer x"},
            auth_query_params={"ak": "demo-ak"},
        )
        request = httpx.Request("GET", "https://example.com/sse?existing=1")

        flow = provider.async_auth_flow(request)
        signed_request = await anext(flow)
        await flow.aclose()

        self.assertEqual(signed_request.headers["Authorization"], "Bearer x")
        self.assertEqual(signed_request.url.params["existing"], "1")
        self.assertEqual(signed_request.url.params["ak"], "demo-ak")


class TestStreamableHttpResourceManagerIntegration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.resource_mgr = ResourceMgr()

    async def asyncTearDown(self):
        await self.resource_mgr.release()

    async def test_mcp_server_streamable_http_lifecycle(self):
        mock_tools = [
            McpToolCard(
                name="browser_navigate",
                server_name="streamable-server",
                description="Navigate to URL",
                input_params={
                    "type": "object",
                    "properties": {"url": {"type": "string"}},
                    "required": ["url"],
                },
            ),
            McpToolCard(
                name="browser_extract_text",
                server_name="streamable-server",
                description="Extract text",
                input_params={
                    "type": "object",
                    "properties": {"selector": {"type": "string"}},
                    "required": ["selector"],
                },
            ),
        ]
        mock_tool_result = "navigation completed"
        test_inputs = {"url": "https://example.com"}

        with (
            patch.object(StreamableHttpClient, "connect", AsyncMock(return_value=True)),
            patch.object(StreamableHttpClient, "disconnect", AsyncMock(return_value=True)),
            patch.object(StreamableHttpClient, "list_tools", AsyncMock(return_value=mock_tools)),
            patch.object(StreamableHttpClient, "call_tool", AsyncMock(return_value=mock_tool_result)) as mock_call_tool,
        ):
            mcp_server_config = McpServerConfig(
                server_name="streamable-server",
                server_path="http://127.0.0.1:8930/mcp",
                client_type="streamable-http",
            )

            add_result = await self.resource_mgr.add_mcp_server(mcp_server_config)
            self.assertTrue(add_result.is_ok())

            tool_infos = await self.resource_mgr.get_mcp_tool_infos(server_name="streamable-server")
            self.assertEqual(len(tool_infos), 2)
            self.assertSetEqual({item.name for item in tool_infos}, {"browser_navigate", "browser_extract_text"})

            tools = await self.resource_mgr.get_mcp_tool(name="browser_navigate", server_name="streamable-server")
            self.assertEqual(len(tools), 1)
            self.assertIsNotNone(tools[0])

            result = await tools[0].invoke(test_inputs)
            self.assertEqual(result, {"result": mock_tool_result})
            mock_call_tool.assert_awaited_once_with(tool_name="browser_navigate", arguments=test_inputs)

            remove_results = await self.resource_mgr.remove_mcp_server(server_name="streamable-server")
            self.assertEqual(len(remove_results), 1)
            self.assertTrue(remove_results[0].is_ok())

            remaining_infos = await self.resource_mgr.get_mcp_tool_infos(server_name="streamable-server")
            self.assertEqual(remaining_infos, [])

    async def test_mcp_tool_drops_missing_optional_arguments(self):
        mock_tools = [
            McpToolCard(
                name="browser_type",
                server_name="streamable-server",
                description="Type text",
                input_params={
                    "type": "object",
                    "properties": {
                        "ref": {"type": "string"},
                        "text": {"type": "string"},
                        "submit": {"type": "boolean"},
                        "slowly": {"type": "boolean"},
                    },
                    "required": ["ref", "text"],
                },
            )
        ]

        with (
            patch.object(StreamableHttpClient, "connect", AsyncMock(return_value=True)),
            patch.object(StreamableHttpClient, "disconnect", AsyncMock(return_value=True)),
            patch.object(StreamableHttpClient, "list_tools", AsyncMock(return_value=mock_tools)),
            patch.object(StreamableHttpClient, "call_tool", AsyncMock(return_value="typed")) as mock_call_tool,
        ):
            mcp_server_config = McpServerConfig(
                server_name="streamable-server",
                server_path="http://127.0.0.1:8930/mcp",
                client_type="streamable-http",
            )

            add_result = await self.resource_mgr.add_mcp_server(mcp_server_config)
            self.assertTrue(add_result.is_ok())

            tools = await self.resource_mgr.get_mcp_tool(name="browser_type", server_name="streamable-server")
            result = await tools[0].invoke({"ref": "q", "text": "wireless mouse"})

            self.assertEqual(result, {"result": "typed"})
            mock_call_tool.assert_awaited_once_with(
                tool_name="browser_type",
                arguments={"ref": "q", "text": "wireless mouse"},
            )

    async def test_mcp_tool_preserves_empty_object_arguments(self):
        mock_tools = [
            McpToolCard(
                name="browser_snapshot",
                server_name="streamable-server",
                description="Capture accessibility snapshot",
                input_params={
                    "type": "object",
                    "properties": {
                        "filename": {"type": "string"},
                        "depth": {"type": "number"},
                    },
                    "additionalProperties": False,
                },
            )
        ]

        with (
            patch.object(StreamableHttpClient, "connect", AsyncMock(return_value=True)),
            patch.object(StreamableHttpClient, "disconnect", AsyncMock(return_value=True)),
            patch.object(StreamableHttpClient, "list_tools", AsyncMock(return_value=mock_tools)),
            patch.object(StreamableHttpClient, "call_tool", AsyncMock(return_value="snapshotted")) as mock_call_tool,
        ):
            mcp_server_config = McpServerConfig(
                server_name="streamable-server",
                server_path="http://127.0.0.1:8930/mcp",
                client_type="streamable-http",
            )

            add_result = await self.resource_mgr.add_mcp_server(mcp_server_config)
            self.assertTrue(add_result.is_ok())

            tools = await self.resource_mgr.get_mcp_tool(name="browser_snapshot", server_name="streamable-server")
            result = await tools[0].invoke({})

            self.assertEqual(result, {"result": "snapshotted"})
            mock_call_tool.assert_awaited_once_with(
                tool_name="browser_snapshot",
                arguments={},
            )


class TestMcpToolResultExtraction(unittest.TestCase):
    def test_image_content_returns_compact_description(self):
        tool_result = SimpleNamespace(content=[SimpleNamespace(mimeType="image/png", data="abc123")])

        result = extract_mcp_tool_result_content(tool_result)

        self.assertEqual(result, "[image content: image/png, 6 base64 chars]")

    def test_no_content_returns_none(self):
        tool_result = SimpleNamespace(content=[])

        self.assertIsNone(extract_mcp_tool_result_content(tool_result))


class TestMcpModelToolNameHelpers(unittest.TestCase):
    def test_helpers_pin_the_ability_manager_naming_convention(self):
        # Rails match tools by these names; a silent convention change in
        # AbilityManager must fail here, not in a live run.
        from openjiuwen.core.foundation.tool.mcp.base import mcp_model_tool_name, mcp_model_tool_prefix

        self.assertEqual(mcp_model_tool_prefix("cua-driver"), "mcp_cua-driver_")
        self.assertEqual(mcp_model_tool_name("cua-driver", "click"), "mcp_cua-driver_click")


class TestMcpToolResultMultiBlockExtraction(unittest.TestCase):
    def test_text_and_image_blocks_are_both_preserved(self):
        # Perception tools return action/state text plus a screenshot; block
        # order must not decide which half of the result survives.
        tool_result = SimpleNamespace(
            content=[
                SimpleNamespace(text="window_id=1 pid=7 elements=42"),
                SimpleNamespace(mimeType="image/png", data="abc123"),
            ]
        )

        result = extract_mcp_tool_result_content(tool_result)

        self.assertIn("window_id=1 pid=7 elements=42", result)
        self.assertIn("[image content: image/png, 6 base64 chars]", result)

    def test_image_first_text_last_keeps_both(self):
        tool_result = SimpleNamespace(
            content=[
                SimpleNamespace(mimeType="image/png", data="abc123"),
                SimpleNamespace(text="window_id=1 pid=7 elements=42"),
            ]
        )

        result = extract_mcp_tool_result_content(tool_result)

        self.assertIn("[image content: image/png, 6 base64 chars]", result)
        self.assertIn("window_id=1 pid=7 elements=42", result)

    def test_multiple_text_blocks_joined_in_order(self):
        tool_result = SimpleNamespace(
            content=[SimpleNamespace(text="first"), SimpleNamespace(text="second")]
        )

        result = extract_mcp_tool_result_content(tool_result)

        self.assertEqual(result, "first\n\nsecond")


class TestMcpToolResultImageBridge(unittest.TestCase):
    def test_image_bridged_to_multimodal_when_opted_in(self):
        from openjiuwen.core.foundation.tool import McpToolResult

        tool_result = SimpleNamespace(
            content=[
                SimpleNamespace(text="tree summary"),
                SimpleNamespace(mimeType="image/png", data="abc123"),
            ]
        )

        result = extract_mcp_tool_result_content(
            tool_result, include_image_content=True, tool_name="get_window_state"
        )

        self.assertIsInstance(result, McpToolResult)
        self.assertEqual(
            result.data["content"],
            "tree summary\n\n1 image(s) attached as multimodal input.",
        )
        (item,) = result.data["multimodal"]
        self.assertEqual(item["type"], "image")
        self.assertEqual(item["source"], "mcp")
        self.assertEqual(item["source_path"], "get_window_state")
        self.assertEqual(item["mime_type"], "image/png")
        self.assertEqual(item["data_url"], "data:image/png;base64,abc123")

    def test_opted_in_without_images_returns_plain_text(self):
        tool_result = SimpleNamespace(content=[SimpleNamespace(text="no screenshot here")])

        result = extract_mcp_tool_result_content(tool_result, include_image_content=True)

        self.assertEqual(result, "no screenshot here")


class TestMcpToolInvokeMultimodalWrapping(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _make_tool(call_result):
        from openjiuwen.core.foundation.tool.mcp.base import MCPTool

        client = SimpleNamespace(call_tool=AsyncMock(return_value=call_result))
        card = McpToolCard(
            name="get_window_state",
            description="",
            input_params={"type": "object", "properties": {}},
            server_name="cua-driver",
        )
        return MCPTool(client, card)

    async def test_mcp_tool_result_passes_through_unwrapped(self):
        from openjiuwen.core.foundation.tool import McpToolResult

        bridged = McpToolResult(
            data={
                "content": "tree summary\n\n1 image(s) attached as multimodal input.",
                "multimodal": [
                    {
                        "type": "image",
                        "source": "mcp",
                        "source_path": "get_window_state",
                        "mime_type": "image/png",
                        "data_url": "data:image/png;base64,abc123",
                    }
                ],
            }
        )
        tool = self._make_tool(bridged)

        result = await tool.invoke({})

        self.assertIs(result, bridged)
        self.assertTrue(result.success)

    async def test_plain_dict_result_is_not_mistaken_for_multimodal(self):
        # A server-returned dict that happens to contain "content" and
        # "multimodal" keys must keep the legacy {"result": ...} shape;
        # only the McpToolResult sentinel type triggers the bridge.
        lookalike = {"content": "x", "multimodal": []}
        tool = self._make_tool(lookalike)

        result = await tool.invoke({})

        self.assertEqual(result, {"result": lookalike})

    async def test_plain_result_keeps_legacy_shape(self):
        tool = self._make_tool("snapshotted")

        result = await tool.invoke({})

        self.assertEqual(result, {"result": "snapshotted"})

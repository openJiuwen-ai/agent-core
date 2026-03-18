#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
SSE — MCPTool usage example
=============================
Demonstrates using MCPTool (openjiuwen.core.foundation.tool.mcp.base.MCPTool)
instead of calling the transport client directly.

MCPTool wraps an McpClient + McpToolCard and exposes the standard Tool.invoke()
interface, making MCP tools interchangeable with any other openjiuwen Tool.

Prerequisites:
    1. Start the server first:  python server.py
    2. Run this file:           python client_as_tool.py
"""

import asyncio

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.tool.mcp.base import MCPTool, McpServerConfig
from openjiuwen.core.foundation.tool.mcp.client.sse_client import SseClient

SERVER_URL = "http://127.0.0.1:3001/sse"
SERVER_NAME = "calculator-sse-server"


async def main() -> None:
    # ── 1. Create and connect the transport client ────────────────────────────
    client = SseClient(McpServerConfig(server_name=SERVER_NAME, server_path=SERVER_URL, client_type="sse"))

    logger.info(f"Connecting to SSE server at {SERVER_URL} ...")
    connected = await client.connect()
    if not connected:
        logger.info("Failed to connect. Make sure server.py is running.")
        return
    logger.info("Connected.\n")

    # ── 2. Discover tools — each McpToolCard describes one server tool ────────
    tool_cards = await client.list_tools()
    logger.info(f"Discovered {len(tool_cards)} tool(s): {[c.name for c in tool_cards]}\n")

    # ── 3. Wrap every card in MCPTool ─────────────────────────────────────────
    #   MCPTool(mcp_client, tool_info) — the client is shared across all tools.
    #   MCPTool.invoke() delegates to client.call_tool() internally.
    tools: dict[str, MCPTool] = {
        card.name: MCPTool(mcp_client=client, tool_info=card)
        for card in tool_cards
    }

    # ── 4. Invoke tools via the standard Tool.invoke() interface ─────────────
    #   invoke() always returns {"result": <value>}

    result = await tools["add"].invoke({"a": 10, "b": 3})
    logger.info(f"add(10, 3)       → {result}")       # {"result": "13.0"}

    result = await tools["subtract"].invoke({"a": 10, "b": 3})
    logger.info(f"subtract(10, 3)  → {result}")

    result = await tools["multiply"].invoke({"a": 10, "b": 3})
    logger.info(f"multiply(10, 3)  → {result}")

    result = await tools["divide"].invoke({"a": 10, "b": 4})
    logger.info(f"divide(10, 4)    → {result}")

    result = await tools["divide"].invoke({"a": 10, "b": 0})
    logger.info(f"divide(10, 0)    → {result}")        # error message

    result = await tools["power"].invoke({"base": 2, "exponent": 10})
    logger.info(f"power(2, 10)     → {result}")

    logger.info("")

    # ── 5. Disconnect ─────────────────────────────────────────────────────────
    await client.disconnect()
    logger.info("Disconnected.")


if __name__ == "__main__":
    asyncio.run(main())

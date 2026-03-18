#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
SSE (Server-Sent Events) MCP Client Example
============================================
This client connects to the SSE MCP server (server.py), lists available tools,
and calls each calculator tool to demonstrate usage.

Prerequisites:
    1. Start the server first:
           python server.py
    2. Then run this client:
           python client_direct.py
"""

import asyncio

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.tool.mcp.base import McpServerConfig
from openjiuwen.core.foundation.tool.mcp.client.sse_client import SseClient

SERVER_URL = "http://127.0.0.1:3001/sse"
SERVER_NAME = "calculator-sse-server"


async def main() -> None:
    logger.info(f"Connecting to SSE MCP server at {SERVER_URL} ...")
    client = SseClient(McpServerConfig(
        server_name=SERVER_NAME,
        server_path=SERVER_URL,
        client_type="sse",
        # Optional: add auth headers or query params if the server requires them
        # auth_headers={"Authorization": "Bearer <token>"},
        # auth_query_params={"api_key": "secret"},
    ))

    connected = await client.connect()
    if not connected:
        logger.info("Failed to connect to SSE server. Make sure server.py is running.")
        return

    logger.info("Connected successfully!\n")

    # --- List available tools ---
    tools = await client.list_tools()
    logger.info(f"Available tools ({len(tools)}):")
    for tool in tools:
        logger.info(f"  - {tool.name}: {tool.description}")
    logger.info("")

    # --- Get info for a specific tool ---
    tool_info = await client.get_tool_info("add")
    if tool_info:
        logger.info(f"Tool info for 'add': {tool_info.name} — {tool_info.description}")
        logger.info("")

    # --- Call tools ---
    logger.info("Calling tools:")

    result = await client.call_tool("add", {"a": 10, "b": 3})
    logger.info(f"  add(10, 3)        = {result}")

    result = await client.call_tool("subtract", {"a": 10, "b": 3})
    logger.info(f"  subtract(10, 3)   = {result}")

    result = await client.call_tool("multiply", {"a": 10, "b": 3})
    logger.info(f"  multiply(10, 3)   = {result}")

    result = await client.call_tool("divide", {"a": 10, "b": 3})
    logger.info(f"  divide(10, 3)     = {result}")

    result = await client.call_tool("divide", {"a": 10, "b": 0})
    logger.info(f"  divide(10, 0)     = {result}")

    result = await client.call_tool("power", {"base": 2, "exponent": 8})
    logger.info(f"  power(2, 8)       = {result}")

    logger.info("")

    # --- Disconnect ---
    await client.disconnect()
    logger.info("Disconnected from SSE server.")


if __name__ == "__main__":
    asyncio.run(main())

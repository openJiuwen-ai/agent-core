#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Streamable HTTP MCP Client Example
====================================
This client connects to the Streamable HTTP MCP server (server.py), lists
available tools, and exercises the note-taking API.

Prerequisites:
    1. Start the server first:
           python server.py
    2. Then run this client:
           python client_direct.py
"""

import asyncio

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.tool.mcp.base import McpServerConfig
from openjiuwen.core.foundation.tool.mcp.client.streamable_http_client import StreamableHttpClient

SERVER_URL = "http://127.0.0.1:3002/mcp"
SERVER_NAME = "notes-streamable-http-server"


async def main() -> None:
    logger.info(f"Connecting to Streamable HTTP MCP server at {SERVER_URL} ...")
    client = StreamableHttpClient(McpServerConfig(
        server_name=SERVER_NAME,
        server_path=SERVER_URL,
        client_type="streamable-http",
        # Optional: add auth headers or query params if the server requires them
        # auth_headers={"Authorization": "Bearer <token>"},
        # auth_query_params={"api_key": "secret"},
    ))

    connected = await client.connect(timeout=30.0)
    if not connected:
        logger.info("Failed to connect. Make sure server.py is running.")
        return

    logger.info("Connected successfully!\n")

    # --- List available tools ---
    tools = await client.list_tools()
    logger.info(f"Available tools ({len(tools)}):")
    for tool in tools:
        logger.info(f"  - {tool.name}: {tool.description}")
    logger.info("")

    # --- Get info for a specific tool ---
    tool_info = await client.get_tool_info("add_note")
    if tool_info:
        logger.info(f"Tool info for 'add_note': {tool_info.name} — {tool_info.description}")
        logger.info("")

    # --- Call tools ---
    logger.info("Calling tools:")

    result = await client.call_tool("list_notes", {})
    logger.info(f"  list_notes (empty):      {result}")

    result = await client.call_tool("add_note", {"content": "Buy groceries"})
    logger.info(f"  add_note 'Buy groceries': {result}")

    result = await client.call_tool("add_note", {"content": "Call the dentist"})
    logger.info(f"  add_note 'Call dentist':  {result}")

    result = await client.call_tool("add_note", {"content": "Finish quarterly report"})
    logger.info(f"  add_note 'Quarterly':     {result}")

    result = await client.call_tool("list_notes", {})
    logger.info(f"  list_notes (3 items):    {result}")

    result = await client.call_tool("get_note", {"note_id": 1})
    logger.info(f"  get_note(1):             {result}")

    result = await client.call_tool("delete_note", {"note_id": 0})
    logger.info(f"  delete_note(0):          {result}")

    result = await client.call_tool("list_notes", {})
    logger.info(f"  list_notes after delete: {result}")

    result = await client.call_tool("clear_notes", {})
    logger.info(f"  clear_notes:             {result}")

    result = await client.call_tool("list_notes", {})
    logger.info(f"  list_notes (cleared):    {result}")

    logger.info("")

    # --- Disconnect ---
    await client.disconnect()
    logger.info("Disconnected from Streamable HTTP server.")


if __name__ == "__main__":
    asyncio.run(main())

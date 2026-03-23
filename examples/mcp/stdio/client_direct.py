#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Stdio MCP Client Example
========================
This client launches server.py as a subprocess (Stdio transport), communicates
with it over stdin/stdout, lists available tools, and calls each text-processing
tool to demonstrate usage.

Prerequisites:
    No need to start the server manually — the client starts it automatically.

Run:
    python client_direct.py

Note:
    The `command` field should point to the Python interpreter and server.py.
    Update the path below if your environment is different.
"""

import sys
import asyncio
from pathlib import Path

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.tool.mcp.base import McpServerConfig
from openjiuwen.core.foundation.tool.mcp.client.stdio_client import StdioClient

# Path to the server script (resolved relative to this file)
SERVER_SCRIPT = str(Path(__file__).parent / "server.py")
SERVER_NAME = "text-processor-stdio-server"


async def main() -> None:
    logger.info(f"Launching Stdio MCP server: {sys.executable} {SERVER_SCRIPT}")

    client = StdioClient(McpServerConfig(
        server_name=SERVER_NAME,
        server_path="",          # Not used for Stdio; command/args come from params
        client_type="stdio",
        params={
            "command": sys.executable,          # Current Python interpreter
            "args": [SERVER_SCRIPT],             # The server script to run
            "env": None,                         # Inherit environment (or pass a dict)
            "cwd": str(Path(__file__).parent),  # Working directory
            "encoding_error_handler": "strict",  # 'strict' | 'ignore' | 'replace'
        },
    ))

    connected = await client.connect()
    if not connected:
        logger.info("Failed to start Stdio server.")
        return

    logger.info("Stdio server started and connected!\n")

    # --- List available tools ---
    tools = await client.list_tools()
    logger.info(f"Available tools ({len(tools)}):")
    for tool in tools:
        logger.info(f"  - {tool.name}: {tool.description}")
    logger.info("")

    # --- Get info for a specific tool ---
    tool_info = await client.get_tool_info("word_count")
    if tool_info:
        logger.info(f"Tool info for 'word_count': {tool_info.name} — {tool_info.description}")
        logger.info("")

    sample_text = "The quick brown fox jumps over the lazy dog"
    logger.info(f"Sample text: '{sample_text}'\n")

    # --- Call tools ---
    logger.info("Calling tools:")

    result = await client.call_tool("word_count", {"text": sample_text})
    logger.info(f"  word_count        = {result}")

    result = await client.call_tool("char_count", {"text": sample_text})
    logger.info(f"  char_count        = {result}")

    result = await client.call_tool("reverse_text", {"text": sample_text})
    logger.info(f"  reverse_text      = {result}")

    result = await client.call_tool("to_uppercase", {"text": sample_text})
    logger.info(f"  to_uppercase      = {result}")

    result = await client.call_tool("to_lowercase", {"text": "Hello WORLD"})
    logger.info(f"  to_lowercase      = {result}")

    multiline = "Line one\nLine two\nLine three"
    result = await client.call_tool("count_lines", {"text": multiline})
    logger.info(f"  count_lines       = {result}  (for 3-line string)")

    logger.info("")

    # --- Disconnect (terminates the subprocess) ---
    await client.disconnect()
    logger.info("Disconnected — subprocess terminated.")


if __name__ == "__main__":
    asyncio.run(main())

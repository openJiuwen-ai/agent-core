#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Playwright MCP Client Example
==============================
Demonstrates two connection modes supported by PlaywrightClient:

  Mode A — SSE (default in this example):
      Connect to the Playwright MCP server running at an HTTP/SSE URL.
      Start the server first:  python server.py

  Mode B — Stdio:
      Launch the official Playwright MCP Node.js server as a subprocess.
      Requires Node.js:  npm install -g @playwright/mcp
      (Uncomment the stdio section below to use this mode.)

Run (SSE mode):
    # Terminal 1 — start the server
    python server.py
    # Terminal 2 — run the client
    python client_direct.py
"""

import asyncio

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.tool.mcp.base import McpServerConfig
from openjiuwen.core.foundation.tool.mcp.client.playwright_client import PlaywrightClient

# ── Mode A: SSE transport (connects to server.py) ─────────────────────────────
SSE_SERVER_URL = "http://127.0.0.1:3003/sse"
SERVER_NAME = "browser-playwright-server"

# ── Mode B: Stdio transport (official @playwright/mcp Node.js server) ─────────
# from mcp import StdioServerParameters
# STDIO_PARAMS = StdioServerParameters(
#     command="npx",
#     args=["@playwright/mcp@latest", "--headless"],
# )


async def run_sse_example() -> None:
    """Connect using SSE transport (requires server.py to be running)."""
    logger.info(f"[SSE] Connecting to Playwright MCP server at {SSE_SERVER_URL} ...")
    client = PlaywrightClient(McpServerConfig(server_name=SERVER_NAME, server_path=SSE_SERVER_URL,
                                              client_type="playwright"))

    connected = await client.connect()
    if not connected:
        logger.info("[SSE] Failed to connect. Make sure server.py is running.")
        return

    logger.info("[SSE] Connected successfully!\n")
    await _run_browser_demo(client, mode="SSE")
    await client.disconnect()
    logger.info("[SSE] Disconnected.")


# async def run_stdio_example() -> None:
#     """
#     Connect using Stdio transport (launches @playwright/mcp as a subprocess).
#     Requires Node.js and:  npm install -g @playwright/mcp
#     """
#     logger.info("[Stdio] Launching Playwright MCP server via stdio ...")
#     client = PlaywrightClient(McpServerConfig(server_name="playwright-mcp-node", server_path=SSE_SERVER_URL,
#     client_type="playwright"))
#
#     connected = await client.connect()
#     if not connected:
#         logger.info("[Stdio] Failed to start server. Check that Node.js and @playwright/mcp are installed.")
#         return
#
#     logger.info("[Stdio] Server started!\n")
#     await _run_browser_demo(client, mode="Stdio")
#     await client.disconnect()
#     logger.info("[Stdio] Disconnected.")


async def _run_browser_demo(client: PlaywrightClient, mode: str) -> None:
    """List tools and call each browser tool."""

    # --- List available tools ---
    tools = await client.list_tools()
    logger.info(f"[{mode}] Available tools ({len(tools)}):")
    for tool in tools:
        logger.info(f"  - {tool.name}: {tool.description}")
    logger.info("")

    # --- Get info for a specific tool ---
    tool_info = await client.get_tool_info("browser_navigate")
    if tool_info:
        logger.info(f"[{mode}] Tool info for 'browser_navigate': {tool_info.description}")
        logger.info("")

    target_url = "https://example.com"
    logger.info(f"[{mode}] Using target URL: {target_url}\n")

    logger.info(f"[{mode}] Calling tools:")

    result = await client.call_tool("browser_navigate", {"url": target_url})
    logger.info(f"  browser_navigate:      {result}")

    result = await client.call_tool("browser_get_text", {"url": target_url})
    preview = str(result)[:120] + ("..." if result and len(str(result)) > 120 else "")
    logger.info(f"  browser_get_text:      {preview}")

    result = await client.call_tool("browser_get_links", {"url": target_url})
    logger.info(f"  browser_get_links:     {result[:3]} (showing first 3)")

    result = await client.call_tool(
        "browser_take_screenshot",
        {"url": target_url, "output_path": "/tmp/playwright_example.png"}
    )
    logger.info(f"  browser_take_screenshot: {result}")

    logger.info("")


async def main() -> None:
    # Run SSE mode (default)
    await run_sse_example()

    # Uncomment below to run Stdio mode instead (needs @playwright/mcp installed)
    # await run_stdio_example()


if __name__ == "__main__":
    asyncio.run(main())

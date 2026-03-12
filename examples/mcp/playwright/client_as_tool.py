#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Playwright — MCPTool usage example
=====================================
Demonstrates using MCPTool with the PlaywrightClient transport.
Browser automation tools are wrapped in MCPTool and invoked via the
standard Tool.invoke() interface.

Two connection modes are shown:

  Mode A — SSE (default, requires server.py to be running):
      python server.py        # Terminal 1
      python client_as_tool.py  # Terminal 2

  Mode B — Stdio via official Node.js @playwright/mcp (commented out):
      npm install -g @playwright/mcp
      python client_as_tool.py
"""

import asyncio

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.tool.mcp.base import MCPTool, McpServerConfig
from openjiuwen.core.foundation.tool.mcp.client.playwright_client import PlaywrightClient

# ── Mode A: SSE ───────────────────────────────────────────────────────────────
SSE_SERVER_URL = "http://127.0.0.1:3003/sse"
SERVER_NAME = "browser-playwright-server"

# ── Mode B: Stdio via @playwright/mcp (Node.js) ──────────────────────────────
# from mcp import StdioServerParameters
# STDIO_PARAMS = StdioServerParameters(
#     command="npx",
#     args=["@playwright/mcp@latest", "--headless"],
# )


async def main() -> None:
    # ── 1. Create and connect the transport client ────────────────────────────
    #   Switch to PlaywrightClient(McpServerConfig(..., client_type="playwright")) for Mode B.
    client = PlaywrightClient(McpServerConfig(server_name=SERVER_NAME, server_path=SSE_SERVER_URL,
                                              client_type="playwright"))

    logger.info(f"Connecting to Playwright server at {SSE_SERVER_URL} ...")
    connected = await client.connect()
    if not connected:
        logger.info("Failed to connect. Make sure server.py is running.")
        return
    logger.info("Connected.\n")

    # ── 2. Discover tools ─────────────────────────────────────────────────────
    tool_cards = await client.list_tools()
    logger.info(f"Discovered {len(tool_cards)} tool(s): {[c.name for c in tool_cards]}\n")

    # ── 3. Wrap every card in MCPTool ─────────────────────────────────────────
    tools: dict[str, MCPTool] = {
        card.name: MCPTool(mcp_client=client, tool_info=card)
        for card in tool_cards
    }

    # ── 4. Invoke tools via the standard Tool.invoke() interface ─────────────
    target_url = "https://example.com"
    logger.info(f"Target URL: {target_url}\n")

    result = await tools["browser_navigate"].invoke({"url": target_url})
    logger.info(f"browser_navigate        → {result}")

    result = await tools["browser_get_text"].invoke({"url": target_url})
    raw = result.get("result", "") or ""
    preview = str(raw)[:120] + ("..." if len(str(raw)) > 120 else "")
    logger.info(f"browser_get_text        → {{'result': '{preview}'}}")

    result = await tools["browser_get_links"].invoke({"url": target_url})
    links = result.get("result", []) or []
    logger.info(f"browser_get_links       → {{result: [{links[0] if links else ''}...]}}")

    result = await tools["browser_take_screenshot"].invoke({
        "url": target_url,
        "output_path": "/tmp/playwright_mcp_tool.png",
    })
    logger.info(f"browser_take_screenshot → {result}")

    logger.info("")

    # ── 5. Disconnect ─────────────────────────────────────────────────────────
    await client.disconnect()
    logger.info("Disconnected.")


if __name__ == "__main__":
    asyncio.run(main())

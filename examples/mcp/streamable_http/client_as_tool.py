#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Streamable HTTP — MCPTool usage example
=========================================
Demonstrates using MCPTool with the Streamable HTTP transport.
Each note-taking endpoint is wrapped in an MCPTool and invoked via
the standard Tool.invoke() interface.

Prerequisites:
    1. Start the server first:  python server.py
    2. Run this file:           python client_as_tool.py
"""

import asyncio

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.tool.mcp.base import MCPTool, McpServerConfig
from openjiuwen.core.foundation.tool.mcp.client.streamable_http_client import StreamableHttpClient

SERVER_URL = "http://127.0.0.1:3002/mcp"
SERVER_NAME = "notes-streamable-http-server"


async def main() -> None:
    # ── 1. Create and connect the transport client ────────────────────────────
    client = StreamableHttpClient(McpServerConfig(server_name=SERVER_NAME, server_path=SERVER_URL,
                                                  client_type="streamable-http"))

    logger.info(f"Connecting to Streamable HTTP server at {SERVER_URL} ...")
    connected = await client.connect(timeout=30.0)
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
    result = await tools["list_notes"].invoke({})
    logger.info(f"list_notes (empty)          → {result}")

    result = await tools["add_note"].invoke({"content": "Buy groceries"})
    logger.info(f"add_note 'Buy groceries'    → {result}")

    result = await tools["add_note"].invoke({"content": "Call the dentist"})
    logger.info(f"add_note 'Call dentist'     → {result}")

    result = await tools["add_note"].invoke({"content": "Finish quarterly report"})
    logger.info(f"add_note 'Quarterly report' → {result}")

    result = await tools["list_notes"].invoke({})
    logger.info(f"list_notes (3 items)        → {result}")

    result = await tools["get_note"].invoke({"note_id": 1})
    logger.info(f"get_note(1)                 → {result}")

    result = await tools["delete_note"].invoke({"note_id": 0})
    logger.info(f"delete_note(0)              → {result}")

    result = await tools["list_notes"].invoke({})
    logger.info(f"list_notes after delete     → {result}")

    result = await tools["clear_notes"].invoke({})
    logger.info(f"clear_notes                 → {result}")

    result = await tools["list_notes"].invoke({})
    logger.info(f"list_notes (cleared)        → {result}")

    logger.info("")

    # ── 5. Disconnect ─────────────────────────────────────────────────────────
    await client.disconnect()
    logger.info("Disconnected.")


if __name__ == "__main__":
    asyncio.run(main())

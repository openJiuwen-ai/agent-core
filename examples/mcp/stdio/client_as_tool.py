#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Stdio — MCPTool usage example
================================
Demonstrates using MCPTool with the Stdio transport.
The client launches server.py as a subprocess; MCPTool wraps each discovered
tool card so it can be invoked via the standard Tool.invoke() interface.

Prerequisites:
    No separate server process needed — the subprocess is managed automatically.

Run:
    python client_as_tool.py
"""

import sys
import asyncio
from pathlib import Path

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.tool.mcp.base import MCPTool, McpServerConfig
from openjiuwen.core.foundation.tool.mcp.client.stdio_client import StdioClient

SERVER_SCRIPT = str(Path(__file__).parent / "server.py")
SERVER_NAME = "text-processor-stdio-server"


async def main() -> None:
    # ── 1. Create and connect the transport client ────────────────────────────
    client = StdioClient(McpServerConfig(
        server_name=SERVER_NAME,
        server_path="",
        client_type="stdio",
        params={
            "command": sys.executable,
            "args": [SERVER_SCRIPT],
            "cwd": str(Path(__file__).parent),
            "encoding_error_handler": "strict",
        },
    ))

    logger.info(f"Launching Stdio server: {sys.executable} {SERVER_SCRIPT}")
    connected = await client.connect()
    if not connected:
        logger.info("Failed to start server subprocess.")
        return
    logger.info("Server started.\n")

    # ── 2. Discover tools ─────────────────────────────────────────────────────
    tool_cards = await client.list_tools()
    logger.info(f"Discovered {len(tool_cards)} tool(s): {[c.name for c in tool_cards]}\n")

    # ── 3. Wrap every card in MCPTool ─────────────────────────────────────────
    tools: dict[str, MCPTool] = {
        card.name: MCPTool(mcp_client=client, tool_info=card)
        for card in tool_cards
    }

    # ── 4. Invoke tools via the standard Tool.invoke() interface ─────────────
    sample = "The quick brown fox jumps over the lazy dog"
    logger.info(f"Sample text: '{sample}'\n")

    result = await tools["word_count"].invoke({"text": sample})
    logger.info(f"word_count     → {result}")

    result = await tools["char_count"].invoke({"text": sample})
    logger.info(f"char_count     → {result}")

    result = await tools["reverse_text"].invoke({"text": sample})
    logger.info(f"reverse_text   → {result}")

    result = await tools["to_uppercase"].invoke({"text": sample})
    logger.info(f"to_uppercase   → {result}")

    result = await tools["to_lowercase"].invoke({"text": "Hello WORLD"})
    logger.info(f"to_lowercase   → {result}")

    multiline = "Line one\nLine two\nLine three"
    result = await tools["count_lines"].invoke({"text": multiline})
    logger.info(f"count_lines    → {result}  (3-line string)")

    logger.info("")

    # ── 5. Disconnect (terminates subprocess) ─────────────────────────────────
    await client.disconnect()
    logger.info("Disconnected — subprocess terminated.")


if __name__ == "__main__":
    asyncio.run(main())

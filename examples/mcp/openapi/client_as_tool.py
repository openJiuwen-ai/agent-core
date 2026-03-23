#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
OpenAPI — MCPTool usage example
==================================
Demonstrates using MCPTool with OpenApiClient.
OpenApiClient converts openapi.yaml into McpToolCard objects; each card is then
wrapped in MCPTool so it can be invoked via the standard Tool.invoke() interface.
Internally MCPTool delegates to OpenApiClient which makes real HTTP calls to
server.py.

Prerequisites:
    1. Start the REST server first:  python server.py
    2. Run this file:                python client_as_tool.py
"""

import asyncio
import json
from pathlib import Path

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.tool.mcp.base import MCPTool, McpServerConfig
from openjiuwen.core.foundation.tool.mcp.client.openapi_client import OpenApiClient

SPEC_PATH = str(Path(__file__).parent / "openapi.yaml")
SERVER_NAME = "task-openapi-server"


def _pretty(value) -> str:
    """Format the {"result": ...} Output from MCPTool.invoke() for display."""
    if not isinstance(value, dict):
        return str(value)
    inner = value.get("result")
    if inner is None:
        return str(value)
    # OpenApiClient returns (list[TextContent], dict) — use the dict part
    data = inner[1] if isinstance(inner, tuple) and len(inner) > 1 else inner
    try:
        return json.dumps(data, indent=2, ensure_ascii=False)
    except Exception:
        return str(data)


def _get_id(result: dict) -> int:
    """Extract the 'id' field from an MCPTool.invoke() result."""
    inner = result.get("result")
    data = inner[1] if isinstance(inner, tuple) and len(inner) > 1 else inner
    return data["id"]


async def main() -> None:
    # ── 1. Create and connect the OpenAPI client ──────────────────────────────
    #   connect() parses openapi.yaml and builds the tool registry; no HTTP yet.
    client = OpenApiClient(McpServerConfig(server_name=SERVER_NAME, server_path=SPEC_PATH, client_type="openapi"))

    logger.info(f"Loading OpenAPI spec from: {SPEC_PATH}")
    connected = await client.connect()
    if not connected:
        logger.info("Failed to load spec. Check that openapi.yaml exists and is valid.")
        return
    logger.info("Spec loaded.\n")

    # ── 2. Discover tools ─────────────────────────────────────────────────────
    tool_cards = await client.list_tools()
    logger.info(f"Discovered {len(tool_cards)} tool(s): {[c.name for c in tool_cards]}\n")

    # ── 3. Wrap every card in MCPTool ─────────────────────────────────────────
    tools: dict[str, MCPTool] = {
        card.name: MCPTool(mcp_client=client, tool_info=card)
        for card in tool_cards
    }

    # ── 4. Invoke tools via the standard Tool.invoke() interface ─────────────
    #   Each invoke() makes a real HTTP request to server.py via httpx.

    logger.info("Calling tools (HTTP requests → http://127.0.0.1:3004):\n")

    result = await tools["list_tasks"].invoke({})
    logger.info(f"list_tasks (empty):\n{_pretty(result)}\n")

    result = await tools["create_task"].invoke({"title": "Buy groceries"})
    logger.info(f"create_task 'Buy groceries':\n{_pretty(result)}\n")
    id1 = _get_id(result)

    result = await tools["create_task"].invoke({"title": "Call the dentist", "completed": False})
    logger.info(f"create_task 'Call the dentist':\n{_pretty(result)}\n")
    id2 = _get_id(result)

    result = await tools["create_task"].invoke({"title": "Finish quarterly report"})
    logger.info(f"create_task 'Quarterly report':\n{_pretty(result)}\n")

    result = await tools["list_tasks"].invoke({})
    logger.info(f"list_tasks (3 items):\n{_pretty(result)}\n")

    result = await tools["get_task"].invoke({"task_id": id2})
    logger.info(f"get_task({id2}):\n{_pretty(result)}\n")

    result = await tools["update_task"].invoke({"task_id": id1, "completed": True})
    logger.info(f"update_task({id1}, completed=True):\n{_pretty(result)}\n")

    result = await tools["delete_task"].invoke({"task_id": id2})
    logger.info(f"delete_task({id2}):\n{_pretty(result)}\n")

    result = await tools["list_tasks"].invoke({})
    logger.info(f"list_tasks (final):\n{_pretty(result)}\n")

    # ── 5. Disconnect ─────────────────────────────────────────────────────────
    await client.disconnect()
    logger.info("Disconnected.")


if __name__ == "__main__":
    asyncio.run(main())

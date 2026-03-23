#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
OpenAPI MCP Client Example
============================
Demonstrates how OpenApiClient converts an OpenAPI specification file
(openapi.yaml) into callable MCP tools — without running any MCP server.

Each HTTP endpoint defined in the spec becomes a tool that internally
uses httpx to call the live REST server (server.py).

Prerequisites:
    1. Start the REST server first:
           python server.py
    2. Then run this client:
           python client_direct.py

How it works:
    OpenApiClient reads openapi.yaml, parses every path+method combination
    into an OpenAPITool (via fastmcp), and then calls the real HTTP server
    using httpx when you invoke call_tool().
"""

import asyncio
import json
from pathlib import Path

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.tool.mcp.base import McpServerConfig
from openjiuwen.core.foundation.tool.mcp.client.openapi_client import OpenApiClient

# Path to the OpenAPI spec file (same directory as this script)
SPEC_PATH = str(Path(__file__).parent / "openapi.yaml")
SERVER_NAME = "task-openapi-server"


def _pretty(value) -> str:
    """Pretty-print a value returned by call_tool."""
    if value is None:
        return "None"
    serializable = list(value) if hasattr(value, "__iter__") and not isinstance(value, str) else value
    try:
        return json.dumps(serializable, indent=2, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(serializable)


async def main() -> None:
    logger.info(f"Loading OpenAPI spec from: {SPEC_PATH}")
    client = OpenApiClient(McpServerConfig(server_name=SERVER_NAME, server_path=SPEC_PATH, client_type="openapi"))

    # connect() parses the spec and builds the tool registry — no network call yet
    connected = await client.connect()
    if not connected:
        logger.info("Failed to load OpenAPI spec. Check that openapi.yaml exists and is valid.")
        return

    logger.info("OpenAPI spec loaded successfully!\n")

    # --- List generated tools ---
    tools = await client.list_tools()
    logger.info(f"Tools generated from spec ({len(tools)}):")
    for tool in tools:
        logger.info(f"  - {tool.name}")
    logger.info("")

    # --- Get info for a specific tool ---
    tool_info = await client.get_tool_info("list_tasks")
    if tool_info:
        logger.info(f"Tool info for 'list_tasks':\n  {tool_info.description[:200]}\n")

    # ── The following calls make real HTTP requests to server.py ─────────────
    logger.info("Calling tools (HTTP requests sent to http://127.0.0.1:3004):\n")

    # list_tasks — initially empty
    result = await client.call_tool("list_tasks", {})
    logger.info(f"list_tasks (empty):\n{_pretty(result)}\n")

    # create three tasks — capture each id for use in later calls
    task1 = await client.call_tool("create_task", {"title": "Buy groceries"})
    logger.info(f"create_task 'Buy groceries':\n{_pretty(task1)}\n")

    task2 = await client.call_tool("create_task", {"title": "Call the dentist", "completed": False})
    logger.info(f"create_task 'Call the dentist':\n{_pretty(task2)}\n")

    task3 = await client.call_tool("create_task", {"title": "Finish quarterly report"})
    logger.info(f"create_task 'Finish quarterly report':\n{_pretty(task3)}\n")

    # call_tool returns (list[TextContent], dict); index [1] is the parsed dict
    id1 = task1[1]["id"]
    id2 = task2[1]["id"]

    # list_tasks — now 3 items
    result = await client.call_tool("list_tasks", {})
    logger.info(f"list_tasks (3 items):\n{_pretty(result)}\n")

    # get a single task — use id from the first created task
    result = await client.call_tool("get_task", {"task_id": id1})
    logger.info(f"get_task({id1}):\n{_pretty(result)}\n")

    # update a task
    result = await client.call_tool("update_task", {"task_id": id1, "completed": True})
    logger.info(f"update_task({id1}, completed=True):\n{_pretty(result)}\n")

    # delete a task
    result = await client.call_tool("delete_task", {"task_id": id2})
    logger.info(f"delete_task({id2}):\n{_pretty(result)}\n")

    # final list
    result = await client.call_tool("list_tasks", {})
    logger.info(f"list_tasks (final):\n{_pretty(result)}\n")

    # --- Disconnect (closes internal httpx client) ---
    await client.disconnect()
    logger.info("Disconnected.")


if __name__ == "__main__":
    asyncio.run(main())

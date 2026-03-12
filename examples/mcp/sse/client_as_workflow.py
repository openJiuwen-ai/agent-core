#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
SSE — Workflow usage example
==============================
Demonstrates integrating an SSE MCPTool into an openjiuwen Workflow.

Workflow layout:
    Start(a, b) → ToolComponent[add] → End

The ToolComponent is bound to the 'add' MCPTool discovered from the SSE server.
The workflow is invoked with {"a": 7, "b": 3} and prints the sum.

Prerequisites:
    1. Start the server first:  python server.py
    2. Run this file:           python client_as_workflow.py
"""

import argparse
import asyncio
import os
from pathlib import Path

# ── CLI arguments ─────────────────────────────────────────────────────────────
_parser = argparse.ArgumentParser(
    description="SSE MCP workflow example",
    formatter_class=argparse.RawDescriptionHelpFormatter,
)
_parser.add_argument(
    "--visualize",
    action="store_true",
    default=False,
    help="Save a workflow diagram to workflow.png (requires network access to mermaid.ink)",
)
_args = _parser.parse_args()

# Enable workflow visualization only when requested
if _args.visualize:
    os.environ["WORKFLOW_DRAWABLE"] = "true"

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.tool.mcp.base import MCPTool, McpServerConfig
from openjiuwen.core.foundation.tool.mcp.client.sse_client import SseClient
from openjiuwen.core.workflow import (
    Workflow, WorkflowCard,
    Start, End,
    ToolComponent, ToolComponentConfig,
    create_workflow_session,
)

SERVER_URL = "http://127.0.0.1:3001/sse"
SERVER_NAME = "calculator-sse-server"


def build_workflow(add_tool: MCPTool) -> Workflow:
    """Construct a workflow that calls the 'add' calculator tool."""
    workflow = Workflow(card=WorkflowCard(
        id="sse_calculator_workflow",
        name="SSE Calculator Workflow",
        version="1.0.0",
    ))

    # ── Start: expose 'a' and 'b' from the invoke input ──────────────────────
    workflow.set_start_comp("start", Start(), inputs_schema={"a": "${a}", "b": "${b}"})

    # ── ToolComponent: bound to the SSE 'add' MCPTool ─────────────────────────
    #   inputs_schema maps workflow state → tool parameters.
    #   MCPTool.invoke() receives {"a": <value>, "b": <value>} and forwards them
    #   to the MCP server's 'add' tool.
    tool_comp = ToolComponent(ToolComponentConfig()).bind_tool(add_tool)
    workflow.add_workflow_comp(
        "tool", tool_comp,
        inputs_schema={"a": "${start.a}", "b": "${start.b}"},
    )

    # ── End: display the tool result ──────────────────────────────────────────
    #   ToolComponent output contains a 'data' field with the raw tool result.
    end = End({"response_template": "add(a, b) = {{result}}"})
    workflow.set_end_comp("end", end, inputs_schema={"result": "${tool.data}"})

    # ── Connections ───────────────────────────────────────────────────────────
    workflow.add_connection("start", "tool")
    workflow.add_connection("tool", "end")

    return workflow


async def main() -> None:
    # ── 1. Connect to SSE server ───────────────────────────────────────────────
    client = SseClient(McpServerConfig(server_name=SERVER_NAME, server_path=SERVER_URL, client_type="sse"))

    logger.info(f"Connecting to SSE server at {SERVER_URL} ...")
    connected = await client.connect()
    if not connected:
        logger.info("Failed to connect. Make sure server.py is running.")
        return
    logger.info("Connected.\n")

    # ── 2. Discover tools and wrap in MCPTool ─────────────────────────────────
    tool_cards = await client.list_tools()
    logger.info(f"Discovered {len(tool_cards)} tool(s): {[c.name for c in tool_cards]}\n")
    mcp_tools: dict[str, MCPTool] = {
        card.name: MCPTool(mcp_client=client, tool_info=card)
        for card in tool_cards
    }

    # ── 3. Build workflow with the 'add' tool ─────────────────────────────────
    workflow = build_workflow(mcp_tools["add"])

    # ── 3.5. Visualize the workflow (pass --visualize to enable) ─────────────
    if _args.visualize:
        png_bytes = workflow.draw(title="SSE Calculator Workflow", output_format="png")
        out_path = Path(__file__).parent / "workflow.png"
        out_path.write_bytes(png_bytes)
        logger.info(f"Workflow diagram saved to: {out_path}\n")

    # ── 4. Invoke the workflow ─────────────────────────────────────────────────
    session = create_workflow_session(session_id="sse_workflow_session")
    inputs = {"a": 7, "b": 3}
    logger.info(f"Invoking workflow with inputs: {inputs}")
    result = await workflow.invoke(inputs, session)
    logger.info(f"Workflow output: {result}\n")

    # ── 5. Disconnect ─────────────────────────────────────────────────────────
    await client.disconnect()
    logger.info("Disconnected.")


if __name__ == "__main__":
    asyncio.run(main())

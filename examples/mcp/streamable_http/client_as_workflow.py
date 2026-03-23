#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Streamable HTTP — Workflow usage example
==========================================
Demonstrates integrating a Streamable HTTP MCPTool into an openjiuwen Workflow.

Workflow layout:
    Start(content) → ToolComponent[add_note] → End

The ToolComponent is bound to the 'add_note' MCPTool discovered from the
Streamable HTTP server. The workflow is invoked with a note content string
and returns the server's confirmation message.

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
    description="Streamable HTTP MCP workflow example",
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
from openjiuwen.core.foundation.tool.mcp.client.streamable_http_client import StreamableHttpClient
from openjiuwen.core.workflow import (
    Workflow, WorkflowCard,
    Start, End,
    ToolComponent, ToolComponentConfig,
    create_workflow_session,
)

SERVER_URL = "http://127.0.0.1:3002/mcp"
SERVER_NAME = "notes-streamable-http-server"


def build_workflow(add_note_tool: MCPTool) -> Workflow:
    """Construct a workflow that adds a note via the Streamable HTTP MCP tool."""
    workflow = Workflow(card=WorkflowCard(
        id="streamable_http_notes_workflow",
        name="Streamable HTTP Notes Workflow",
        version="1.0.0",
    ))

    # ── Start: expose 'content' from the invoke input ─────────────────────────
    workflow.set_start_comp("start", Start(), inputs_schema={"content": "${content}"})

    # ── ToolComponent: bound to the 'add_note' MCPTool ────────────────────────
    #   MCPTool.invoke() sends {"content": <value>} to the Streamable HTTP
    #   server's 'add_note' tool and returns the server's response.
    tool_comp = ToolComponent(ToolComponentConfig()).bind_tool(add_note_tool)
    workflow.add_workflow_comp(
        "tool", tool_comp,
        inputs_schema={"content": "${start.content}"},
    )

    # ── End: display the server confirmation ──────────────────────────────────
    end = End({"response_template": "Note added: {{result}}"})
    workflow.set_end_comp("end", end, inputs_schema={"result": "${tool.data}"})

    # ── Connections ───────────────────────────────────────────────────────────
    workflow.add_connection("start", "tool")
    workflow.add_connection("tool", "end")

    return workflow


async def main() -> None:
    # ── 1. Connect to Streamable HTTP server ──────────────────────────────────
    client = StreamableHttpClient(McpServerConfig(server_name=SERVER_NAME, server_path=SERVER_URL,
                                                  client_type="streamable-http"))

    logger.info(f"Connecting to Streamable HTTP server at {SERVER_URL} ...")
    connected = await client.connect(timeout=30.0)
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

    # ── 3. Build workflow with the 'add_note' tool ────────────────────────────
    workflow = build_workflow(mcp_tools["add_note"])

    # ── 3.5. Visualize the workflow (pass --visualize to enable) ─────────────
    if _args.visualize:
        png_bytes = workflow.draw(title="Streamable HTTP Notes Workflow", output_format="png")
        out_path = Path(__file__).parent / "workflow.png"
        out_path.write_bytes(png_bytes)
        logger.info(f"Workflow diagram saved to: {out_path}\n")

    # ── 4. Invoke the workflow three times with different notes ────────────────
    notes = ["Buy groceries", "Call the dentist", "Finish quarterly report"]
    for i, note in enumerate(notes, start=1):
        session = create_workflow_session(session_id=f"streamable_http_session_{i}")
        inputs = {"content": note}
        logger.info(f"Invoking workflow [{i}] with content: '{note}'")
        result = await workflow.invoke(inputs, session)
        logger.info(f"Workflow output [{i}]: {result}\n")

    # ── 5. Disconnect ─────────────────────────────────────────────────────────
    await client.disconnect()
    logger.info("Disconnected.")


if __name__ == "__main__":
    asyncio.run(main())

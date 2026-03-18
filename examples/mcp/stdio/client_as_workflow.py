#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Stdio — Workflow usage example
================================
Demonstrates integrating a Stdio MCPTool into an openjiuwen Workflow.

Workflow layout:
    Start(text) → ToolComponent[word_count] → End

The StdioClient launches server.py as a subprocess automatically.
The ToolComponent is bound to the 'word_count' MCPTool.
The workflow is invoked with a text string and returns the word count.

Prerequisites:
    No separate server process needed — the subprocess is managed automatically.

Run:
    python client_as_workflow.py
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

# ── CLI arguments ─────────────────────────────────────────────────────────────
_parser = argparse.ArgumentParser(
    description="Stdio MCP workflow example",
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
from openjiuwen.core.foundation.tool.mcp.client.stdio_client import StdioClient
from openjiuwen.core.workflow import (
    Workflow, WorkflowCard,
    Start, End,
    ToolComponent, ToolComponentConfig,
    create_workflow_session,
)

SERVER_SCRIPT = str(Path(__file__).parent / "server.py")
SERVER_NAME = "text-processor-stdio-server"


def build_workflow(word_count_tool: MCPTool) -> Workflow:
    """Construct a workflow that counts words in the input text."""
    workflow = Workflow(card=WorkflowCard(
        id="stdio_text_workflow",
        name="Stdio Text Processing Workflow",
        version="1.0.0",
    ))

    # ── Start: expose 'text' from the invoke input ────────────────────────────
    workflow.set_start_comp("start", Start(), inputs_schema={"text": "${text}"})

    # ── ToolComponent: bound to the Stdio 'word_count' MCPTool ────────────────
    #   MCPTool.invoke() launches the subprocess (already connected) and calls
    #   the 'word_count' tool with {"text": <value>}.
    tool_comp = ToolComponent(ToolComponentConfig()).bind_tool(word_count_tool)
    workflow.add_workflow_comp(
        "tool", tool_comp,
        inputs_schema={"text": "${start.text}"},
    )

    # ── End: display the word count result ────────────────────────────────────
    end = End({"response_template": "word_count = {{result}}"})
    workflow.set_end_comp("end", end, inputs_schema={"result": "${tool.data}"})

    # ── Connections ───────────────────────────────────────────────────────────
    workflow.add_connection("start", "tool")
    workflow.add_connection("tool", "end")

    return workflow


async def main() -> None:
    # ── 1. Create and connect the Stdio client (launches server subprocess) ───
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

    # ── 2. Discover tools and wrap in MCPTool ─────────────────────────────────
    tool_cards = await client.list_tools()
    logger.info(f"Discovered {len(tool_cards)} tool(s): {[c.name for c in tool_cards]}\n")
    mcp_tools: dict[str, MCPTool] = {
        card.name: MCPTool(mcp_client=client, tool_info=card)
        for card in tool_cards
    }

    # ── 3. Build workflow with the 'word_count' tool ──────────────────────────
    workflow = build_workflow(mcp_tools["word_count"])

    # ── 3.5. Visualize the workflow (pass --visualize to enable) ─────────────
    if _args.visualize:
        png_bytes = workflow.draw(title="Stdio Text Processing Workflow", output_format="png")
        out_path = Path(__file__).parent / "workflow.png"
        out_path.write_bytes(png_bytes)
        logger.info(f"Workflow diagram saved to: {out_path}\n")

    # ── 4. Invoke the workflow ─────────────────────────────────────────────────
    session = create_workflow_session(session_id="stdio_workflow_session")
    sample_text = "The quick brown fox jumps over the lazy dog"
    inputs = {"text": sample_text}
    logger.info(f"Invoking workflow with text: '{sample_text}'")
    result = await workflow.invoke(inputs, session)
    logger.info(f"Workflow output: {result}\n")

    # ── 5. Disconnect (terminates subprocess) ─────────────────────────────────
    await client.disconnect()
    logger.info("Disconnected — subprocess terminated.")


if __name__ == "__main__":
    asyncio.run(main())

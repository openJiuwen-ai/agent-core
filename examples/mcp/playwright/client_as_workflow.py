#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Playwright — Workflow usage example
======================================
Demonstrates integrating a Playwright MCPTool into an openjiuwen Workflow.

Workflow layout:
    Start(url) → ToolComponent[browser_navigate] → End

The ToolComponent is bound to the 'browser_navigate' MCPTool discovered from
the Playwright SSE server. The workflow is invoked with a target URL and
returns the page title reported by the browser.

Prerequisites:
    1. Install playwright if needed:
           pip install playwright && playwright install chromium
    2. Start the server first:  python server.py
    3. Run this file:           python client_as_workflow.py
"""

import argparse
import asyncio
import os
from pathlib import Path

# ── CLI arguments ─────────────────────────────────────────────────────────────
_parser = argparse.ArgumentParser(
    description="Playwright MCP workflow example",
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
from openjiuwen.core.foundation.tool.mcp.client.playwright_client import PlaywrightClient
from openjiuwen.core.workflow import (
    Workflow, WorkflowCard,
    Start, End,
    ToolComponent, ToolComponentConfig,
    create_workflow_session,
)

SSE_SERVER_URL = "http://127.0.0.1:3003/sse"
SERVER_NAME = "browser-playwright-server"


def build_workflow(navigate_tool: MCPTool) -> Workflow:
    """Construct a workflow that navigates to a URL using the Playwright tool."""
    workflow = Workflow(card=WorkflowCard(
        id="playwright_browser_workflow",
        name="Playwright Browser Workflow",
        version="1.0.0",
    ))

    # ── Start: expose 'url' from the invoke input ─────────────────────────────
    workflow.set_start_comp("start", Start(), inputs_schema={"url": "${url}"})

    # ── ToolComponent: bound to the 'browser_navigate' MCPTool ───────────────
    #   MCPTool.invoke() sends {"url": <value>} to the Playwright server's
    #   'browser_navigate' tool, which opens the page and returns its title.
    tool_comp = ToolComponent(ToolComponentConfig()).bind_tool(navigate_tool)
    workflow.add_workflow_comp(
        "tool", tool_comp,
        inputs_schema={"url": "${start.url}"},
    )

    # ── End: display the navigation result ───────────────────────────────────
    end = End({"response_template": "Navigation result: {{result}}"})
    workflow.set_end_comp("end", end, inputs_schema={"result": "${tool.data}"})

    # ── Connections ───────────────────────────────────────────────────────────
    workflow.add_connection("start", "tool")
    workflow.add_connection("tool", "end")

    return workflow


async def main() -> None:
    # ── 1. Connect to Playwright SSE server ───────────────────────────────────
    client = PlaywrightClient(McpServerConfig(server_name=SERVER_NAME, server_path=SSE_SERVER_URL,
                                              client_type="playwright"))

    logger.info(f"Connecting to Playwright server at {SSE_SERVER_URL} ...")
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

    # ── 3. Build workflow with the 'browser_navigate' tool ────────────────────
    workflow = build_workflow(mcp_tools["browser_navigate"])

    # ── 3.5. Visualize the workflow (pass --visualize to enable) ─────────────
    if _args.visualize:
        png_bytes = workflow.draw(title="Playwright Browser Workflow", output_format="png")
        out_path = Path(__file__).parent / "workflow.png"
        out_path.write_bytes(png_bytes)
        logger.info(f"Workflow diagram saved to: {out_path}\n")

    # ── 4. Invoke the workflow ─────────────────────────────────────────────────
    session = create_workflow_session(session_id="playwright_workflow_session")
    target_url = "https://example.com"
    inputs = {"url": target_url}
    logger.info(f"Invoking workflow with URL: '{target_url}'")
    result = await workflow.invoke(inputs, session)
    logger.info(f"Workflow output: {result}\n")

    # ── 5. Disconnect ─────────────────────────────────────────────────────────
    await client.disconnect()
    logger.info("Disconnected.")


if __name__ == "__main__":
    asyncio.run(main())

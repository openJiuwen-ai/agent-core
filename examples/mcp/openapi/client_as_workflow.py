#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
OpenAPI — Workflow usage example
===================================
Demonstrates integrating an OpenAPI MCPTool into an openjiuwen Workflow.

Workflow layout:
    Start(title) → ToolComponent[create_task] → End

OpenApiClient reads openapi.yaml and converts each REST endpoint into an
McpToolCard. The ToolComponent is bound to the 'create_task' MCPTool, which
makes a real HTTP POST request to the running REST server when invoked.

Prerequisites:
    1. Start the REST server first:  python server.py
    2. Run this file:                python client_as_workflow.py
"""

import argparse
import asyncio
import os
from pathlib import Path

# ── CLI arguments ─────────────────────────────────────────────────────────────
_parser = argparse.ArgumentParser(
    description="OpenAPI MCP workflow example",
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
from openjiuwen.core.foundation.tool.mcp.client.openapi_client import OpenApiClient
from openjiuwen.core.workflow import (
    Workflow, WorkflowCard,
    Start, End,
    ToolComponent, ToolComponentConfig,
    create_workflow_session,
)

SPEC_PATH = str(Path(__file__).parent / "openapi.yaml")
SERVER_NAME = "task-openapi-server"


def build_workflow(create_task_tool: MCPTool) -> Workflow:
    """Construct a workflow that creates a task item via the OpenAPI MCP tool."""
    workflow = Workflow(card=WorkflowCard(
        id="openapi_task_workflow",
        name="OpenAPI Task Workflow",
        version="1.0.0",
    ))

    # ── Start: expose 'title' from the invoke input ───────────────────────────
    workflow.set_start_comp("start", Start(), inputs_schema={"title": "${title}"})

    # ── ToolComponent: bound to the 'create_task' MCPTool ─────────────────────
    #   MCPTool.invoke() delegates to OpenApiClient which sends a real HTTP POST
    #   to http://127.0.0.1:3004/tasks with {"title": <value>}.
    tool_comp = ToolComponent(ToolComponentConfig()).bind_tool(create_task_tool)
    workflow.add_workflow_comp(
        "tool", tool_comp,
        inputs_schema={"title": "${start.title}"},
    )

    # ── End: display the created task ─────────────────────────────────────────
    end = End({"response_template": "Created task: {{result}}"})
    workflow.set_end_comp("end", end, inputs_schema={"result": "${tool.data}"})

    # ── Connections ───────────────────────────────────────────────────────────
    workflow.add_connection("start", "tool")
    workflow.add_connection("tool", "end")

    return workflow


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

    # ── 2. Discover tools and wrap in MCPTool ─────────────────────────────────
    tool_cards = await client.list_tools()
    logger.info(f"Discovered {len(tool_cards)} tool(s): {[c.name for c in tool_cards]}\n")
    mcp_tools: dict[str, MCPTool] = {
        card.name: MCPTool(mcp_client=client, tool_info=card)
        for card in tool_cards
    }

    # ── 3. Build workflow with the 'create_task' tool ─────────────────────────
    workflow = build_workflow(mcp_tools["create_task"])

    # ── 3.5. Visualize the workflow (pass --visualize to enable) ─────────────
    if _args.visualize:
        png_bytes = workflow.draw(title="OpenAPI Task Workflow", output_format="png")
        out_path = Path(__file__).parent / "workflow.png"
        out_path.write_bytes(png_bytes)
        logger.info(f"Workflow diagram saved to: {out_path}\n")

    # ── 4. Invoke the workflow three times with different task titles ──────────
    task_titles = ["Buy groceries", "Call the dentist", "Finish quarterly report"]
    for i, title in enumerate(task_titles, start=1):
        session = create_workflow_session(session_id=f"openapi_workflow_session_{i}")
        inputs = {"title": title}
        logger.info(f"Invoking workflow [{i}] with title: '{title}'")
        result = await workflow.invoke(inputs, session)
        logger.info(f"Workflow output [{i}]: {result}\n")

    # ── 5. Disconnect ─────────────────────────────────────────────────────────
    await client.disconnect()
    logger.info("Disconnected.")


if __name__ == "__main__":
    asyncio.run(main())

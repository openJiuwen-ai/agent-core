#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Playwright — Runner / ResourceMgr usage example
=================================================
Demonstrates registering a Playwright MCP server, invoking browser tools, and
running a workflow entirely through the openjiuwen Runner and ResourceMgr.

The Playwright server exposes its tools over SSE; PlaywrightClient is selected
by setting client_type="playwright" in McpServerConfig.

Patterns demonstrated:
  1. MCP server lifecycle via Runner.resource_mgr.add_mcp_server() with
     client_type="playwright"
  2. Tool discovery and direct invocation via Runner.resource_mgr.get_mcp_tool()
  3. Workflow registration and execution via Runner.resource_mgr.add_workflow() /
     Runner.run_workflow()

Workflow layout:
    Start(url) → ToolComponent[browser_navigate] → End

Prerequisites:
    1. Install playwright if needed:
           pip install playwright && playwright install chromium
    2. Start the server first:  python server.py
    3. Run this file:           python client_as_resources_runner.py
"""

import argparse
import asyncio
import os
from pathlib import Path

# ── CLI arguments ─────────────────────────────────────────────────────────────
_parser = argparse.ArgumentParser(
    description="Playwright MCP runner example",
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
from openjiuwen.core.foundation.tool.mcp.base import McpServerConfig
from openjiuwen.core.runner import Runner
from openjiuwen.core.workflow import (
    Workflow, WorkflowCard,
    Start, End,
    ToolComponent, ToolComponentConfig,
)

SSE_SERVER_URL = "http://127.0.0.1:3003/sse"
SERVER_NAME = "browser-playwright-server"
SERVER_ID = "playwright-browser-server-01"
WORKFLOW_ID = "playwright_browser_workflow"


def build_workflow(navigate_tool) -> Workflow:
    """Construct the browser navigation workflow bound to 'browser_navigate'."""
    workflow = Workflow(card=WorkflowCard(
        id=WORKFLOW_ID,
        name="Playwright Browser Workflow",
        version="1.0.0",
    ))
    workflow.set_start_comp("start", Start(), inputs_schema={"url": "${url}"})
    tool_comp = ToolComponent(ToolComponentConfig()).bind_tool(navigate_tool)
    workflow.add_workflow_comp("tool", tool_comp, inputs_schema={"url": "${start.url}"})
    end = End({"response_template": "Navigation result: {{result}}"})
    workflow.set_end_comp("end", end, inputs_schema={"result": "${tool.data}"})
    workflow.add_connection("start", "tool")
    workflow.add_connection("tool", "end")
    return workflow


async def main() -> None:
    await Runner.start()
    try:
        # ── 1. Register Playwright MCP server with the resource manager ────────
        #   client_type="playwright" selects PlaywrightClient, which connects
        #   to the SSE endpoint exposed by server.py.
        config = McpServerConfig(
            server_id=SERVER_ID,
            server_name=SERVER_NAME,
            server_path=SSE_SERVER_URL,
            client_type="playwright",
        )
        logger.info(f"Registering Playwright MCP server '{SERVER_NAME}' at {SSE_SERVER_URL} ...")
        result = await Runner.resource_mgr.add_mcp_server(config, tag=["mcp", "browser"])
        if result.is_err():
            logger.error(f"Failed to register server: {result.msg()}")
            return
        logger.info(f"Server registered.\n")

        # ── 2. List all tools registered from the MCP server ──────────────────
        tool_infos = await Runner.resource_mgr.get_mcp_tool_infos(server_name=SERVER_NAME)
        tool_infos = tool_infos if isinstance(tool_infos, list) else [tool_infos]
        logger.info(f"Registered {len(tool_infos)} tool(s): {[t.name for t in tool_infos if t]}\n")

        # ── 3. Retrieve a specific tool and invoke it directly ─────────────────
        #   get_mcp_tool() may return a list or a single instance; unwrap it.
        navigate_result = await Runner.resource_mgr.get_mcp_tool(
            name="browser_navigate", server_name=SERVER_NAME
        )
        navigate_tool = navigate_result[0] if isinstance(navigate_result, list) else navigate_result
        if navigate_tool is None:
            logger.error("Tool 'browser_navigate' not found.")
            return
        direct_result = await navigate_tool.invoke({"url": "https://example.com"})
        logger.info(f"Direct tool invocation — browser_navigate = {direct_result}\n")

        # ── 4. Build workflow using the tool from the resource manager ─────────
        workflow = build_workflow(navigate_tool)

        # ── 4.5. Visualize the workflow (pass --visualize to enable) ──────────
        if _args.visualize:
            png_bytes = workflow.draw(title="Playwright Browser Workflow", output_format="png")
            out_path = Path(__file__).parent / "workflow.png"
            out_path.write_bytes(png_bytes)
            logger.info(f"Workflow diagram saved to: {out_path}\n")

        # ── 5. Register workflow with the resource manager ─────────────────────
        Runner.resource_mgr.add_workflow(
            WorkflowCard(id=WORKFLOW_ID, name="Playwright Browser Workflow", version="1.0.0"),
            lambda: workflow,
        )
        logger.info(f"Workflow '{WORKFLOW_ID}' registered.\n")

        # ── 6. Run workflow through the Runner by registered ID ────────────────
        target_url = "https://example.com"
        inputs = {"url": target_url}
        logger.info(f"Running workflow '{WORKFLOW_ID}' with URL: '{target_url}'")
        run_result = await Runner.run_workflow(WORKFLOW_ID, inputs=inputs)
        logger.info(f"Workflow result: {run_result}\n")

    finally:
        # ── 7. Clean up registered resources ──────────────────────────────────
        await Runner.resource_mgr.remove_mcp_server(server_name=SERVER_NAME)
        Runner.resource_mgr.remove_workflow(workflow_id=WORKFLOW_ID)
        await Runner.stop()
        logger.info("Runner stopped.")


if __name__ == "__main__":
    asyncio.run(main())

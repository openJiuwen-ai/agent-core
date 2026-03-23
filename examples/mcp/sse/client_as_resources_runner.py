#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
SSE — Runner / ResourceMgr usage example
==========================================
Demonstrates registering an MCP server, invoking tools, and running a workflow
entirely through the openjiuwen Runner and ResourceMgr — rather than managing
client connections manually.

Patterns demonstrated:
  1. MCP server lifecycle via Runner.resource_mgr.add_mcp_server() /
     remove_mcp_server()
  2. Tool discovery and direct invocation via Runner.resource_mgr.get_mcp_tool()
  3. Workflow registration and execution via Runner.resource_mgr.add_workflow() /
     Runner.run_workflow()

Workflow layout:
    Start(a, b) → ToolComponent[add] → End

Prerequisites:
    1. Start the server first:  python server.py
    2. Run this file:           python client_as_resources_runner.py
"""

import argparse
import asyncio
import os
from pathlib import Path

# ── CLI arguments ─────────────────────────────────────────────────────────────
_parser = argparse.ArgumentParser(
    description="SSE MCP runner example",
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

SERVER_URL = "http://127.0.0.1:3001/sse"
SERVER_NAME = "calculator-sse-server"
SERVER_ID = "sse-calc-server-01"
WORKFLOW_ID = "sse_calculator_workflow"


def build_workflow(add_tool) -> Workflow:
    """Construct the calculator workflow bound to the 'add' MCP tool."""
    workflow = Workflow(card=WorkflowCard(
        id=WORKFLOW_ID,
        name="SSE Calculator Workflow",
        version="1.0.0",
    ))
    workflow.set_start_comp("start", Start(), inputs_schema={"a": "${a}", "b": "${b}"})
    tool_comp = ToolComponent(ToolComponentConfig()).bind_tool(add_tool)
    workflow.add_workflow_comp("tool", tool_comp, inputs_schema={"a": "${start.a}", "b": "${start.b}"})
    end = End({"response_template": "add(a, b) = {{result}}"})
    workflow.set_end_comp("end", end, inputs_schema={"result": "${tool.data}"})
    workflow.add_connection("start", "tool")
    workflow.add_connection("tool", "end")
    return workflow


async def main() -> None:
    await Runner.start()
    try:
        # ── 1. Register MCP server with the resource manager ──────────────────
        #   Runner.resource_mgr.add_mcp_server() connects to the server,
        #   discovers all tools, and registers them internally.
        config = McpServerConfig(
            server_id=SERVER_ID,
            server_name=SERVER_NAME,
            server_path=SERVER_URL,
            client_type="sse",
        )
        logger.info(f"Registering SSE MCP server '{SERVER_NAME}' at {SERVER_URL} ...")
        result = await Runner.resource_mgr.add_mcp_server(config, tag=["mcp", "calculator"])
        if result.is_err():
            logger.error(f"Failed to register server: {result.msg()}")
            return
        logger.info(f"Server registered.\n")

        # ── 2. List all tools registered from the MCP server ──────────────────
        tool_infos = await Runner.resource_mgr.get_mcp_tool_infos(server_name=SERVER_NAME)
        tool_infos = tool_infos if isinstance(tool_infos, list) else [tool_infos]
        logger.info(f"Registered {len(tool_infos)} tool(s): {[t.name for t in tool_infos if t]}\n")

        # ── 3. Retrieve a specific tool and invoke it directly ─────────────────
        #   get_mcp_tool() may return a list or a single instance depending on
        #   how the query resolves; unwrap a list to get the Tool instance.
        add_tool_result = await Runner.resource_mgr.get_mcp_tool(name="add", server_name=SERVER_NAME)
        add_tool = add_tool_result[0] if isinstance(add_tool_result, list) else add_tool_result
        if add_tool is None:
            logger.error("Tool 'add' not found.")
            return
        direct_result = await add_tool.invoke({"a": 10, "b": 5})
        logger.info(f"Direct tool invocation — add(10, 5) = {direct_result}\n")

        # ── 4. Build workflow using the tool from the resource manager ─────────
        workflow = build_workflow(add_tool)

        # ── 4.5. Visualize the workflow (pass --visualize to enable) ──────────
        if _args.visualize:
            png_bytes = workflow.draw(title="SSE Calculator Workflow", output_format="png")
            out_path = Path(__file__).parent / "workflow.png"
            out_path.write_bytes(png_bytes)
            logger.info(f"Workflow diagram saved to: {out_path}\n")

        # ── 5. Register workflow with the resource manager ─────────────────────
        Runner.resource_mgr.add_workflow(
            WorkflowCard(id=WORKFLOW_ID, name="SSE Calculator Workflow", version="1.0.0"),
            lambda: workflow,
        )
        logger.info(f"Workflow '{WORKFLOW_ID}' registered.\n")

        # ── 6. Run workflow through the Runner by registered ID ────────────────
        inputs = {"a": 7, "b": 3}
        logger.info(f"Running workflow '{WORKFLOW_ID}' with inputs: {inputs}")
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

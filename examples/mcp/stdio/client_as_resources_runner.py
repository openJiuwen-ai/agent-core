#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Stdio — Runner / ResourceMgr usage example
============================================
Demonstrates registering a Stdio MCP server (subprocess), invoking tools, and
running a workflow entirely through the openjiuwen Runner and ResourceMgr.

The subprocess is launched automatically by the resource manager when
add_mcp_server() is called — no manual StdioClient setup needed.

Patterns demonstrated:
  1. Subprocess-based MCP server registration via Runner.resource_mgr.add_mcp_server()
     with client_type="stdio" and params
  2. Tool discovery and direct invocation via Runner.resource_mgr.get_mcp_tool()
  3. Workflow registration and execution via Runner.resource_mgr.add_workflow() /
     Runner.run_workflow()

Workflow layout:
    Start(text) → ToolComponent[word_count] → End

Prerequisites:
    No separate server process needed — the subprocess is managed automatically.

Run:
    python client_as_resources_runner.py
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

# ── CLI arguments ─────────────────────────────────────────────────────────────
_parser = argparse.ArgumentParser(
    description="Stdio MCP runner example",
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

SERVER_SCRIPT = str(Path(__file__).parent / "server.py")
SERVER_NAME = "text-processor-stdio-server"
SERVER_ID = "stdio-text-server-01"
WORKFLOW_ID = "stdio_text_workflow"


def build_workflow(word_count_tool) -> Workflow:
    """Construct the text processing workflow bound to the 'word_count' MCP tool."""
    workflow = Workflow(card=WorkflowCard(
        id=WORKFLOW_ID,
        name="Stdio Text Processing Workflow",
        version="1.0.0",
    ))
    workflow.set_start_comp("start", Start(), inputs_schema={"text": "${text}"})
    tool_comp = ToolComponent(ToolComponentConfig()).bind_tool(word_count_tool)
    workflow.add_workflow_comp("tool", tool_comp, inputs_schema={"text": "${start.text}"})
    end = End({"response_template": "word_count = {{result}}"})
    workflow.set_end_comp("end", end, inputs_schema={"result": "${tool.data}"})
    workflow.add_connection("start", "tool")
    workflow.add_connection("tool", "end")
    return workflow


async def main() -> None:
    await Runner.start()
    try:
        # ── 1. Register Stdio MCP server with the resource manager ────────────
        #   client_type="stdio" tells the resource manager to launch the script
        #   as a subprocess and communicate over stdin/stdout.
        config = McpServerConfig(
            server_id=SERVER_ID,
            server_name=SERVER_NAME,
            server_path="",           # unused for stdio; subprocess is set in params
            client_type="stdio",
            params={
                "command": sys.executable,
                "args": [SERVER_SCRIPT],
                "cwd": str(Path(__file__).parent),
                "encoding_error_handler": "strict",
            },
        )
        logger.info(f"Registering Stdio MCP server '{SERVER_NAME}' (subprocess: {SERVER_SCRIPT}) ...")
        result = await Runner.resource_mgr.add_mcp_server(config, tag=["mcp", "text-processing"])
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
        word_count_result = await Runner.resource_mgr.get_mcp_tool(name="word_count", server_name=SERVER_NAME)
        word_count_tool = word_count_result[0] if isinstance(word_count_result, list) else word_count_result
        if word_count_tool is None:
            logger.error("Tool 'word_count' not found.")
            return
        sample = "The quick brown fox jumps over the lazy dog"
        direct_result = await word_count_tool.invoke({"text": sample})
        logger.info(f"Direct tool invocation — word_count('{sample}') = {direct_result}\n")

        # ── 4. Build workflow using the tool from the resource manager ─────────
        workflow = build_workflow(word_count_tool)

        # ── 4.5. Visualize the workflow (pass --visualize to enable) ──────────
        if _args.visualize:
            png_bytes = workflow.draw(title="Stdio Text Processing Workflow", output_format="png")
            out_path = Path(__file__).parent / "workflow.png"
            out_path.write_bytes(png_bytes)
            logger.info(f"Workflow diagram saved to: {out_path}\n")

        # ── 5. Register workflow with the resource manager ─────────────────────
        Runner.resource_mgr.add_workflow(
            WorkflowCard(id=WORKFLOW_ID, name="Stdio Text Processing Workflow", version="1.0.0"),
            lambda: workflow,
        )
        logger.info(f"Workflow '{WORKFLOW_ID}' registered.\n")

        # ── 6. Run workflow through the Runner by registered ID ────────────────
        inputs = {"text": sample}
        logger.info(f"Running workflow '{WORKFLOW_ID}' with inputs: {inputs}")
        run_result = await Runner.run_workflow(WORKFLOW_ID, inputs=inputs)
        logger.info(f"Workflow result: {run_result}\n")

    finally:
        # ── 7. Clean up — also terminates the subprocess ──────────────────────
        await Runner.resource_mgr.remove_mcp_server(server_name=SERVER_NAME)
        Runner.resource_mgr.remove_workflow(workflow_id=WORKFLOW_ID)
        await Runner.stop()
        logger.info("Runner stopped — subprocess terminated.")


if __name__ == "__main__":
    asyncio.run(main())

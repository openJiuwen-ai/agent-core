#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
OpenAPI — Runner / ResourceMgr usage example
==============================================
Demonstrates registering an OpenAPI-backed MCP server, invoking REST-backed
tools, and running a workflow entirely through the openjiuwen Runner and
ResourceMgr.

OpenApiClient reads openapi.yaml and converts each REST endpoint into an
McpToolCard. add_mcp_server() with client_type="openapi" performs this
conversion automatically and registers all generated tools.

Patterns demonstrated:
  1. OpenAPI spec loading and tool registration via
     Runner.resource_mgr.add_mcp_server() with client_type="openapi"
  2. Tool discovery and direct invocation via Runner.resource_mgr.get_mcp_tool()
  3. Workflow registration and execution via Runner.resource_mgr.add_workflow() /
     Runner.run_workflow()

Workflow layout:
    Start(title) → ToolComponent[create_task] → End

Prerequisites:
    1. Start the REST server first:  python server.py
    2. Run this file:                python client_as_resources_runner.py
"""

import argparse
import asyncio
import os
from pathlib import Path

# ── CLI arguments ─────────────────────────────────────────────────────────────
_parser = argparse.ArgumentParser(
    description="OpenAPI MCP runner example",
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

SPEC_PATH = str(Path(__file__).parent / "openapi.yaml")
SERVER_NAME = "task-openapi-server"
SERVER_ID = "openapi-task-server-01"
WORKFLOW_ID = "openapi_task_workflow"


def build_workflow(create_task_tool) -> Workflow:
    """Construct the task workflow bound to the 'create_task' MCP tool."""
    workflow = Workflow(card=WorkflowCard(
        id=WORKFLOW_ID,
        name="OpenAPI Task Workflow",
        version="1.0.0",
    ))
    workflow.set_start_comp("start", Start(), inputs_schema={"title": "${title}"})
    tool_comp = ToolComponent(ToolComponentConfig()).bind_tool(create_task_tool)
    workflow.add_workflow_comp("tool", tool_comp, inputs_schema={"title": "${start.title}"})
    end = End({"response_template": "Created task: {{result}}"})
    workflow.set_end_comp("end", end, inputs_schema={"result": "${tool.data}"})
    workflow.add_connection("start", "tool")
    workflow.add_connection("tool", "end")
    return workflow


async def main() -> None:
    await Runner.start()
    try:
        # ── 1. Register OpenAPI MCP server with the resource manager ──────────
        #   client_type="openapi" tells the resource manager to use OpenApiClient,
        #   which reads the spec file at server_path and auto-generates one MCP
        #   tool per REST endpoint. Actual HTTP calls go to the running server.py.
        config = McpServerConfig(
            server_id=SERVER_ID,
            server_name=SERVER_NAME,
            server_path=SPEC_PATH,
            client_type="openapi",
        )
        logger.info(f"Registering OpenAPI MCP server '{SERVER_NAME}' from spec: {SPEC_PATH} ...")
        result = await Runner.resource_mgr.add_mcp_server(config, tag=["mcp", "task", "rest"])
        if result.is_err():
            logger.error(f"Failed to register server: {result.msg()}")
            return
        logger.info(f"Server registered.\n")

        # ── 2. List all tools registered from the OpenAPI spec ────────────────
        tool_infos = await Runner.resource_mgr.get_mcp_tool_infos(server_name=SERVER_NAME)
        tool_infos = tool_infos if isinstance(tool_infos, list) else [tool_infos]
        logger.info(f"Registered {len(tool_infos)} tool(s): {[t.name for t in tool_infos if t]}\n")

        # ── 3. Retrieve a specific tool and invoke it directly ─────────────────
        #   get_mcp_tool() may return a list or a single instance; unwrap it.
        create_task_result = await Runner.resource_mgr.get_mcp_tool(name="create_task", server_name=SERVER_NAME)
        create_task_tool = create_task_result[0] if isinstance(create_task_result, list) else create_task_result
        if create_task_tool is None:
            logger.error("Tool 'create_task' not found.")
            return
        direct_result = await create_task_tool.invoke({"title": "Direct tool invocation test"})
        logger.info(f"Direct tool invocation — create_task = {direct_result}\n")

        # ── 4. Build workflow using the tool from the resource manager ─────────
        workflow = build_workflow(create_task_tool)

        # ── 4.5. Visualize the workflow (pass --visualize to enable) ──────────
        if _args.visualize:
            png_bytes = workflow.draw(title="OpenAPI Task Workflow", output_format="png")
            out_path = Path(__file__).parent / "workflow.png"
            out_path.write_bytes(png_bytes)
            logger.info(f"Workflow diagram saved to: {out_path}\n")

        # ── 5. Register workflow with the resource manager ─────────────────────
        Runner.resource_mgr.add_workflow(
            WorkflowCard(id=WORKFLOW_ID, name="OpenAPI Task Workflow", version="1.0.0"),
            lambda: workflow,
        )
        logger.info(f"Workflow '{WORKFLOW_ID}' registered.\n")

        # ── 6. Run workflow through the Runner three times with different titles ─
        task_titles = ["Buy groceries", "Call the dentist", "Finish quarterly report"]
        for i, title in enumerate(task_titles, start=1):
            inputs = {"title": title}
            logger.info(f"Running workflow [{i}] with title: '{title}'")
            run_result = await Runner.run_workflow(WORKFLOW_ID, inputs=inputs)
            logger.info(f"Workflow result [{i}]: {run_result}\n")

    finally:
        # ── 7. Clean up registered resources ──────────────────────────────────
        await Runner.resource_mgr.remove_mcp_server(server_name=SERVER_NAME)
        Runner.resource_mgr.remove_workflow(workflow_id=WORKFLOW_ID)
        await Runner.stop()
        logger.info("Runner stopped.")


if __name__ == "__main__":
    asyncio.run(main())

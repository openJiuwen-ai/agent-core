# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Integration tests for ReActAgent with AIO Sandbox file operations.

These tests verify that an Agent can autonomously complete file operation tasks
using the AIO sandbox as its execution environment.

Requires:
- A running AIO sandbox service at http://localhost:8080
- Proper LLM configuration (api_key, api_base, model_name)
"""

import logging
import uuid

import pytest
import pytest_asyncio

from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent import AgentCard, ReActAgent, ReActAgentConfig
from openjiuwen.core.sys_operation import SysOperationCard

logger = logging.getLogger(__name__)


class TestAIOAgentFileOperation:
    """Test ReActAgent through AIO Sandbox for file operations.

    These tests validate the complete integration:
    - Agent receives a natural language task
    - Agent plans and calls fs tools (write_file, read_file)
    - Tools execute in the AIO sandbox
    - Agent returns the final result
    """

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Requires running AIO sandbox and LLM configuration")
    async def test_agent_write_and_read_file(self, aio_agent_op):
        """Agent writes a file to /tmp, reads it back, and returns the content.

        This test validates the full ReAct loop:
        1. Agent receives task: "Create file /tmp/agent_test.txt with content 'Hello from Agent'
           then read it and return the content"
        2. Agent calls fs.write_file tool
        3. Agent calls fs.read_file tool
        4. Agent returns the content in its response
        """
        # Step 1: Set up the Agent with fs tools
        rm = Runner.resource_mgr
        card_id = aio_agent_op.id

        # Create Agent
        agent_card = AgentCard(id="aio_file_agent", name="AIO File Agent")
        agent = ReActAgent(card=agent_card)

        # Configure Agent - uses environment variables or test config for LLM
        import os
        agent.configure(
            ReActAgentConfig()
            .configure_model_client(
                provider=os.environ.get("LLM_PROVIDER", "OpenAI"),
                api_key=os.environ.get("LLM_API_KEY", "test-key"),
                api_base=os.environ.get("LLM_API_BASE", "https://api.openai.com/v1"),
                model_name=os.environ.get("LLM_MODEL_NAME", "gpt-4o-mini"),
            )
            .configure_prompt_template([
                {
                    "role": "system",
                    "content": "You are a precise file operation assistant. "
                               "Use the provided tools to complete tasks."
                }
            ])
            .configure_max_iterations(5)
        )

        # Step 2: Mount fs tools onto the Agent
        read_tool_id = SysOperationCard.generate_tool_id(card_id, "fs", "read_file")
        read_tool = rm.get_tool(read_tool_id)
        assert read_tool is not None, f"Failed to get tool: {read_tool_id}"
        agent.ability_manager.add(read_tool.card)

        write_tool_id = SysOperationCard.generate_tool_id(card_id, "fs", "write_file")
        write_tool = rm.get_tool(write_tool_id)
        assert write_tool is not None, f"Failed to get tool: {write_tool_id}"
        agent.ability_manager.add(write_tool.card)

        # Step 3: Run the task
        query = (
            "Please perform the following task:\n"
            "1. Create a file at /tmp/agent_test.txt with the content 'Hello from Agent'\n"
            "2. Read the file you just created\n"
            "3. Return the content of the file in your response"
        )

        result = await Runner.run_agent(
            agent=agent,
            inputs={"query": query, "conversation_id": f"test_{uuid.uuid4().hex[:8]}"}
        )

        output = result.get("output", "") if isinstance(result, dict) else str(result)
        logger.info(f"Agent output: {output}")

        # Step 4: Verify the result
        assert "Hello from Agent" in output, f"Expected 'Hello from Agent' in output, got: {output}"

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Requires running AIO sandbox and LLM configuration")
    async def test_agent_list_and_search_files(self, aio_agent_op):
        """Agent lists files in /tmp and searches for specific patterns.

        This test validates that the Agent can:
        1. Use fs.list_files to explore the filesystem
        2. Use fs.search_files to find files by pattern
        3. Report findings accurately
        """
        # First, create some test files in the sandbox
        test_content = "searchable test content"
        test_files = [
            "/tmp/unique_test_file_001.txt",
            "/tmp/unique_test_file_002.txt",
            "/tmp/data_unique_xyz.txt",
        ]
        for path in test_files:
            await aio_agent_op.fs().write_file(path=path, content=test_content)

        # Set up the Agent
        rm = Runner.resource_mgr
        card_id = aio_agent_op.id

        agent_card = AgentCard(id="aio_search_agent", name="AIO Search Agent")
        agent = ReActAgent(card=agent_card)

        import os
        agent.configure(
            ReActAgentConfig()
            .configure_model_client(
                provider=os.environ.get("LLM_PROVIDER", "OpenAI"),
                api_key=os.environ.get("LLM_API_KEY", "test-key"),
                api_base=os.environ.get("LLM_API_BASE", "https://api.openai.com/v1"),
                model_name=os.environ.get("LLM_MODEL_NAME", "gpt-4o-mini"),
            )
            .configure_prompt_template([
                {
                    "role": "system",
                    "content": "You are a file system assistant. Use tools to explore and report findings."
                }
            ])
            .configure_max_iterations(5)
        )

        # Mount search and list tools
        list_tool_id = SysOperationCard.generate_tool_id(card_id, "fs", "list_files")
        list_tool = rm.get_tool(list_tool_id)
        assert list_tool is not None
        agent.ability_manager.add(list_tool.card)

        search_tool_id = SysOperationCard.generate_tool_id(card_id, "fs", "search_files")
        search_tool = rm.get_tool(search_tool_id)
        assert search_tool is not None
        agent.ability_manager.add(search_tool.card)

        # Run the task
        query = (
            "Please perform the following:\n"
            "1. List all files in /tmp directory\n"
            "2. Search for files matching pattern '*unique*' in /tmp\n"
            "3. Report how many matching files you found"
        )

        result = await Runner.run_agent(
            agent=agent,
            inputs={"query": query, "conversation_id": f"test_{uuid.uuid4().hex[:8]}"}
        )

        output = result.get("output", "") if isinstance(result, dict) else str(result)
        logger.info(f"Agent output: {output}")

        # Verify Agent found the files
        assert "unique" in output.lower() or "3" in output, \
            f"Expected Agent to find files with 'unique' pattern, got: {output}"

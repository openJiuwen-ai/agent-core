# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import os
import unittest
import pytest
from openjiuwen.core.workflow.components.llm.react import ReActAgentComp, ReActAgentCompConfig
from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig, ModelRequestConfig
from openjiuwen.core.common.logging import logger
from openjiuwen.core.workflow import Workflow, Start, End, create_workflow_session, WorkflowCard
from openjiuwen.core.foundation.tool import LocalFunction, ToolCard
from openjiuwen.core.runner import Runner


API_BASE = os.getenv("API_BASE", "https://api.openai.com/v1")
API_KEY = os.getenv("API_KEY", "sk-fake")
MODEL_NAME = os.getenv("MODEL_NAME", "")
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "OpenAI")


def test_component_creation():
    """Test that we can create the ReAct agent workflow component"""
    # Create a basic configuration
    config = ReActAgentCompConfig(
        model_client_config=ModelClientConfig(client_provider=MODEL_PROVIDER, api_key=API_KEY, api_base=API_BASE),
        model_config_obj=ModelRequestConfig(model_name=MODEL_NAME),
        max_iterations=5,
    )

    # Create the component
    component = ReActAgentComp(config=config)

    assert component is not None
    assert component.executable is not None


@pytest.mark.asyncio
async def test_executable_methods():
    """Test that executable has required methods"""
    config = ReActAgentCompConfig(
        model_client_config=ModelClientConfig(client_provider=MODEL_PROVIDER, api_key=API_KEY, api_base=API_BASE),
        model_config_obj=ModelRequestConfig(model_name=MODEL_NAME),
        max_iterations=5,
    )

    component = ReActAgentComp(config=config)
    executable = component.executable

    # Check that required methods exist
    assert hasattr(executable, "invoke")
    assert hasattr(executable, "stream")
    assert hasattr(executable, "collect")
    assert hasattr(executable, "transform")


@unittest.skip("skip system test")
@pytest.mark.asyncio
async def test_react_agent_in_workflow():
    """Test ReActAgentComp in a workflow with Start -> ReActAgent -> End components

    Note: This test uses real LLM API and is skipped by default.
    Set environment variables and remove @unittest.skip to run.
    """
    # Store original environment variable value to restore later
    original_ssl_verify = os.environ.get("HTTP_SSL_VERIFY")

    # Set environment variable to disable SSL verification
    os.environ["HTTP_SSL_VERIFY"] = "false"

    try:
        # Create a workflow
        flow = Workflow()

        # Create components
        start_component = Start()
        end_component = End({"responseTemplate": "{{output}}"})

        # Create ReActAgentComp component configuration
        config = ReActAgentCompConfig(
            model_client_config=ModelClientConfig(
                client_provider=MODEL_PROVIDER,
                api_key=API_KEY,
                api_base=API_BASE,
                timeout=30,
                verify_ssl=False,
            ),
            model_config_obj=ModelRequestConfig(model_name=MODEL_NAME),
            max_iterations=3,  # Limit iterations for testing
            model_name=MODEL_NAME,
            model_provider=MODEL_PROVIDER,
            api_key=API_KEY,
            api_base=API_BASE,
        )
        react_component = ReActAgentComp(config=config)

        # Set up the workflow connections
        flow.set_start_comp("s", start_component, inputs_schema={"query": "${query}"})
        flow.set_end_comp("e", end_component, inputs_schema={"output": "${react.output}"})
        flow.add_workflow_comp("react", react_component, inputs_schema={"query": "${s.query}"})

        # Add connections: start -> react -> end
        flow.add_connection("s", "react")
        flow.add_connection("react", "e")

        # Create session context
        context = create_workflow_session()

        # Invoke the workflow with a test query
        result = await flow.invoke(inputs={"query": "What is the capital of France?"}, session=context)
        logger.info(f"Workflow invoke result: {result}")

        # Basic assertions to verify the workflow ran
        assert result is not None
        logger.info("✓ ReActAgentComp in workflow executed successfully!")
    finally:
        # Restore original environment variable value
        if original_ssl_verify is not None:
            os.environ["HTTP_SSL_VERIFY"] = original_ssl_verify
        else:
            if "HTTP_SSL_VERIFY" in os.environ:
                del os.environ["HTTP_SSL_VERIFY"]


@unittest.skip("skip system test")
@pytest.mark.asyncio
async def test_react_agent_with_add_tool_in_workflow():
    """Test ReActAgentComp with tool calling (add tool) in workflow

    Scenario: User requests calculation, Agent must call add tool to complete the task.
    This test verifies the complete tool calling workflow:
    1. Tool registration with Runner.resource_mgr
    2. Tool ability added to ReActAgent
    3. Tool execution during ReAct loop
    4. Final result contains the calculated value

    Note: This test uses real LLM API and is skipped by default.
    Set environment variables and remove @unittest.skip to run.
    """
    # Store original environment variable value to restore later
    original_ssl_verify = os.environ.get("HTTP_SSL_VERIFY")
    os.environ["HTTP_SSL_VERIFY"] = "false"

    try:
        # 1. Create the add tool
        add_tool = LocalFunction(
            card=ToolCard(
                name="add",
                description="加法运算，计算两个数的和",
                input_params={
                    "type": "object",
                    "properties": {
                        "a": {"description": "第一个加数", "type": "number"},
                        "b": {"description": "第二个加数", "type": "number"},
                    },
                    "required": ["a", "b"],
                },
            ),
            func=lambda a, b: a + b,
        )

        # 2. Register tool to Runner.resource_mgr (before creating component)
        Runner.resource_mgr.add_tool(add_tool)

        # 3. Create a workflow
        flow = Workflow()

        # Create components
        start_component = Start()
        end_component = End({"responseTemplate": "{{output}}"})

        # Create ReActAgentComp component configuration with system prompt
        # that instructs the agent to use the add tool
        config = ReActAgentCompConfig(
            model_client_config=ModelClientConfig(
                client_provider=MODEL_PROVIDER,
                api_key=API_KEY,
                api_base=API_BASE,
                timeout=30,
                verify_ssl=False,
            ),
            model_config_obj=ModelRequestConfig(model_name=MODEL_NAME),
            max_iterations=5,  # Allow enough iterations for tool calling
            model_name=MODEL_NAME,
            model_provider=MODEL_PROVIDER,
            api_key=API_KEY,
            api_base=API_BASE,
        )
        react_component = ReActAgentComp(config=config)

        # 4. Add tool to agent's ability list via public ability_manager property
        react_component.executable.ability_manager.add(add_tool.card)

        # 5. Set up the workflow connections
        flow.set_start_comp("s", start_component, inputs_schema={"query": "${query}"})
        flow.set_end_comp("e", end_component, inputs_schema={"output": "${react.output}"})
        flow.add_workflow_comp("react", react_component, inputs_schema={"query": "${s.query}"})

        # Add connections: start -> react -> end
        flow.add_connection("s", "react")
        flow.add_connection("react", "e")

        # 6. Create session context
        context = create_workflow_session()

        # 7. Invoke the workflow with a calculation query
        # The query explicitly requests using the add tool
        query = "使用 add 工具计算 123 + 456"
        result = await flow.invoke(inputs={"query": query}, session=context)

        logger.info(f"Workflow invoke result with add tool: {result}")

        # 8. Verify the result
        # result is a WorkflowOutput object with 'result' and 'state' attributes
        assert result is not None, "Workflow result should not be None"
        assert hasattr(result, "result"), "WorkflowOutput should have 'result' attribute"
        assert isinstance(result.result, dict), f"Result should be a dictionary, got {type(result.result)}"
        assert "response" in result.result, "Result should contain 'response' key"
        # The result should contain 579 (123 + 456 = 579)
        # This number is unlikely to be guessed by LLM directly, must call the tool
        assert "579" in result.result["response"], f"Expected '579' in response, got: {result.result['response']}"

        logger.info("✓ ReActAgentComp with add tool in workflow executed successfully!")

    finally:
        # Restore original environment variable value
        if original_ssl_verify is not None:
            os.environ["HTTP_SSL_VERIFY"] = original_ssl_verify
        else:
            if "HTTP_SSL_VERIFY" in os.environ:
                del os.environ["HTTP_SSL_VERIFY"]


@unittest.skip("skip system test")
@pytest.mark.asyncio
async def test_react_agent_stream_with_add_tool_in_workflow():
    """Test ReActAgentComp with streaming output and tool calling in workflow

    This test is similar to test_llm_agent_stream_with_real_plugin, but uses
    ReActAgentComp in a workflow context with streaming mode.

    Scenario: User requests calculation, Agent must call add tool to complete the task.
    The test verifies streaming output while the agent executes the ReAct loop.

    Note: This test uses real LLM API and is skipped by default.
    Set environment variables and remove @unittest.skip to run.
    """
    # Store original environment variable value to restore later
    original_ssl_verify = os.environ.get("HTTP_SSL_VERIFY")
    os.environ["HTTP_SSL_VERIFY"] = "false"

    try:
        # 1. Create the add tool
        add_tool = LocalFunction(
            card=ToolCard(
                name="add",
                description="加法运算，计算两个数的和",
                input_params={
                    "type": "object",
                    "properties": {
                        "a": {"description": "第一个加数", "type": "number"},
                        "b": {"description": "第二个加数", "type": "number"},
                    },
                    "required": ["a", "b"],
                },
            ),
            func=lambda a, b: a + b,
        )

        # 2. Register tool to Runner.resource_mgr (before creating component)
        Runner.resource_mgr.add_tool(add_tool)

        # 3. Create a workflow
        flow = Workflow()

        # Create components
        start_component = Start()
        end_component = End({"responseTemplate": "{{output}}"})

        # Create ReActAgentComp component configuration
        config = ReActAgentCompConfig(
            model_client_config=ModelClientConfig(
                client_provider=MODEL_PROVIDER,
                api_key=API_KEY,
                api_base=API_BASE,
                timeout=30,
                verify_ssl=False,
            ),
            model_config_obj=ModelRequestConfig(model_name=MODEL_NAME),
            max_iterations=5,  # Allow enough iterations for tool calling
            model_name=MODEL_NAME,
            model_provider=MODEL_PROVIDER,
            api_key=API_KEY,
            api_base=API_BASE,
        )
        react_component = ReActAgentComp(config=config)

        # 4. Add tool to agent's ability list via public ability_manager property
        react_component.executable.ability_manager.add(add_tool.card)

        # 5. Set up the workflow connections
        flow.set_start_comp("s", start_component, inputs_schema={"query": "${query}"})
        flow.set_end_comp("e", end_component, inputs_schema={"output": "${react.output}"})
        flow.add_workflow_comp("react", react_component, inputs_schema={"query": "${s.query}"})

        # Add connections: start -> react -> end
        flow.add_connection("s", "react")
        flow.add_connection("react", "e")

        # 6. Create session context
        context = create_workflow_session()

        # 7. Stream the workflow with a calculation query
        query = "使用 add 工具计算 123 + 456"
        stream_result = flow.stream(inputs={"query": query}, session=context)

        # 8. Iterate through streaming results
        collected_chunks = []
        async for chunk in stream_result:
            logger.info(f"Stream chunk received: {chunk}")
            collected_chunks.append(chunk)

        # 9. Verify that we received streaming chunks
        assert len(collected_chunks) > 0, "Should receive at least one streaming chunk"

        # 10. Verify the final result contains the expected calculation
        # Look for the workflow_final type chunk which contains the final result
        final_result_chunk = None
        for chunk in collected_chunks:
            # Check if this is the final workflow output chunk
            if hasattr(chunk, "type") and chunk.type == "workflow_final":
                final_result_chunk = chunk
                break
            # Or if it's a dict with the final result
            if isinstance(chunk, dict) and "response" in chunk:
                final_result_chunk = chunk
                break

        # If no explicit final chunk found, use the last chunk
        if final_result_chunk is None:
            final_result_chunk = collected_chunks[-1]

        logger.info(f"Final result chunk: {final_result_chunk}")

        # Check that the final result contains 579 (123 + 456 = 579)
        if hasattr(final_result_chunk, "payload") and isinstance(final_result_chunk.payload, dict):
            # OutputSchema with payload dict
            assert "response" in final_result_chunk.payload, (
                f"Final chunk payload should contain 'response' key, got: {final_result_chunk.payload}"
            )
            assert "579" in final_result_chunk.payload["response"], (
                f"Expected '579' in response, got: {final_result_chunk.payload['response']}"
            )
        elif hasattr(final_result_chunk, "result") and isinstance(final_result_chunk.result, dict):
            # WorkflowOutput with result dict
            assert "response" in final_result_chunk.result, (
                f"Final chunk result should contain 'response' key, got: {final_result_chunk.result}"
            )
            assert "579" in final_result_chunk.result["response"], (
                f"Expected '579' in response, got: {final_result_chunk.result['response']}"
            )
        elif isinstance(final_result_chunk, dict):
            # Plain dict
            assert "response" in final_result_chunk or "output" in final_result_chunk, (
                f"Final chunk should contain 'response' or 'output' key, got: {final_result_chunk}"
            )
            response_value = final_result_chunk.get("response", final_result_chunk.get("output", ""))
            assert "579" in str(response_value), f"Expected '579' in response, got: {response_value}"
        else:
            # Fallback: convert to string and check
            chunk_str = str(final_result_chunk)
            assert "579" in chunk_str, f"Expected '579' in final chunk, got: {chunk_str}"

        logger.info("✓ ReActAgentComp stream with add tool in workflow executed successfully!")

    finally:
        # Restore original environment variable value
        if original_ssl_verify is not None:
            os.environ["HTTP_SSL_VERIFY"] = original_ssl_verify
        else:
            if "HTTP_SSL_VERIFY" in os.environ:
                del os.environ["HTTP_SSL_VERIFY"]


@unittest.skip("skip system test")
@pytest.mark.asyncio
async def test_react_agent_comp_stream():
    model_client = ModelClientConfig(
        client_id="demo1",
        client_provider="SiliconFlow",
        api_key="sk",
        api_base="http://127.0.0.1:8088/v1/chat/completions",
        timeout=30,
        verify_ssl=False
    )
    model_config = ModelRequestConfig(
        model="qwen-plus",
        temperature=0.7,
        top_p=0.9
    )

    config = ReActAgentCompConfig(
        model_client_config=model_client,
        model_config_obj=model_config,
        max_iterations=5
    )  
    react_component = ReActAgentComp(config=config)

    flow = Workflow(card=WorkflowCard(name="test_react_agent_comp_003", id="react_agent_comp_001", version="1.0"))
    start_component = Start()
    end_component = End({"responseTemplate": "{{output}}"})

    flow.set_start_comp("s", start_component, inputs_schema={"query": "${query}"})
    flow.set_end_comp("e", end_component, stream_inputs_schema={"output": "${react}"})
    flow.add_workflow_comp("react", react_component, inputs_schema={"query": "${s.query}"})

    flow.add_connection("s", "react")
    flow.add_stream_connection("react", "e")

    context = create_workflow_session()

    result = await flow.invoke(inputs={"query": "生成一句关于月亮的诗句"}, session=context)
    logger.info(f"Workflow invoke result: {result}")
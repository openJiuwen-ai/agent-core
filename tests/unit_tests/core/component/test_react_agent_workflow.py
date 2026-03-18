# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import os
import pytest
from openjiuwen.core.workflow.components.llm.react import ReActAgentComp, ReActAgentCompConfig
from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig, ModelRequestConfig
from openjiuwen.core.common.logging import logger


API_BASE = os.getenv("API_BASE", "mock://api.openai.com/v1")
API_KEY = os.getenv("API_KEY", "sk-fake")
MODEL_NAME = os.getenv("MODEL_NAME", "gpt-3.5-turbo")
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "OpenAI")


def test_component_creation():
    """Test that we can create the ReAct agent workflow component"""
    # Create a basic configuration
    config = ReActAgentCompConfig(
        model_client_config=ModelClientConfig(
            client_provider=MODEL_PROVIDER,
            api_key=API_KEY,
            api_base=API_BASE
        ),
        model_config_obj=ModelRequestConfig(model_name=MODEL_NAME),
        max_iterations=5
    )

    # Create the component
    component = ReActAgentComp(config=config)

    assert component is not None
    assert component.executable is not None


@pytest.mark.asyncio
async def test_executable_methods():
    """Test that executable has required methods"""
    config = ReActAgentCompConfig(
        model_client_config=ModelClientConfig(
            client_provider=MODEL_PROVIDER,
            api_key=API_KEY,
            api_base=API_BASE
        ),
        model_config_obj=ModelRequestConfig(model_name=MODEL_NAME),
        max_iterations=5
    )

    component = ReActAgentComp(config=config)
    executable = component.executable

    # Check that required methods exist
    assert hasattr(executable, 'invoke')
    assert hasattr(executable, 'stream')
    assert hasattr(executable, 'collect')
    assert hasattr(executable, 'transform')


@pytest.mark.asyncio
async def test_react_agent_in_workflow():
    """Test ReActAgentComp in a workflow with Start -> ReActAgent -> End components"""
    from openjiuwen.core.workflow import Workflow, Start, End, create_workflow_session

    # Store original environment variable value to restore later
    original_ssl_verify = os.environ.get('HTTP_SSL_VERIFY')

    # Set environment variable to disable SSL verification
    os.environ['HTTP_SSL_VERIFY'] = 'false'

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
            api_base=API_BASE
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
            os.environ['HTTP_SSL_VERIFY'] = original_ssl_verify
        else:
            if 'HTTP_SSL_VERIFY' in os.environ:
                del os.environ['HTTP_SSL_VERIFY']
#!/usr/bin/env python3
"""
Simple test to verify the HTTP Request component can be imported and instantiated
"""
import logging
import os
import pytest
from openjiuwen.core.workflow import (
    HTTPRequestComponent,
    HttpComponentConfig,
    HttpRequestParamConfig,
    HttpAdvancedOptionsConfig,
    Workflow,
    Start,
    End,
    create_workflow_session
)

# Set up logging for the test module
logger = logging.getLogger(__name__)


def test_component_creation():
    """Test that we can create the HTTP Request component"""
    logger.info("Testing HTTP Request Component creation...")

    # Create a basic configuration
    config = HttpComponentConfig(
        request_params=HttpRequestParamConfig(
            url="https://httpbin.org/get",
            method="GET"
        )
    )

    # Create the component
    component = HTTPRequestComponent(config=config)

    logger.info("✓ HTTP Request Component created successfully!")
    logger.info(f"Component type: {type(component)}")
    logger.info(f"Config URL: {component.config.request_params.url}")

    assert component is not None
    assert component.config.request_params.url == "https://httpbin.org/get"


@pytest.mark.asyncio
async def test_executable_creation():
    """Test that we can create the executable"""
    logger.info("Testing executable creation...")

    # Create a basic configuration
    config = HttpComponentConfig(
        request_params=HttpRequestParamConfig(
            url="https://httpbin.org/get",
            method="GET"
        )
    )

    # Create the component
    component = HTTPRequestComponent(config=config)

    # Get the executable
    executable = component.executable

    logger.info("✓ Executable created successfully!")
    logger.info(f"Executable type: {type(executable)}")

    assert executable is not None


@pytest.mark.asyncio
async def test_start_http_request_end_in_workflow():
    """Test a workflow with Start -> HTTPRequest -> End components"""
    logger.info("Testing Start -> HTTPRequest -> End workflow...")

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

        # Create HTTP Request component configuration with SSL disabled for testing
        config = HttpComponentConfig(
            request_params=HttpRequestParamConfig(
                url="{{url}}",  # URL will be dynamically set from input named 'url'
                method="GET"
            ),
            advanced_options=HttpAdvancedOptionsConfig(ignore_ssl_issues=True)  # Disable SSL verification for testing
        )
        http_component = HTTPRequestComponent(config=config)

        # Set up the workflow connections
        flow.set_start_comp("s", start_component, inputs_schema={"query": "${query}"})
        flow.set_end_comp("e", end_component, inputs_schema={"output": "${http.output}"})
        flow.add_workflow_comp("http", http_component, inputs_schema={"url": "${s.query}"})

        # Add connections: start -> http -> end
        flow.add_connection("s", "http")
        flow.add_connection("http", "e")

        # Create session context
        context = create_workflow_session()

        # Invoke the workflow with a test URL
        result = await flow.invoke(inputs={"query": "https://httpbin.org/get?test=value"}, session=context)
        logger.info(f"Workflow invoke result: {result}")

        # Basic assertions to verify the workflow ran successfully
        assert result is not None
        logger.info("✓ Start -> HTTPRequest -> End workflow executed successfully!")
    finally:
        # Restore original environment variable value
        if original_ssl_verify is not None:
            os.environ['HTTP_SSL_VERIFY'] = original_ssl_verify
        else:
            del os.environ['HTTP_SSL_VERIFY']
#!/usr/bin/env python3
"""
Simple test to verify the HTTP Request component can be imported and instantiated
"""
import ast
import json
import logging
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.workflow import (
    HTTPRequestComponent,
    HttpComponentConfig,
    HttpRequestParamConfig,
    HttpAdvancedOptionsConfig,
    HttpRequestBodyConfig,
    HttpRetryConfig,
    HttpContentType,
    Workflow,
    Start,
    End,
    create_workflow_session
)
from openjiuwen.core.common.exception.errors import BaseError, ExecutionError
from openjiuwen.core.workflow.base import WorkflowCard

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


@pytest.mark.asyncio
async def test_retry_count_and_timeout_session_handling():
    """
    Test that verifies retry count and timeout configuration in a workflow.

    This test ensures that:
    1. The retry configuration is properly set up with max_retries and timeout
    2. The connector is recreated on each retry attempt (fix for "Session is closed" bug)
    3. The component handles session lifecycle correctly during retries

    DevOps reported: After the second retry, the session is closed.
    This was caused by creating the connector outside the retry loop.
    When the ClientSession exited, it closed the connector, causing subsequent
    retries to fail with "Session is closed" error.

    Fix: Create a new connector inside the retry loop for each attempt.

    Uses mocked aiohttp to avoid real network calls while still exercising
    the retry loop and connector-creation logic.
    """
    flow = Workflow()
    start_component = Start()
    end_component = End({"responseTemplate": "{{output}}"})

    config = HttpComponentConfig(
        request_params=HttpRequestParamConfig(
            url="{{url}}",
            method="GET",
            timeout=5.0,
            advanced_options=HttpAdvancedOptionsConfig(
                timeout=10000,
                ignore_ssl_issues=True,
            ),
            retry_config=HttpRetryConfig(
                enabled=True,
                max_retries=2,
                retry_delay=1,  # 1ms — fast in tests
                retry_on_status_codes=[500, 502, 503, 504, 429],
                backoff_type="fixed",
            ),
        )
    )
    http_component = HTTPRequestComponent(config=config)

    flow.set_start_comp("s", start_component, inputs_schema={"query": "${query}"})
    flow.set_end_comp("e", end_component, inputs_schema={"output": "${http.output}"})
    flow.add_workflow_comp("http", http_component, inputs_schema={"url": "${s.query}"})
    flow.add_connection("s", "http")
    flow.add_connection("http", "e")

    # Verify config is wired up correctly
    assert config.request_params.timeout == 5.0
    assert config.request_params.advanced_options.timeout == 10000
    assert config.request_params.retry_config.enabled is True
    assert config.request_params.retry_config.max_retries == 2

    connector_create_count = 0

    class _CountingConnector:
        """Tracks how many times a fresh connector is created."""
        def __init__(self, *args, **kwargs):
            nonlocal connector_create_count
            connector_create_count += 1

    async def _fake_iter_chunked(size):
        yield b"Internal Server Error"

    mock_response = MagicMock()
    mock_response.status = 500
    mock_response.reason = "Internal Server Error"
    mock_response.url = "https://example.com/status/500"
    mock_response.headers = {"content-type": "text/plain; charset=utf-8"}
    mock_response.content.iter_chunked = _fake_iter_chunked

    mock_request_ctx = MagicMock()
    mock_request_ctx.__aenter__ = AsyncMock(return_value=mock_response)
    mock_request_ctx.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.request = MagicMock(return_value=mock_request_ctx)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    context = create_workflow_session()
    os.environ['HTTP_SSL_VERIFY'] = 'false'
    try:
        with patch(
            "openjiuwen.core.workflow.components.tool.http.http_request_component.aiohttp.TCPConnector",
            _CountingConnector,
        ), patch(
            "openjiuwen.core.workflow.components.tool.http.http_request_component.aiohttp.ClientSession",
            return_value=mock_session,
        ):
            result = await flow.invoke(
                inputs={"query": "https://example.com/status/500"},
                session=context,
            )
    finally:
        del os.environ['HTTP_SSL_VERIFY']

    # Connector must be recreated for each attempt: 1 initial + 2 retries = 3 total.
    # The original bug reused a closed connector, so this count verifies the fix.
    assert connector_create_count == 3, (
        f"Expected connector to be created 3 times (1 initial + 2 retries), "
        f"got {connector_create_count}. "
        "The 'Session is closed' bug likely regressed."
    )
    assert result is not None


@unittest.skip("skip system test")
@pytest.mark.asyncio
async def test_http_comp_008():
    os.environ['HTTP_SSL_VERIFY'] = 'false'
    config = HttpComponentConfig(
        request_params=HttpRequestParamConfig(
            url="http://localhost:8000/weather_timeout?location={{location}}",
            method="GET",
            timeout=5,
            retry_config=HttpRetryConfig(
                enabled=True,
                max_retries=3,
                retry_on_status_codes=[500, 502, 503, 504],
                retry_delay=1000,
                backoff_type="exponential"
            )
        )
    )
    http_comp = HTTPRequestComponent(config=config)

    flow = Workflow(card=WorkflowCard(name="test_http_comp_008", id="react_agent_comp_007", version="1.0"))
    start_component = Start()
    end_component = End()

    flow.set_start_comp("start", start_component, inputs_schema={"query": "${query}"})
    flow.set_end_comp("end", end_component, inputs_schema={"http_response": "${http}"})
    flow.add_workflow_comp("http", http_comp, inputs_schema={"location": "${start.query}"})

    flow.add_connection("start", "http")
    flow.add_connection("http", "end")

    context = create_workflow_session()
    try:
        result = await flow.invoke(inputs={"query": "深圳"}, session=context)
        logger.info(f"Workflow invoke result: {result}")
        assert False
    except BaseError as e:
        assert e.code == StatusCode.COMPONENT_TOOL_EXECUTION_ERROR.code
        assert "Session is closed" not in e.message


@unittest.skip("skip system test")
@pytest.mark.asyncio
async def test_http_comp_002():
    os.environ['HTTP_SSL_VERIFY'] = 'false'
    config = HttpComponentConfig(
        request_params=HttpRequestParamConfig(
            url="http://localhost:8000/weather",
            query_parameters={"location": "{{location}}"},
            method="GET",
            advanced_options=HttpAdvancedOptionsConfig(ignore_ssl_issues=True)
        )
    )
    http_comp = HTTPRequestComponent(config=config)

    flow = Workflow(card=WorkflowCard(name="test_http_comp_002", id="react_agent_comp_001", version="1.0"))
    start_component = Start()
    end_component = End()

    flow.set_start_comp("start", start_component, inputs_schema={"query": "${query}"})
    flow.set_end_comp("end", end_component, inputs_schema={"http_response": "${http}"})
    flow.add_workflow_comp("http", http_comp, inputs_schema={"location": "${start.query}"})

    flow.add_connection("start", "http")
    flow.add_connection("http", "end")

    context = create_workflow_session()
    result = await flow.invoke(inputs={"query": "杭州"}, session=context)
    logger.info(f"Workflow invoke result: {result}")
    response = result.result
    http_response = response.get('output').get('http_response')
    assert http_response.get('statusCode') == 200
    result_json = json.loads(http_response.get('body'))
    assert result_json == {'location': '杭州', 'temperature': '18℃ - 26℃', 'condition': '晴'}
    assert http_response.get("ok") == True


@unittest.skip("skip system test")
@pytest.mark.asyncio
async def test_http_comp_013():
    os.environ['HTTP_SSL_VERIFY'] = 'false'

    config = HttpComponentConfig(
        request_params=HttpRequestParamConfig(
            url="http://localhost:8000/post_weather_with_headers",
            headers="{{head}}",
            method="POST",
            body=HttpRequestBodyConfig(
                content_type=HttpContentType.JSON,
                json_data="{{query}}"
            )
        )
    )
    http_comp = HTTPRequestComponent(config=config)

    flow = Workflow(card=WorkflowCard(name="test_http_comp_013", id="http_comp_012", version="1.0"))
    start_component = Start()
    end_component = End({"responseTemplate": "{{http_response}}"})

    flow.set_start_comp("start", start_component, inputs_schema={"query": "${query}", "head": "${head}"})
    flow.set_end_comp("end", end_component, inputs_schema={"http_response": "${http}"})
    flow.add_workflow_comp("http", http_comp, inputs_schema={"query": "${start.query}", "head": "${start.head}"})

    flow.add_connection("start", "http")
    flow.add_connection("http", "end")

    context = create_workflow_session()
    result = await flow.invoke(inputs={"query": {"location": "杭州", "scenic": "西湖十景", "score": 88.8},
                                        "head": {"Authorization": "Bearer abc123xyz", "X-API-Key": "my-secret-key"}},
                                session=context)
    logger.info(f"Workflow invoke result: {result}")
    response = result.result
    http_response = response.get('response')
    outer_dict = ast.literal_eval(http_response)
    assert outer_dict.get('statusCode') == 200
    http_response_json = json.loads(outer_dict.get('body'))
    assert http_response_json == {'location': '杭州', 'temperature': '18℃ - 26℃', 'condition': '晴', 'score': 88.8,
                                    'scenic': '西湖十景'}
    assert outer_dict.get("ok") == True
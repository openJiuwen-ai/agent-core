#!/usr/bin/env python3
"""
Simple test to verify the HTTP Request component can be imported and instantiated
"""
import json
import logging
import os
import unittest
from unittest.mock import AsyncMock, Mock, patch

import pytest
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.workflow import (
    HTTPRequestComponent,
    HttpComponentConfig,
    HttpRequestParamConfig,
    HttpAdvancedOptionsConfig,
    HttpRetryConfig,
    Workflow,
    Start,
    End,
    create_workflow_session
)
from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.workflow.base import WorkflowCard

# Set up logging for the test module
logger = logging.getLogger(__name__)

_HTTP_COMPONENT_MODULE = (
    "openjiuwen.core.workflow.components.tool.http.http_request_component"
)


def _create_mock_http_response(*, status: int = 500, content: bytes = b"error"):
    """Build a minimal aiohttp-like response for HTTPRequestExecutable tests."""
    mock_response = AsyncMock()
    mock_response.status = status
    mock_response.headers = {"Content-Type": "application/json"}
    mock_response.url = "https://example.test/status/500"
    mock_response.reason = "INTERNAL SERVER ERROR"

    async def content_iter():
        yield content

    mock_response.content.iter_chunked = Mock(return_value=content_iter())
    return mock_response


def _create_mock_client_session(mock_response):
    """Build a ClientSession async context manager that returns mock_response."""
    mock_session = AsyncMock()

    class MockResponseContext:
        def __init__(self, response):
            self.response = response

        async def __aenter__(self):
            return self.response

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            return None

    mock_session.request = Mock(return_value=MockResponseContext(mock_response))
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    return mock_session


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
    """
    logger.info("Testing retry count and timeout session handling in workflow...")

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
                retry_delay=100,
                retry_on_status_codes=[500, 502, 503, 504, 429],
                backoff_type="fixed",
            ),
        )
    )

    assert config.request_params.timeout == 5.0
    assert config.request_params.advanced_options.timeout == 10000
    assert config.request_params.retry_config.enabled is True
    assert config.request_params.retry_config.max_retries == 2
    assert config.request_params.retry_config.retry_delay == 100

    mock_response = _create_mock_http_response()
    mock_session = _create_mock_client_session(mock_response)
    connector_instances: list[Mock] = []

    def _make_connector(*_args, **_kwargs):
        connector = Mock()
        connector_instances.append(connector)
        return connector

    with (
        patch(f"{_HTTP_COMPONENT_MODULE}.aiohttp.TCPConnector", side_effect=_make_connector),
        patch(f"{_HTTP_COMPONENT_MODULE}.aiohttp.ClientSession", return_value=mock_session),
        patch(f"{_HTTP_COMPONENT_MODULE}.SslUtils.get_ssl_config", return_value=(False, None)),
    ):
        flow = Workflow()
        start_component = Start()
        end_component = End({"responseTemplate": "{{output}}"})
        http_component = HTTPRequestComponent(config=config)

        flow.set_start_comp("s", start_component, inputs_schema={"query": "${query}"})
        flow.set_end_comp("e", end_component, inputs_schema={"output": "${http.output}"})
        flow.add_workflow_comp("http", http_component, inputs_schema={"url": "${s.query}"})
        flow.add_connection("s", "http")
        flow.add_connection("http", "e")

        context = create_workflow_session()
        result = await flow.invoke(
            inputs={"query": "https://example.test/status/500"},
            session=context,
        )

    expected_attempts = config.request_params.retry_config.max_retries + 1
    assert len(connector_instances) == expected_attempts
    assert mock_session.request.call_count == expected_attempts
    assert result is not None
    logger.info(
        "Retry count and timeout verified: %d connector(s) and %d request(s)",
        len(connector_instances),
        mock_session.request.call_count,
    )


@unittest.skip("skip system test")
@pytest.mark.asyncio
async def test_http_comp_008():
    """
    # !+================================================================
    # 版权 (C) 2019-2020，华为技术有限公司 2012实验室中央软件院
    # ==================================================================
    # @level: level 0
    # @CaseID: test_http_comp_008
    # @Description: HTTPRequestComponent进行get请求，设置重试配置
    # @Precondition: 部署jiuwen开源项目环境
    # @Step:
    # 1、配置HttpComponentConfig参数，method设置为get
    # 2、设置工作流：start -> http -> end
    # 3、工作流invoke执行
    # @Result:
    # 工作流执行异常报错：报错信息是不是可以直接写超时
    # !!================================================================
    """
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
    """
    # !+================================================================
    # 版权 (C) 2019-2020，华为技术有限公司 2012实验室中央软件院
    # ==================================================================
    # @level: level 0
    # @CaseID: test_http_comp_002
    # @Description: HTTPRequestComponent进行get请求，请求参数在query_parameters里，ssl不进行验证，end组件不设置模板，invoke模式
    # @Precondition: 部署jiuwen开源项目环境
    # @Step:
    # 1、配置HttpComponentConfig参数，method设置为GET，url中不包含请求参数
    # 2、设置工作流：start -> http -> end
    # 3、工作流invoke执行
    # @Result:
    # 工作流执行成功，请求结果返回正确。实际参数不能够传递
    # !!================================================================
    """
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

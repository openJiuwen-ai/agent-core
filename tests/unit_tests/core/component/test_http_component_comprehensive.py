#!/usr/bin/env python3
"""
Comprehensive test for the HTTP Request component
"""

import pytest
from openjiuwen.core.workflow import (
    HTTPRequestComponent,
    HttpComponentConfig,
    HttpRequestParamConfig,
    HttpAuthConfig,
    HttpRequestBodyConfig,
    HttpResponseHandlingConfig,
    HttpAdvancedOptionsConfig,
    HttpRetryConfig,
    HttpAuthType,
    HttpContentType
)
from openjiuwen.core.common.logging import logger


def test_basic_get_request():
    """Test basic GET request configuration"""
    logger.info("Testing basic GET request configuration...")

    config = HttpComponentConfig(
        request_params=HttpRequestParamConfig(
            url="https://httpbin.org/get",
            method="GET",
            headers={"User-Agent": "openJiuwen HTTP Component"},
        )
    )

    component = HTTPRequestComponent(config=config)

    assert component.config.request_params.url == "https://httpbin.org/get"
    assert component.config.request_params.method == "GET"
    assert component.config.request_params.headers["User-Agent"] == "openJiuwen HTTP Component"

    logger.info("✓ Basic GET request configuration test passed!")


def test_post_request_with_body():
    """Test POST request with JSON body"""
    logger.info("Testing POST request with JSON body...")

    config = HttpComponentConfig(
        request_params=HttpRequestParamConfig(
            url="https://httpbin.org/post",
            method="POST",
            body=HttpRequestBodyConfig(
                content_type=HttpContentType.JSON,
                json_data={"key": "value", "test": True}
            ),
            headers={"Content-Type": "application/json"}
        )
    )

    component = HTTPRequestComponent(config=config)

    assert component.config.request_params.method == "POST"
    assert component.config.request_params.body.content_type == HttpContentType.JSON
    assert component.config.request_params.body.json_data["key"] == "value"

    logger.info("✓ POST request with JSON body test passed!")


def test_authentication_config():
    """Test authentication configuration"""
    logger.info("Testing authentication configuration...")

    config = HttpComponentConfig(
        request_params=HttpRequestParamConfig(
            url="https://httpbin.org/get",
            method="GET",
            authentication=HttpAuthConfig(
                type=HttpAuthType.BASIC,
                username="testuser",
                password="testpass"
            )
        )
    )

    component = HTTPRequestComponent(config=config)

    auth = component.config.request_params.authentication
    assert auth.type == HttpAuthType.BASIC
    assert auth.username == "testuser"
    assert auth.password == "testpass"

    logger.info("✓ Authentication configuration test passed!")


def test_advanced_options():
    """Test advanced options configuration"""
    logger.info("Testing advanced options configuration...")

    config = HttpComponentConfig(
        request_params=HttpRequestParamConfig(
            url="https://httpbin.org/get",
            method="GET",
            advanced_options=HttpAdvancedOptionsConfig(
                follow_redirect=True,
                timeout=15000,  # 15 seconds in milliseconds
                ignore_ssl_issues=False
            ),
            retry_config=HttpRetryConfig(
                enabled=True,
                max_retries=3,
                retry_delay=1000  # 1 second in milliseconds
            )
        )
    )

    component = HTTPRequestComponent(config=config)

    adv_opts = component.config.request_params.advanced_options
    retry_cfg = component.config.request_params.retry_config

    assert adv_opts.follow_redirect is True
    assert adv_opts.timeout == 15000
    assert retry_cfg.enabled is True
    assert retry_cfg.max_retries == 3

    logger.info("✓ Advanced options configuration test passed!")


def test_response_handling():
    """Test response handling configuration"""
    logger.info("Testing response handling configuration...")

    config = HttpComponentConfig(
        request_params=HttpRequestParamConfig(
            url="https://httpbin.org/json",
            method="GET",
            response_handling=HttpResponseHandlingConfig(
                response_format="json",
                response_code_success_codes=[200, 201],
                response_mode="full"
            )
        )
    )

    component = HTTPRequestComponent(config=config)

    resp_handling = component.config.request_params.response_handling
    assert resp_handling.response_format == "json"
    assert 200 in resp_handling.response_code_success_codes
    assert resp_handling.response_mode == "full"

    logger.info("✓ Response handling configuration test passed!")


@pytest.mark.asyncio
async def test_executable_methods():
    """Test that executable has required methods"""
    logger.info("Testing executable methods...")

    config = HttpComponentConfig(
        request_params=HttpRequestParamConfig(
            url="https://httpbin.org/get",
            method="GET"
        )
    )

    component = HTTPRequestComponent(config=config)
    executable = component.executable

    # Check that required methods exist
    assert hasattr(executable, 'invoke')
    assert hasattr(executable, 'stream')
    assert hasattr(executable, 'collect')
    assert hasattr(executable, 'transform')

    logger.info("✓ Executable methods test passed!")
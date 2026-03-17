# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
Shared fixtures for guardrail framework tests.
"""

import pytest

from openjiuwen.core.runner.callback import AsyncCallbackFramework
from openjiuwen.core.security.guardrail import (
    BaseGuardrail,
    GuardrailBackend,
    GuardrailContext,
    GuardrailContentType,
    GuardrailError,
    GuardrailResult,
    RiskAssessment,
    RiskLevel,
)


@pytest.fixture
def framework():
    """Callback framework instance for guardrail integration tests."""
    return AsyncCallbackFramework(enable_metrics=False, enable_logging=False)


@pytest.fixture
def framework_with_logging():
    """Callback framework instance with logging enabled."""
    return AsyncCallbackFramework(enable_metrics=False, enable_logging=True)


@pytest.fixture
def mock_backend():
    """Mock guardrail backend that returns safe results."""
    class MockBackend(GuardrailBackend):
        async def analyze(self, data):
            return RiskAssessment(
                has_risk=False,
                risk_level=RiskLevel.SAFE,
                risk_type=None
            )

    return MockBackend()


@pytest.fixture
def risky_backend():
    """Mock guardrail backend that returns risky results."""
    class RiskyBackend(GuardrailBackend):
        async def analyze(self, data):
            return RiskAssessment(
                has_risk=True,
                risk_level=RiskLevel.HIGH,
                risk_type="test_risk"
            )

    return RiskyBackend()


@pytest.fixture
def safe_guardrail_result():
    """Safe GuardrailResult instance."""
    return GuardrailResult.pass_()


@pytest.fixture
def blocked_guardrail_result():
    """Blocked GuardrailResult instance."""
    return GuardrailResult.block(
        risk_level=RiskLevel.HIGH,
        risk_type="prompt_injection",
        details={"matched_pattern": "ignore instructions"}
    )


@pytest.fixture
def risky_backend_with_details():
    """Mock guardrail backend that returns risky results with details."""
    class RiskyBackendWithDetails(GuardrailBackend):
        async def analyze(self, data):
            return RiskAssessment(
                has_risk=True,
                risk_level=RiskLevel.HIGH,
                risk_type="prompt_injection",
                details={"matched_pattern": "ignore previous instructions", "confidence": 0.95}
            )

    return RiskyBackendWithDetails()


@pytest.fixture
def simple_guardrail():
    """Simple guardrail implementation for testing."""

    class SimpleGuardrail(BaseGuardrail):
        DEFAULT_EVENTS = ["test_event"]

        def extract_context(self, event, *args, **kwargs):
            return GuardrailContext(
                content_type=GuardrailContentType.TEXT,
                content=kwargs.get("text", ""),
                event=str(event),
                metadata=kwargs
            )

        async def detect(self, event_name, *args, **kwargs):
            return GuardrailResult.pass_()

    return SimpleGuardrail()


@pytest.fixture
def user_input_data():
    """Sample user input event data."""
    return {
        "text": "Hello, how are you?",
        "user_id": "user123",
        "session_id": "session456"
    }


@pytest.fixture
def llm_input_data():
    """Sample LLM input event data."""
    return {
        "prompt": "You are a helpful assistant.\nUser: What is 2+2?",
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "What is 2+2?"}
        ],
        "model": "gpt-4"
    }


@pytest.fixture
def llm_output_data():
    """Sample LLM output event data."""
    return {
        "content": "2+2 equals 4.",
        "tool_calls": [],
        "model": "gpt-4"
    }


@pytest.fixture
def tool_input_data():
    """Sample tool input event data."""
    return {
        "tool_name": "search",
        "tool_input": {"query": "weather today"},
        "caller_context": {"user_id": "user123"}
    }


@pytest.fixture
def tool_output_data():
    """Sample tool output event data."""
    return {
        "tool_name": "search",
        "tool_output": {"result": "Sunny, 25C"},
        "execution_time": 0.5
    }


@pytest.fixture
def planning_start_data():
    """Sample planning start event data."""
    return {
        "task": "Find the best Italian restaurant nearby"
    }


@pytest.fixture
def planning_complete_data():
    """Sample planning complete event data."""
    return {
        "plan": {
            "steps": [
                {"action": "search", "params": {"query": "Italian restaurants"}},
                {"action": "filter", "params": {"rating": ">4.0"}},
                {"action": "sort", "params": {"by": "distance"}}
            ]
        },
        "steps": [
            {"action": "search", "params": {"query": "Italian restaurants"}},
            {"action": "filter", "params": {"rating": ">4.0"}},
            {"action": "sort", "params": {"by": "distance"}}
        ]
    }

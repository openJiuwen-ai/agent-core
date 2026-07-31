# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""ReliabilityRail tests through the real ReActAgent callback lifecycle."""

from collections.abc import Callable
from typing import Any
from unittest.mock import patch

import pytest

from openjiuwen.agent_teams.reliability.detectors.model_error import ModelStreamErrorDetector
from openjiuwen.agent_teams.reliability.detectors.tool_error import ToolErrorRateDetector
from openjiuwen.agent_teams.reliability.monitor import ReliabilityMonitor
from openjiuwen.agent_teams.reliability.rail import ReliabilityRail
from openjiuwen.agent_teams.reliability.remediation.policy import RemediationPolicy
from openjiuwen.agent_teams.reliability.signals import Signal, SignalKind
from openjiuwen.core.foundation.llm import ModelClientConfig, ModelRequestConfig
from openjiuwen.core.foundation.tool import LocalFunction, ToolCard
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent import AgentCard, ReActAgent, ReActAgentConfig
from openjiuwen.core.single_agent.rail.base import AgentRail
from tests.unit_tests.fixtures.mock_llm import MockLLMModel, create_text_response, create_tool_call_response


pytestmark = pytest.mark.level1


class _RecordingMonitor:
    """Monitor stand-in that records signals without running detectors."""

    def __init__(self) -> None:
        self.signals: list[Signal] = []

    async def feed(self, signal: Signal) -> list:
        self.signals.append(signal)
        return []

    def reset(self) -> None:
        self.signals.clear()


class _RecordingReporter:
    """Reporter stand-in used to assert detector output."""

    def __init__(self) -> None:
        self.reported: list = []

    async def report(self, anomaly) -> None:
        self.reported.append(anomaly)


class _RetryFirstModelFailureRail(AgentRail):
    """Request one model retry to expose per-attempt signal ordering."""

    async def on_model_exception(self, ctx) -> None:
        if ctx.retry_attempt == 0:
            ctx.request_retry()


class _RetryFirstToolFailureRail(AgentRail):
    """Request one tool retry to expose per-attempt signal ordering."""

    async def on_tool_exception(self, ctx) -> None:
        if ctx.retry_attempt == 0:
            ctx.request_retry()


@pytest.fixture
def agent_factory():
    """Build agents with isolated tool registrations and clean global state."""
    registered_tool_ids: list[str] = []

    def create(tool_name: str, tool_func: Callable[..., Any]) -> ReActAgent:
        config = ReActAgentConfig(
            model_config_obj=ModelRequestConfig(model="mock-model"),
            model_client_config=ModelClientConfig(
                client_provider="OpenAI",
                api_key="mock-api-key",
                api_base="http://mock-api-base",
                verify_ssl=False,
            ),
            prompt_template=[{"role": "system", "content": "reliability lifecycle test"}],
        )
        agent = ReActAgent(card=AgentCard(description="reliability lifecycle test agent")).configure(config)
        tool = LocalFunction(
            card=ToolCard(
                id=tool_name,
                name=tool_name,
                description="Add two numbers for a lifecycle test",
                input_params={
                    "type": "object",
                    "properties": {
                        "a": {"type": "number"},
                        "b": {"type": "number"},
                    },
                    "required": ["a", "b"],
                },
            ),
            func=tool_func,
        )
        agent.ability_manager.add(tool.card)
        Runner.resource_mgr.add_tool(tool)
        registered_tool_ids.append(tool.card.id)
        return agent

    yield create

    for tool_id in registered_tool_ids:
        Runner.resource_mgr.remove_tool(tool_id)


@pytest.mark.asyncio
async def test_reliability_rail_observes_real_model_and_tool_success_lifecycle(agent_factory):
    agent = agent_factory("reliability_mock_add", lambda a, b: a + b)
    monitor = _RecordingMonitor()
    await agent.register_rail(ReliabilityRail(monitor=monitor, member_name="worker"))
    mock_llm = MockLLMModel()
    mock_llm.set_responses(
        [
            create_tool_call_response("reliability_mock_add", '{"a": 1, "b": 2}'),
            create_text_response("3"),
        ]
    )

    with patch.object(agent, "_get_llm", return_value=mock_llm):
        result = await agent.invoke({"query": "1 + 2"})

    assert result["result_type"] == "answer"
    assert [signal.kind for signal in monitor.signals] == [
        SignalKind.BEFORE_MODEL_CALL,
        SignalKind.AFTER_MODEL_CALL,
        SignalKind.BEFORE_TOOL_CALL,
        SignalKind.AFTER_TOOL_CALL,
        SignalKind.BEFORE_MODEL_CALL,
        SignalKind.AFTER_MODEL_CALL,
    ]
    tool_before = next(signal for signal in monitor.signals if signal.kind == SignalKind.BEFORE_TOOL_CALL)
    tool_after = next(signal for signal in monitor.signals if signal.kind == SignalKind.AFTER_TOOL_CALL)
    assert tool_before.tool_name == "reliability_mock_add"
    assert tool_before.tool_args is None
    assert tool_after.tool_result == 3
    assert monitor.signals[-1].text_len == 1


@pytest.mark.asyncio
async def test_reliability_rail_observes_model_failure_finally_lifecycle(agent_factory):
    agent = agent_factory("reliability_unused_model_error_tool", lambda a, b: a + b)
    monitor = _RecordingMonitor()
    await agent.register_rail(ReliabilityRail(monitor=monitor, member_name="worker"))
    mock_llm = MockLLMModel()

    async def fail_model(*args, **kwargs):
        raise RuntimeError("mock model failure")

    mock_llm.invoke = fail_model
    with patch.object(agent, "_get_llm", return_value=mock_llm):
        with pytest.raises(RuntimeError, match="mock model failure"):
            await agent.invoke({"query": "fail"})

    assert [signal.kind for signal in monitor.signals] == [
        SignalKind.BEFORE_MODEL_CALL,
        SignalKind.MODEL_EXCEPTION,
        SignalKind.AFTER_MODEL_CALL,
    ]
    assert monitor.signals[1].error == "mock model failure"
    assert monitor.signals[2].text_len is None


@pytest.mark.asyncio
async def test_reliability_rail_observes_failed_tool_exception_then_finally(agent_factory):
    def fail_tool(a, b):
        raise ValueError("mock tool failure")

    agent = agent_factory("reliability_mock_fail", fail_tool)
    monitor = _RecordingMonitor()
    await agent.register_rail(ReliabilityRail(monitor=monitor, member_name="worker"))
    mock_llm = MockLLMModel()
    mock_llm.set_responses(
        [
            create_tool_call_response("reliability_mock_fail", '{"a": 1, "b": 2}'),
            create_text_response("handled"),
        ]
    )

    with patch.object(agent, "_get_llm", return_value=mock_llm):
        await agent.invoke({"query": "call a failing tool"})

    kinds = [signal.kind for signal in monitor.signals]
    tool_error_index = kinds.index(SignalKind.TOOL_EXCEPTION)
    assert kinds[tool_error_index + 1] == SignalKind.AFTER_TOOL_CALL
    assert "mock tool failure" in monitor.signals[tool_error_index].error
    assert monitor.signals[tool_error_index + 1].tool_result is None


@pytest.mark.asyncio
async def test_reliability_rail_model_retry_emits_after_for_each_attempt(agent_factory):
    agent = agent_factory("reliability_unused_model_retry_tool", lambda a, b: a + b)
    monitor = _RecordingMonitor()
    await agent.register_rail(ReliabilityRail(monitor=monitor, member_name="worker"))
    await agent.register_rail(_RetryFirstModelFailureRail())
    mock_llm = MockLLMModel()
    attempts = 0

    async def flaky_model(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("retry model once")
        return create_text_response("recovered")

    mock_llm.invoke = flaky_model
    with patch.object(agent, "_get_llm", return_value=mock_llm):
        await agent.invoke({"query": "retry model"})

    assert attempts == 2
    assert [signal.kind for signal in monitor.signals] == [
        SignalKind.BEFORE_MODEL_CALL,
        SignalKind.MODEL_EXCEPTION,
        SignalKind.AFTER_MODEL_CALL,
        SignalKind.BEFORE_MODEL_CALL,
        SignalKind.AFTER_MODEL_CALL,
    ]


@pytest.mark.asyncio
async def test_reliability_rail_tool_retry_skips_intermediate_after_signal(agent_factory):
    attempts = 0

    def flaky_tool(a, b):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ValueError("retry tool once")
        return a + b

    agent = agent_factory("reliability_mock_flaky", flaky_tool)
    monitor = _RecordingMonitor()
    await agent.register_rail(ReliabilityRail(monitor=monitor, member_name="worker"))
    await agent.register_rail(_RetryFirstToolFailureRail())
    mock_llm = MockLLMModel()
    mock_llm.set_responses(
        [
            create_tool_call_response("reliability_mock_flaky", '{"a": 1, "b": 2}'),
            create_text_response("3"),
        ]
    )

    with patch.object(agent, "_get_llm", return_value=mock_llm):
        await agent.invoke({"query": "retry tool"})

    assert attempts == 2
    assert [signal.kind for signal in monitor.signals] == [
        SignalKind.BEFORE_MODEL_CALL,
        SignalKind.AFTER_MODEL_CALL,
        SignalKind.BEFORE_TOOL_CALL,
        SignalKind.TOOL_EXCEPTION,
        SignalKind.BEFORE_TOOL_CALL,
        SignalKind.AFTER_TOOL_CALL,
        SignalKind.BEFORE_MODEL_CALL,
        SignalKind.AFTER_MODEL_CALL,
    ]


@pytest.mark.xfail(
    strict=True,
    reason="AbilityManager emits JSON-string args while ReliabilityRail only retains dict args",
)
@pytest.mark.asyncio
async def test_real_tool_lifecycle_preserves_args_for_repeat_detection(agent_factory):
    agent = agent_factory("reliability_mock_args", lambda a, b: a + b)
    monitor = _RecordingMonitor()
    await agent.register_rail(ReliabilityRail(monitor=monitor, member_name="worker"))
    mock_llm = MockLLMModel()
    mock_llm.set_responses(
        [
            create_tool_call_response("reliability_mock_args", '{"a": 1, "b": 2}'),
            create_text_response("3"),
        ]
    )

    with patch.object(agent, "_get_llm", return_value=mock_llm):
        await agent.invoke({"query": "preserve args"})

    tool_before = next(signal for signal in monitor.signals if signal.kind == SignalKind.BEFORE_TOOL_CALL)
    assert tool_before.tool_args == {"a": 1, "b": 2}


@pytest.mark.xfail(
    strict=True,
    reason="AFTER_MODEL_CALL fires after an exception and currently resets the model-error streak",
)
@pytest.mark.asyncio
async def test_real_model_failures_reach_consecutive_threshold(agent_factory):
    agent = agent_factory("reliability_unused_model_streak_tool", lambda a, b: a + b)
    reporter = _RecordingReporter()
    monitor = ReliabilityMonitor(
        [ModelStreamErrorDetector(rate_threshold=100, consecutive_threshold=2)],
        reporter,
        RemediationPolicy(),
    )
    await agent.register_rail(ReliabilityRail(monitor=monitor, member_name="worker"))
    mock_llm = MockLLMModel()

    async def fail_model(*args, **kwargs):
        raise RuntimeError("persistent model failure")

    mock_llm.invoke = fail_model
    with patch.object(agent, "_get_llm", return_value=mock_llm):
        for _ in range(2):
            with pytest.raises(RuntimeError, match="persistent model failure"):
                await agent.invoke({"query": "fail twice"})

    assert len(reporter.reported) == 1


@pytest.mark.xfail(
    strict=True,
    reason="AFTER_TOOL_CALL fires after a final exception and currently resets the tool-error streak",
)
@pytest.mark.asyncio
async def test_real_tool_failures_reach_consecutive_threshold(agent_factory):
    def fail_tool(a, b):
        raise ValueError("persistent tool failure")

    agent = agent_factory("reliability_mock_tool_streak", fail_tool)
    reporter = _RecordingReporter()
    monitor = ReliabilityMonitor(
        [ToolErrorRateDetector(rate_threshold=100, consecutive_threshold=2)],
        reporter,
        RemediationPolicy(),
    )
    await agent.register_rail(ReliabilityRail(monitor=monitor, member_name="worker"))
    mock_llm = MockLLMModel()
    mock_llm.set_responses(
        [
            create_tool_call_response("reliability_mock_tool_streak", '{"a": 1, "b": 2}'),
            create_text_response("handled once"),
            create_tool_call_response("reliability_mock_tool_streak", '{"a": 1, "b": 2}'),
            create_text_response("handled twice"),
        ]
    )

    with patch.object(agent, "_get_llm", return_value=mock_llm):
        await agent.invoke({"query": "fail tool once"})
        await agent.invoke({"query": "fail tool twice"})

    assert len(reporter.reported) == 1

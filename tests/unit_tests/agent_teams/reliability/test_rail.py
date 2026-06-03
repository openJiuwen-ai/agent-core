# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for ReliabilityRail signal capture, monitor fan-out, and local steer."""

import asyncio

import pytest

from openjiuwen.agent_teams.reliability.anomaly import Anomaly, AnomalyKind, Severity
from openjiuwen.agent_teams.reliability.detectors.tool_error import ToolErrorRateDetector
from openjiuwen.agent_teams.reliability.monitor import ReliabilityMonitor
from openjiuwen.agent_teams.reliability.rail import ReliabilityRail
from openjiuwen.agent_teams.reliability.remediation.local import LocalAutoRemediator
from openjiuwen.agent_teams.reliability.remediation.policy import RemediationPolicy
from openjiuwen.agent_teams.reliability.signals import Signal, SignalKind
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ModelCallInputs, ToolCallInputs


class _RecordingMonitor:
    """Monitor stub that records fed signals and reports no anomaly."""

    def __init__(self) -> None:
        self.signals: list[Signal] = []

    async def feed(self, signal: Signal) -> list[Anomaly]:
        self.signals.append(signal)
        return []

    def reset(self) -> None:
        pass


class _AnomalyMonitor:
    """Monitor stub that always returns one HIGH anomaly."""

    async def feed(self, signal: Signal) -> list[Anomaly]:
        return [
            Anomaly(
                detector="repeat_tool_call",
                kind=AnomalyKind.TOOL_CALL_LOOP,
                severity=Severity.HIGH,
                member_name="m1",
                summary="loop",
            )
        ]

    def reset(self) -> None:
        pass


class _RecordingReporter:
    """Reporter stub that records reported anomalies."""

    def __init__(self) -> None:
        self.reported: list = []

    async def report(self, anomaly) -> None:
        self.reported.append(anomaly)


class _Response:
    """Minimal LLM response stand-in with content + reasoning."""

    content = "x" * 50
    reasoning_content = "y" * 20


def _ctx(inputs, exception: Exception | None = None) -> AgentCallbackContext:
    return AgentCallbackContext(agent=None, inputs=inputs, exception=exception)


@pytest.mark.asyncio
async def test_rail_before_tool_call_emits_signal():
    monitor = _RecordingMonitor()
    rail = ReliabilityRail(monitor=monitor, member_name="m1")
    await rail.before_tool_call(_ctx(ToolCallInputs(tool_name="run", tool_args={"a": 1})))
    assert len(monitor.signals) == 1
    signal = monitor.signals[0]
    assert signal.kind == SignalKind.BEFORE_TOOL_CALL
    assert signal.member_name == "m1"
    assert signal.tool_name == "run"
    assert signal.tool_args == {"a": 1}


@pytest.mark.asyncio
async def test_rail_tool_exception_carries_error():
    monitor = _RecordingMonitor()
    rail = ReliabilityRail(monitor=monitor, member_name="m1")
    await rail.on_tool_exception(_ctx(ToolCallInputs(tool_name="run"), exception=ValueError("boom")))
    signal = monitor.signals[0]
    assert signal.kind == SignalKind.TOOL_EXCEPTION
    assert "boom" in signal.error


@pytest.mark.asyncio
async def test_rail_model_exception_carries_error():
    monitor = _RecordingMonitor()
    rail = ReliabilityRail(monitor=monitor, member_name="m1")
    await rail.on_model_exception(_ctx(ModelCallInputs(), exception=RuntimeError("rate limit")))
    signal = monitor.signals[0]
    assert signal.kind == SignalKind.MODEL_EXCEPTION
    assert "rate limit" in signal.error


@pytest.mark.asyncio
async def test_rail_before_model_call_counts_messages():
    monitor = _RecordingMonitor()
    rail = ReliabilityRail(monitor=monitor, member_name="m1")
    await rail.before_model_call(_ctx(ModelCallInputs(messages=[1, 2, 3])))
    signal = monitor.signals[0]
    assert signal.kind == SignalKind.BEFORE_MODEL_CALL
    assert signal.message_count == 3


@pytest.mark.asyncio
async def test_rail_after_model_call_measures_response():
    monitor = _RecordingMonitor()
    rail = ReliabilityRail(monitor=monitor, member_name="m1")
    await rail.after_model_call(_ctx(ModelCallInputs(response=_Response())))
    signal = monitor.signals[0]
    assert signal.kind == SignalKind.AFTER_MODEL_CALL
    assert signal.text_len == 50
    assert signal.thinking_len == 20


@pytest.mark.asyncio
async def test_monitor_fans_out_to_detectors_and_reports():
    reporter = _RecordingReporter()
    detector = ToolErrorRateDetector(window_seconds=60.0, rate_threshold=100, consecutive_threshold=2, now=lambda: 0.0)
    monitor = ReliabilityMonitor([detector], reporter, RemediationPolicy())
    await monitor.feed(Signal(kind=SignalKind.TOOL_EXCEPTION, member_name="m", error="e"))
    produced = await monitor.feed(Signal(kind=SignalKind.TOOL_EXCEPTION, member_name="m", error="e"))
    assert len(produced) == 1
    assert len(reporter.reported) == 1


@pytest.mark.asyncio
async def test_rail_local_steer_pushes_steering():
    auto = LocalAutoRemediator(RemediationPolicy(), intensity=5, period_seconds=60.0, now=lambda: 0.0)
    rail = ReliabilityRail(monitor=_AnomalyMonitor(), member_name="m1", auto_remediator=auto)
    ctx = _ctx(ToolCallInputs(tool_name="x"))
    queue: asyncio.Queue = asyncio.Queue()
    ctx.bind_steering_queue(queue)
    await rail.before_tool_call(ctx)
    assert not queue.empty()


def test_reliability_rail_priority_is_low():
    assert ReliabilityRail.priority < 12

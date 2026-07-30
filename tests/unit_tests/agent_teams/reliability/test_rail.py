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
from openjiuwen.agent_teams.reliability.reporter import LocalAnomalyReporter
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


class _ThinkingResponse:
    """Response shape that exposes ``thinking`` instead of reasoning content."""

    content = {"not": "text"}
    reasoning_content = ""
    thinking = "fallback-thinking"


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
async def test_rail_before_tool_call_drops_non_dict_args():
    monitor = _RecordingMonitor()
    rail = ReliabilityRail(monitor=monitor, member_name="m1")
    await rail.before_tool_call(_ctx(ToolCallInputs(tool_name="run", tool_args='{"a": 1}')))
    assert monitor.signals[0].tool_args is None


@pytest.mark.asyncio
async def test_rail_before_tool_call_uses_safe_defaults_for_unknown_input_shape():
    monitor = _RecordingMonitor()
    rail = ReliabilityRail(monitor=monitor, member_name="m1")
    await rail.before_tool_call(_ctx(object()))
    signal = monitor.signals[0]
    assert signal.tool_name == ""
    assert signal.tool_args is None


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


@pytest.mark.parametrize(
    ("hook_name", "inputs", "expected_kind"),
    [
        ("on_tool_exception", ToolCallInputs(tool_name="run"), SignalKind.TOOL_EXCEPTION),
        ("on_model_exception", ModelCallInputs(), SignalKind.MODEL_EXCEPTION),
    ],
)
@pytest.mark.asyncio
async def test_rail_exception_without_exception_object_uses_stable_fallback(hook_name, inputs, expected_kind):
    monitor = _RecordingMonitor()
    rail = ReliabilityRail(monitor=monitor, member_name="m1")
    await getattr(rail, hook_name)(_ctx(inputs))
    signal = monitor.signals[0]
    assert signal.kind == expected_kind
    assert signal.error == "error"


@pytest.mark.asyncio
async def test_rail_before_model_call_counts_messages():
    monitor = _RecordingMonitor()
    rail = ReliabilityRail(monitor=monitor, member_name="m1")
    await rail.before_model_call(_ctx(ModelCallInputs(messages=[1, 2, 3])))
    signal = monitor.signals[0]
    assert signal.kind == SignalKind.BEFORE_MODEL_CALL
    assert signal.message_count == 3


@pytest.mark.asyncio
async def test_rail_before_model_call_ignores_non_list_messages():
    monitor = _RecordingMonitor()
    rail = ReliabilityRail(monitor=monitor, member_name="m1")
    await rail.before_model_call(_ctx(ModelCallInputs(messages=(1, 2, 3))))
    assert monitor.signals[0].message_count is None


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
async def test_rail_after_model_call_uses_thinking_fallback_and_ignores_structured_content():
    monitor = _RecordingMonitor()
    rail = ReliabilityRail(monitor=monitor, member_name="m1")
    await rail.after_model_call(_ctx(ModelCallInputs(response=_ThinkingResponse())))
    signal = monitor.signals[0]
    assert signal.text_len is None
    assert signal.thinking_len == len("fallback-thinking")


@pytest.mark.asyncio
async def test_rail_after_model_call_handles_missing_response():
    monitor = _RecordingMonitor()
    rail = ReliabilityRail(monitor=monitor, member_name="m1")
    await rail.after_model_call(_ctx(ModelCallInputs(response=None)))
    signal = monitor.signals[0]
    assert signal.text_len is None
    assert signal.thinking_len is None


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
async def test_monitor_isolates_detector_failure_and_continues_fanout():
    anomaly = Anomaly(
        detector="healthy",
        kind=AnomalyKind.MODEL_ERROR,
        severity=Severity.MEDIUM,
        member_name="m1",
        summary="detected",
    )

    class FailingDetector:
        name = "broken"

        def observe(self, signal):
            raise RuntimeError("detector failed")

        def reset(self):
            pass

    class HealthyDetector:
        name = "healthy"

        def __init__(self):
            self.observed = []

        def observe(self, signal):
            self.observed.append(signal)
            return anomaly

        def reset(self):
            pass

    healthy = HealthyDetector()
    reporter = _RecordingReporter()
    monitor = ReliabilityMonitor([FailingDetector(), healthy], reporter, RemediationPolicy())
    signal = Signal(kind=SignalKind.MODEL_EXCEPTION, member_name="m1", error="boom")
    produced = await monitor.feed(signal)
    assert healthy.observed == [signal]
    assert produced == [anomaly]
    assert reporter.reported == [anomaly]


def test_monitor_reset_resets_every_detector():
    class ResettableDetector:
        name = "resettable"

        def __init__(self):
            self.reset_count = 0

        def observe(self, signal):
            return None

        def reset(self):
            self.reset_count += 1

    detectors = [ResettableDetector(), ResettableDetector()]
    monitor = ReliabilityMonitor(detectors, _RecordingReporter(), RemediationPolicy())
    monitor.reset()
    assert [detector.reset_count for detector in detectors] == [1, 1]


@pytest.mark.asyncio
async def test_rail_local_steer_pushes_steering():
    auto = LocalAutoRemediator(RemediationPolicy(), intensity=5, period_seconds=60.0, now=lambda: 0.0)
    rail = ReliabilityRail(monitor=_AnomalyMonitor(), member_name="m1", auto_remediator=auto)
    ctx = _ctx(ToolCallInputs(tool_name="x"))
    queue: asyncio.Queue = asyncio.Queue()
    ctx.bind_steering_queue(queue)
    await rail.before_tool_call(ctx)
    assert not queue.empty()


@pytest.mark.asyncio
async def test_rail_local_steer_respects_intensity_budget():
    auto = LocalAutoRemediator(RemediationPolicy(), intensity=1, period_seconds=60.0, now=lambda: 0.0)
    rail = ReliabilityRail(monitor=_AnomalyMonitor(), member_name="m1", auto_remediator=auto)
    ctx = _ctx(ToolCallInputs(tool_name="x"))
    queue: asyncio.Queue = asyncio.Queue()
    ctx.bind_steering_queue(queue)
    await rail.before_tool_call(ctx)
    await rail.before_tool_call(ctx)
    assert queue.qsize() == 1


@pytest.mark.asyncio
async def test_rail_without_anomaly_does_not_invoke_auto_remediator():
    class UnexpectedAutoRemediator:
        def steer_message(self, anomaly):
            raise AssertionError("auto remediation must only run for produced anomalies")

    rail = ReliabilityRail(
        monitor=_RecordingMonitor(),
        member_name="m1",
        auto_remediator=UnexpectedAutoRemediator(),
    )
    await rail.before_model_call(_ctx(ModelCallInputs()))


@pytest.mark.asyncio
async def test_rail_local_steer_is_safe_without_bound_queue():
    auto = LocalAutoRemediator(RemediationPolicy(), intensity=1, period_seconds=60.0, now=lambda: 0.0)
    rail = ReliabilityRail(monitor=_AnomalyMonitor(), member_name="m1", auto_remediator=auto)
    await rail.before_tool_call(_ctx(ToolCallInputs(tool_name="x")))


def test_reliability_rail_priority_is_low():
    assert ReliabilityRail.priority < 12


@pytest.mark.asyncio
async def test_rail_after_tool_call_captures_tool_result():
    monitor = _RecordingMonitor()
    rail = ReliabilityRail(monitor=monitor, member_name="m1")
    await rail.after_tool_call(_ctx(ToolCallInputs(tool_name="run", tool_result="the-result")))
    signal = monitor.signals[0]
    assert signal.kind == SignalKind.AFTER_TOOL_CALL
    assert signal.tool_result == "the-result"


@pytest.mark.asyncio
async def test_leader_rail_bind_local_sink_routes_anomaly():
    received = []

    async def sink(anomaly):
        received.append(anomaly)

    local_reporter = LocalAnomalyReporter()
    monitor = ReliabilityMonitor(
        [ToolErrorRateDetector(consecutive_threshold=1, now=lambda: 0.0)],
        local_reporter,
        RemediationPolicy(),
    )
    rail = ReliabilityRail(monitor=monitor, member_name="leader-1", local_reporter=local_reporter)
    rail.bind_local_sink(sink)
    await rail.on_tool_exception(_ctx(ToolCallInputs(tool_name="run"), exception=ValueError("boom")))
    assert len(received) == 1


@pytest.mark.asyncio
async def test_non_leader_rail_bind_local_sink_is_noop():
    async def sink(anomaly):
        pass

    rail = ReliabilityRail(monitor=_RecordingMonitor(), member_name="dev-1")
    rail.bind_local_sink(sink)  # no local reporter -> no-op, must not raise

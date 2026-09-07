# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Recovery policy behavior for detected Agent RAS anomalies."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.agent_ras.config import RecoveryPolicyConfig, RepeatToolConfig
from openjiuwen.harness.agent_ras.detectors.repeat_tool import RepeatToolCallDetector
from openjiuwen.harness.agent_ras.models import (
    Anomaly,
    AnomalyKind,
    Severity,
    Signal,
    SignalKind,
)
from openjiuwen.harness.agent_ras.monitor import AgentRASMonitor
from openjiuwen.harness.agent_ras.recovery.engine import (
    LocalAutoRecovery,
    RecoveryExecutor,
    RecoveryPolicy,
)


class _RecordingSession:
    def __init__(self) -> None:
        self.stream_events: list[object] = []
        self._state: dict[str, object] = {}

    async def write_stream(self, event: object) -> None:
        self.stream_events.append(event)

    def get_state(self, key: str, default: object = None) -> object:
        return self._state.get(key, default)

    def update_state(self, values: dict[str, object]) -> None:
        self._state.update(values)


@pytest.mark.asyncio
async def test_critical_tool_call_loop_force_finishes_current_round() -> None:
    """A confirmed no-progress loop must not continue after its breaker fires."""
    policy = RecoveryPolicy.from_config(RecoveryPolicyConfig())
    executor = RecoveryExecutor(LocalAutoRecovery(policy))
    session = _RecordingSession()
    ctx = AgentCallbackContext(agent=SimpleNamespace(ability_manager=None), session=session)
    ctx.bind_steering_queue(asyncio.Queue())
    anomaly = Anomaly(
        detector="repeat_tool_call",
        kind=AnomalyKind.TOOL_CALL_LOOP,
        severity=Severity.CRITICAL,
        member_name="product-strategist",
        summary="tool_call_loop on view_task",
        evidence={
            "detector_kind": "global_circuit_breaker",
            "tool_name": "view_task",
            "count": 10,
        },
    )

    await executor.apply(ctx, anomaly, policy.ops_for(anomaly))

    request = ctx.consume_force_finish()
    assert request is not None
    assert request.result["result_type"] == "error"
    assert "view_task" in request.result["output"]
    assert any(getattr(event, "type", None) == "error" for event in session.stream_events)


@pytest.mark.asyncio
async def test_identical_view_task_results_trip_breaker_at_threshold() -> None:
    """The production detector-to-recovery chain stops repeated task polling."""
    policy = RecoveryPolicy.from_config(RecoveryPolicyConfig())
    executor = RecoveryExecutor(LocalAutoRecovery(policy))
    detector = RepeatToolCallDetector(
        RepeatToolConfig(
            warning_threshold=2,
            critical_threshold=4,
            global_breaker_threshold=3,
            unknown_tool_threshold=100,
        )
    )
    monitor = AgentRASMonitor(
        detectors=[detector],
        reporter=None,
        policy=policy,
        executor=executor,
        member_name="product-strategist",
    )
    session = _RecordingSession()
    ctx = AgentCallbackContext(
        agent=SimpleNamespace(ability_manager=None),
        session=session,
    )
    ctx.bind_steering_queue(asyncio.Queue())
    signal = Signal(
        kind=SignalKind.AFTER_TOOL_CALL,
        member_name="product-strategist",
        tool_name="view_task",
        tool_args={"action": "list"},
        tool_result={"content": "no claimable tasks"},
    )

    await monitor.start(ctx)
    try:
        await monitor.handle(signal, ctx)
        await monitor.handle(signal, ctx)
        assert not ctx.has_force_finish_request

        anomalies = await monitor.handle(signal, ctx)

        assert any(
            anomaly.kind == AnomalyKind.TOOL_CALL_LOOP and anomaly.severity == Severity.CRITICAL
            for anomaly in anomalies
        )
        request = ctx.consume_force_finish()
        assert request is not None
        assert request.result["result_type"] == "error"
    finally:
        await monitor.stop()

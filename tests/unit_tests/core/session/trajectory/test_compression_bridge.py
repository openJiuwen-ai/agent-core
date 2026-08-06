# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Compression-state capture: the summary that replaces compressed history
must land in the trajectory, because without it a trajectory reader cannot
reconstruct what the model actually saw after a compression pass."""
import pytest

from openjiuwen.core.context_engine.schema.context_state import (
    ContextCompressionMetric,
    ContextCompressionSaved,
    ContextCompressionState,
    ContextCompressionUsage,
)
from openjiuwen.core.runner import Runner
from openjiuwen.core.runner.callback.events import ContextEvents
from openjiuwen.core.session.tracer.tracer import Tracer
from openjiuwen.core.session.trajectory.collector import TrajectoryCollector
from openjiuwen.core.session.trajectory.config import TrajectoryConfig
from openjiuwen.core.session.trajectory.handler import TrajectoryCompressionBridge
from openjiuwen.core.session.trajectory.recorder import TrajectoryRecorder
from openjiuwen.core.session.trajectory.writer import read_trajectory
from openjiuwen.core.single_agent.schema.agent_card import AgentCard

pytestmark = pytest.mark.asyncio

SESSION_ID = "s1"
SUMMARY = "[DIALOGUE_MEMORY_BLOCK] user asked for X; agent produced Y"


class _FakeSession:
    """Session double exposing only what the bridge resolves: ``tracer()``."""

    def __init__(self):
        self._tracer = Tracer(session_id=SESSION_ID)

    def tracer(self):
        return self._tracer

    @property
    def trace_id(self) -> str:
        return self._tracer.trace_id


def _state(status: str = "completed", **overrides) -> ContextCompressionState:
    fields = {
        "operation_id": "op-1",
        "status": status,
        "phase": "get_context_window",
        "processor": "DialogueCompressor",
        "model": "compress-model",
        "before": ContextCompressionMetric(messages=30, tokens=9000),
        "after": ContextCompressionMetric(messages=8, tokens=2000),
        "saved": ContextCompressionSaved(messages=22, tokens=7000, percent=77.8),
        "compression_usage": ContextCompressionUsage(
            calls=1, input_tokens=9000, output_tokens=500, total_tokens=9500, total_cost=0.12
        ),
        "duration_ms": 2500,
        "summary": "Compressed 30 -> 8 messages",
        "compact_summary": SUMMARY,
    }
    fields.update(overrides)
    return ContextCompressionState(**fields)


@pytest.fixture
def collector(tmp_path):
    return TrajectoryCollector(SESSION_ID, TrajectoryConfig(root=str(tmp_path)))


@pytest.fixture
def bridge(collector):
    return TrajectoryCompressionBridge(collector)


def _trace_file(tmp_path, trace_id: str):
    files = list((tmp_path / SESSION_ID).glob(f"*_{trace_id}/trajectory.json"))
    assert len(files) == 1
    return files[0]


async def _read(collector, tmp_path, trace_id: str):
    await collector.finalize_all()
    return read_trajectory(_trace_file(tmp_path, trace_id))


async def test_completed_state_records_summary_and_compression_cost(bridge, collector, tmp_path):
    # The compact summary is the model's only memory of the compressed span;
    # the usage is the compression model's cost, otherwise absent from totals.
    session = _FakeSession()
    await bridge.on_compression_state(session_ref=session, state=_state(), context_id="ctx-1")

    trajectory = await _read(collector, tmp_path, session.trace_id)
    step = trajectory.steps[0]
    assert step.role == "system"
    assert step.extra["invoke_type"] == "context_compression"
    assert step.message == SUMMARY
    assert step.model_name == "compress-model"
    assert step.metrics.input_tokens == 9000
    assert step.metrics.output_tokens == 500
    assert step.metrics.cost == 0.12
    assert step.metrics.latency == 2.5
    assert step.extra["processor"] == "DialogueCompressor"
    assert step.extra["context_id"] == "ctx-1"
    assert step.extra["before_tokens"] == 9000
    assert step.extra["after_tokens"] == 2000
    assert step.extra["saved_tokens"] == 7000
    # Compression cost must be visible in the run's aggregate metrics.
    assert trajectory.final_metrics.total_tokens == 9500
    assert trajectory.final_metrics.total_cost == 0.12


async def test_failed_state_records_error_step(bridge, collector, tmp_path):
    # A failed compression that blows the context is exactly what a
    # trajectory postmortem needs to show.
    session = _FakeSession()
    await bridge.on_compression_state(
        session_ref=session,
        state=_state(status="failed", error="model timeout", compact_summary="", compression_usage=None),
    )

    trajectory = await _read(collector, tmp_path, session.trace_id)
    step = trajectory.steps[0]
    assert step.error.message == "model timeout"
    assert trajectory.final_metrics.success is False
    assert trajectory.final_metrics.error_count == 1


@pytest.mark.parametrize("status", ["started", "noop", "skipped"])
async def test_non_terminal_states_are_ignored(bridge, collector, tmp_path, status):
    # Progress chatter would bury the signal: only terminal states matter.
    await bridge.on_compression_state(session_ref=_FakeSession(), state=_state(status=status))
    await collector.finalize_all()
    assert not list(tmp_path.glob("**/trajectory.json"))


async def test_event_without_resolvable_trace_is_dropped(bridge, collector, tmp_path):
    await bridge.on_compression_state(session_ref=None, state=_state())
    await bridge.on_compression_state(session_ref=object(), state=_state())
    await collector.finalize_all()
    assert not list(tmp_path.glob("**/trajectory.json"))


async def test_filtering_mode_drops_states_of_unrecorded_sessions(collector, tmp_path):
    # attach() switches the collector to filtering mode; compression events
    # from sessions that are not being recorded must not create trajectories.
    collector.allow_agent("recorded-agent")
    bridge = TrajectoryCompressionBridge(collector)
    stranger = _FakeSession()
    await bridge.on_compression_state(session_ref=stranger, state=_state())

    recorded = _FakeSession()
    collector.bind_trace(recorded.trace_id, session_id=SESSION_ID, agent_name="recorded-agent")
    await bridge.on_compression_state(session_ref=recorded, state=_state())

    await collector.finalize_all()
    assert not list((tmp_path / SESSION_ID).glob(f"*_{stranger.trace_id}/trajectory.json"))
    assert _trace_file(tmp_path, recorded.trace_id).exists()


async def test_recorder_registers_and_unregisters_compression_callback(tmp_path):
    # The callback must live exactly as long as the tracer handlers: attach
    # subscribes, close unsubscribes (leaving no leak in the global framework).
    framework = Runner.callback_framework
    event = ContextEvents.CONTEXT_COMPRESSION_STATE
    before = len(framework.callbacks.get(event, []))
    recorder = TrajectoryRecorder(SESSION_ID, TrajectoryConfig(root=str(tmp_path)))
    try:
        recorder.attach(AgentCard(id="agent-1", name="recorded-agent"))
        assert len(framework.callbacks.get(event, [])) == before + 1
    finally:
        await recorder.close()
    assert len(framework.callbacks.get(event, [])) == before


async def test_recorder_end_to_end_records_compression_via_framework_trigger(tmp_path):
    # Full path: attach -> session binds -> the context engine's trigger call
    # shape (processor_state_recorder.emit) lands a step in that session's file.
    recorder = TrajectoryRecorder(SESSION_ID, TrajectoryConfig(root=str(tmp_path)))
    session = _FakeSession()
    try:
        recorder.attach(AgentCard(id="agent-1", name="recorded-agent"))
        recorder._agent_handler.bind_trace(session.trace_id, session_id=SESSION_ID, agent_name="recorded-agent")
        await Runner.callback_framework.trigger(
            ContextEvents.CONTEXT_COMPRESSION_STATE,
            context=None,
            session_ref=session,
            session_id=SESSION_ID,
            context_id="ctx-1",
            state=_state(),
        )
    finally:
        await recorder.close()
    trajectory = read_trajectory(_trace_file(tmp_path, session.trace_id))
    assert trajectory.steps[0].message == SUMMARY
    assert trajectory.steps[0].extra["invoke_type"] == "context_compression"

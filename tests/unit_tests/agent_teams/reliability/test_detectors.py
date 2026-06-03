# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for member-internal reliability detectors."""

from openjiuwen.agent_teams.reliability.anomaly import AnomalyKind, Severity
from openjiuwen.agent_teams.reliability.detectors.compaction import FrequentCompactionDetector
from openjiuwen.agent_teams.reliability.detectors.model_error import ModelStreamErrorDetector
from openjiuwen.agent_teams.reliability.detectors.output_length import OutputLengthDetector
from openjiuwen.agent_teams.reliability.detectors.repeat_tool import RepeatToolCallDetector
from openjiuwen.agent_teams.reliability.detectors.tool_error import ToolErrorRateDetector
from openjiuwen.agent_teams.reliability.signals import Signal, SignalKind


class _Clock:
    """Deterministic clock for window-based detectors."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def _tool_err(member: str = "m1", error: str = "boom") -> Signal:
    return Signal(kind=SignalKind.TOOL_EXCEPTION, member_name=member, tool_name="run", error=error)


def _tool_ok(member: str = "m1") -> Signal:
    return Signal(kind=SignalKind.AFTER_TOOL_CALL, member_name=member, tool_name="run")


# ---- ToolErrorRateDetector ----

def test_tool_error_consecutive_triggers_medium():
    clock = _Clock()
    det = ToolErrorRateDetector(window_seconds=60.0, rate_threshold=100, consecutive_threshold=3, now=clock)
    assert det.observe(_tool_err()) is None
    assert det.observe(_tool_err()) is None
    anomaly = det.observe(_tool_err())
    assert anomaly is not None
    assert anomaly.severity == Severity.MEDIUM
    assert anomaly.kind == AnomalyKind.TOOL_ERROR_RATE


def test_tool_error_success_resets_streak():
    clock = _Clock()
    det = ToolErrorRateDetector(window_seconds=60.0, rate_threshold=100, consecutive_threshold=3, now=clock)
    det.observe(_tool_err())
    det.observe(_tool_err())
    det.observe(_tool_ok())
    assert det.observe(_tool_err()) is None


def test_tool_error_rate_window_triggers():
    clock = _Clock()
    det = ToolErrorRateDetector(window_seconds=60.0, rate_threshold=5, consecutive_threshold=100, now=clock)
    results = [det.observe(_tool_err()) for _ in range(5)]
    assert results[-1] is not None
    assert results[-1].severity == Severity.MEDIUM


def test_tool_error_escalates_to_high():
    clock = _Clock()
    det = ToolErrorRateDetector(window_seconds=60.0, rate_threshold=100, consecutive_threshold=3, now=clock)
    results = [det.observe(_tool_err()) for _ in range(6)]
    severities = [r.severity for r in results if r is not None]
    assert Severity.MEDIUM in severities
    assert Severity.HIGH in severities


def test_tool_error_window_evicts_old_events():
    clock = _Clock()
    det = ToolErrorRateDetector(window_seconds=10.0, rate_threshold=3, consecutive_threshold=100, now=clock)
    det.observe(_tool_err())
    clock.advance(20.0)
    det.observe(_tool_err())
    assert det.observe(_tool_err()) is None


# ---- ModelStreamErrorDetector ----

def test_model_error_consecutive_triggers():
    clock = _Clock()
    det = ModelStreamErrorDetector(window_seconds=120.0, rate_threshold=100, consecutive_threshold=2, now=clock)
    first = det.observe(Signal(kind=SignalKind.MODEL_EXCEPTION, member_name="m", error="rate limit"))
    second = det.observe(Signal(kind=SignalKind.MODEL_EXCEPTION, member_name="m", error="rate limit"))
    assert first is None
    assert second is not None
    assert second.kind == AnomalyKind.MODEL_ERROR


# ---- OutputLengthDetector ----

def test_output_text_too_long():
    det = OutputLengthDetector(text_threshold=100, thinking_threshold=100)
    anomaly = det.observe(Signal(kind=SignalKind.AFTER_MODEL_CALL, member_name="m", text_len=200, thinking_len=0))
    assert anomaly is not None
    assert anomaly.kind == AnomalyKind.OUTPUT_TOO_LONG
    assert anomaly.severity == Severity.LOW


def test_output_thinking_reported_before_text():
    det = OutputLengthDetector(text_threshold=100, thinking_threshold=100)
    signal = Signal(kind=SignalKind.AFTER_MODEL_CALL, member_name="m", text_len=200, thinking_len=200)
    first = det.observe(signal)
    second = det.observe(signal)
    assert first.kind == AnomalyKind.THINKING_TOO_LONG
    assert second.kind == AnomalyKind.OUTPUT_TOO_LONG
    assert det.observe(signal) is None


def test_output_under_threshold_silent():
    det = OutputLengthDetector(text_threshold=100, thinking_threshold=100)
    signal = Signal(kind=SignalKind.AFTER_MODEL_CALL, member_name="m", text_len=50, thinking_len=50)
    assert det.observe(signal) is None


# ---- FrequentCompactionDetector ----

def test_compaction_frequency_triggers_medium():
    clock = _Clock()
    det = FrequentCompactionDetector(window_seconds=300.0, frequency_threshold=3, drop_ratio=0.3, now=clock)
    counts = [10, 20, 5, 25, 6, 30, 7]
    results = [
        det.observe(Signal(kind=SignalKind.BEFORE_MODEL_CALL, member_name="m", message_count=c)) for c in counts
    ]
    fired = [r for r in results if r is not None]
    assert fired
    assert fired[0].kind == AnomalyKind.FREQUENT_COMPACTION
    assert fired[0].severity == Severity.MEDIUM


def test_compaction_ignores_monotonic_growth():
    clock = _Clock()
    det = FrequentCompactionDetector(window_seconds=300.0, frequency_threshold=2, drop_ratio=0.3, now=clock)
    counts = [5, 10, 15, 20]
    results = [
        det.observe(Signal(kind=SignalKind.BEFORE_MODEL_CALL, member_name="m", message_count=c)) for c in counts
    ]
    assert all(r is None for r in results)


# ---- RepeatToolCallDetector ----

def _call_cycle(
    det: RepeatToolCallDetector,
    tool: str,
    args: dict,
    outcome_kind: SignalKind = SignalKind.AFTER_TOOL_CALL,
    error: str | None = None,
    member: str = "m",
):
    det.observe(Signal(kind=SignalKind.BEFORE_TOOL_CALL, member_name=member, tool_name=tool, tool_args=args))
    return det.observe(Signal(kind=outcome_kind, member_name=member, tool_name=tool, error=error))


def test_repeat_generic_repeat_low():
    det = RepeatToolCallDetector(repeat_warn=10, pingpong_warn=100, loop_block=100, global_stop=200)
    results = [_call_cycle(det, "read", {"p": "a"}) for _ in range(10)]
    fired = [r for r in results if r is not None]
    assert fired
    assert fired[0].severity == Severity.LOW
    assert fired[0].kind == AnomalyKind.REPEAT_TOOL_CALL


def test_repeat_no_progress_high():
    det = RepeatToolCallDetector(repeat_warn=10, pingpong_warn=100, loop_block=20, global_stop=30)
    seen = []
    for _ in range(20):
        result = _call_cycle(det, "read", {"p": "a"}, outcome_kind=SignalKind.TOOL_EXCEPTION, error="same")
        if result is not None:
            seen.append((result.severity, result.kind))
    assert any(sev == Severity.HIGH for sev, _ in seen)
    assert any(kind == AnomalyKind.TOOL_CALL_LOOP for _, kind in seen)


def test_repeat_global_circuit_critical():
    det = RepeatToolCallDetector(repeat_warn=10, pingpong_warn=100, loop_block=20, global_stop=30)
    severities = []
    for _ in range(30):
        result = _call_cycle(det, "read", {"p": "a"}, outcome_kind=SignalKind.TOOL_EXCEPTION, error="same")
        if result is not None:
            severities.append(result.severity)
    assert Severity.CRITICAL in severities


def test_repeat_alternation_medium():
    det = RepeatToolCallDetector(repeat_warn=100, pingpong_warn=10, loop_block=100, global_stop=200)
    severities = []
    for index in range(10):
        tool = "read" if index % 2 == 0 else "write"
        result = _call_cycle(det, tool, {"p": "a"})
        if result is not None:
            severities.append(result.severity)
    assert Severity.MEDIUM in severities


def test_repeat_stable_hash_ignores_arg_order():
    det = RepeatToolCallDetector(repeat_warn=3, pingpong_warn=100, loop_block=100, global_stop=200)
    _call_cycle(det, "run", {"a": 1, "b": 2})
    _call_cycle(det, "run", {"b": 2, "a": 1})
    result = _call_cycle(det, "run", {"a": 1, "b": 2})
    assert result is not None
    assert result.severity == Severity.LOW

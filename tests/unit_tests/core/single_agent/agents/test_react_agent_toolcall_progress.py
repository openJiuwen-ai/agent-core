"""Unit tests for the tool_call streaming progress heartbeat helper.

Covers the decision logic (grace/interval throttle) and payload shape. The
loop wiring in react_agent._railed_model_call is a thin caller of this helper
and is verified end-to-end (see plan Task 4 smoke).
"""
from openjiuwen.core.single_agent.agents.react_agent import (
    _TOOLCALL_PROGRESS_GRACE_S,
    _TOOLCALL_PROGRESS_INTERVAL_S,
    _maybe_toolcall_progress_output,
)


def test_no_emit_within_grace_window():
    # only 5s of tool_calls-only streaming (< 15s grace) → no frame
    out = _maybe_toolcall_progress_output(
        last_contentful_at=0.0,
        last_progress_at=0.0,
        now=5.0,
        chunk_count=10,
        index=3,
    )
    assert out is None


def test_no_emit_when_progress_recently_sent():
    # past grace, but last progress frame was only 5s ago (< 30s interval) → no frame
    out = _maybe_toolcall_progress_output(
        last_contentful_at=0.0,
        last_progress_at=40.0,  # emitted at 40s
        now=45.0,               # only 5s since last → throttle suppresses
        chunk_count=100,
        index=5,
    )
    assert out is None


def test_emit_after_grace_and_interval_elapsed():
    # 35s since contentful (>=15 grace) AND 35s since last progress (>=30 interval) → emit
    out = _maybe_toolcall_progress_output(
        last_contentful_at=0.0,
        last_progress_at=0.0,
        now=35.0,
        chunk_count=200,
        index=7,
    )
    assert out is not None
    assert out.type == "llm_toolcall_progress"
    assert out.index == 7
    assert out.payload == {
        "elapsed_s": 35.0,
        "chunk_count": 200,
        "result_type": "answer",
    }


def test_emit_cadence_30s():
    # at now=15.0: grace met (15>=15) but interval since t=0 not yet 30s → suppress
    out = _maybe_toolcall_progress_output(
        last_contentful_at=0.0, last_progress_at=0.0, now=15.0, chunk_count=50, index=1
    )
    assert out is None  # grace met but interval (since t=0) not yet 30s
    # at now=30.0: grace 30>=15, interval 30>=30 → emit
    out = _maybe_toolcall_progress_output(
        last_contentful_at=0.0, last_progress_at=0.0, now=30.0, chunk_count=100, index=2
    )
    assert out is not None and out.type == "llm_toolcall_progress"


def test_constants_values():
    assert _TOOLCALL_PROGRESS_GRACE_S == 15.0
    assert _TOOLCALL_PROGRESS_INTERVAL_S == 30.0

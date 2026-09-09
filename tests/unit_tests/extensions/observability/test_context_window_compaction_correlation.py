# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from openjiuwen.extensions.observability.span_context import (
    consume_context_window_compaction,
    queue_context_window_compaction,
    reset_state,
)


def setup_function() -> None:
    reset_state()


def teardown_function() -> None:
    reset_state()


def _queue(
    operation_id: str,
    *,
    session_id: str = "session-1",
    subject_id: str = "main",
    request_id: str = "request-1",
    step_id: str = "step-1",
) -> bool:
    return queue_context_window_compaction(
        session_id=session_id,
        subject_id=subject_id,
        request_id=request_id,
        step_id=step_id,
        operation_id=operation_id,
    )


def _consume(
    *,
    session_id: str = "session-1",
    subject_id: str = "main",
    request_id: str = "request-1",
    step_id: str = "step-1",
) -> str | None:
    return consume_context_window_compaction(
        session_id=session_id,
        subject_id=subject_id,
        request_id=request_id,
        step_id=step_id,
    )


def test_completed_compaction_is_consumed_once_by_the_next_matching_window() -> None:
    assert _queue("operation-1") is True

    assert _consume() == "operation-1"
    assert _consume() is None


def test_window_without_completed_compaction_has_no_transition() -> None:
    assert _consume() is None
    assert _queue("") is False
    assert _queue("operation-without-step", step_id="") is False
    assert _consume() is None


def test_pending_compactions_are_isolated_by_session_subject_request_and_step() -> None:
    assert _queue("session-2-operation", session_id="session-2") is True
    assert _queue("subject-operation", subject_id="subagent:one") is True
    assert _queue("request-operation", request_id="request-2") is True
    assert _queue("step-operation", step_id="step-2") is True

    assert _consume() is None
    assert _consume(session_id="session-2") == "session-2-operation"
    assert _consume(subject_id="subagent:one") == "subject-operation"
    assert _consume(request_id="request-2") == "request-operation"
    assert _consume(step_id="step-2") == "step-operation"

# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from openjiuwen.extensions.observability.span_context import (
    consume_context_window_compaction,
    context_compaction_number,
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
    step_id: str = "step-1",
) -> bool:
    return queue_context_window_compaction(
        session_id=session_id,
        subject_id=subject_id,
        step_id=step_id,
        operation_id=operation_id,
    )


def _consume(
    *,
    session_id: str = "session-1",
    subject_id: str = "main",
    step_id: str = "step-1",
) -> str | None:
    return consume_context_window_compaction(
        session_id=session_id,
        subject_id=subject_id,
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


def test_pending_compactions_are_isolated_by_session_subject_and_step() -> None:
    assert _queue("session-2-operation", session_id="session-2") is True
    assert _queue("subject-operation", subject_id="subagent:one") is True
    assert _queue("step-operation", step_id="step-2") is True

    assert _consume() is None
    assert _consume(session_id="session-2") == "session-2-operation"
    assert _consume(subject_id="subagent:one") == "subject-operation"
    assert _consume(step_id="step-2") == "step-operation"


def test_a_compaction_queues_without_knowing_any_request_id() -> None:
    """A compaction is queued between model calls, so no request names it.

    Correlation used to key on a request id as well. The span a compaction
    is queued from is an agent or task span, which never carries one, so the
    key could not be built and every compaction was dropped before it was
    ever queued -- silently, and for every session.
    """
    assert _queue("operation-between-calls") is True

    # The window that later states the compaction's output belongs to a
    # request that did not exist when the compaction was queued.
    assert _consume() == "operation-between-calls"
    assert _consume() is None


def test_compactions_of_one_step_are_claimed_in_order() -> None:
    """Several compactions in a step are claimed oldest-first."""
    assert _queue("first") is True
    assert _queue("second") is True

    assert _consume() == "first"
    assert _consume() == "second"
    assert _consume() is None


def test_every_attempt_of_one_compaction_states_the_same_number() -> None:
    """A throttled compaction is retried; the retries are not new compactions.

    The number belongs to the operation, so a reader sees one compaction that
    took several tries rather than several compactions.
    """
    first = context_compaction_number(
        session_id="session-1", subject_id="main", operation_id="op-a"
    )
    retry = context_compaction_number(
        session_id="session-1", subject_id="main", operation_id="op-a"
    )
    second = context_compaction_number(
        session_id="session-1", subject_id="main", operation_id="op-b"
    )

    assert (first, retry, second) == (1, 1, 2)


def test_compaction_numbers_count_within_one_subject() -> None:
    assert context_compaction_number(
        session_id="session-1", subject_id="main", operation_id="op-a"
    ) == 1
    # A subagent compacts its own context and counts from one.
    assert context_compaction_number(
        session_id="session-1", subject_id="subagent:one", operation_id="op-b"
    ) == 1
    assert context_compaction_number(
        session_id="session-2", subject_id="main", operation_id="op-c"
    ) == 1


def test_a_compaction_without_an_operation_states_no_number() -> None:
    """Zero means unnumbered, so a caller never stamps a misleading first."""
    assert context_compaction_number(
        session_id="session-1", subject_id="main", operation_id=""
    ) == 0
    assert context_compaction_number(
        session_id="session-1", subject_id="", operation_id="op-a"
    ) == 0

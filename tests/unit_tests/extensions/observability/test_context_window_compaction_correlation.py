# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from openjiuwen.extensions.observability.span_context import (
    advance_context_window,
    context_compaction_number,
    current_context_window_messages,
    reset_state,
)


def setup_function() -> None:
    reset_state()


def teardown_function() -> None:
    reset_state()


def test_subject_without_a_committed_window_reads_back_none() -> None:
    assert current_context_window_messages(session_id="session-1", subject_id="main") is None


def test_last_committed_window_reads_back_as_its_messages() -> None:
    """The canonical state is enough to restate the window it describes.

    A compaction commits the window it produced without any model request
    having stated the one before it, so the previous window must be
    readable from the state alone: message content, not just identity.
    """
    first = [
        {"message_id": "openjiuwen:request-system-slot:0", "role": "system", "content": "rules"},
        {"message_id": "u1", "role": "user", "content": "hello", "metadata": {"k": 1}},
    ]
    second = [*first, {"message_id": "a1", "role": "assistant", "content": "hi"}]
    advance_context_window(
        session_id="session-1", subject_id="main", window_id="w1", messages=first
    )
    advance_context_window(
        session_id="session-1", subject_id="main", window_id="w2", messages=second
    )

    assert current_context_window_messages(session_id="session-1", subject_id="main") == second
    # Another subject of the same session keeps its own window.
    assert current_context_window_messages(session_id="session-1", subject_id="subagent:one") is None


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

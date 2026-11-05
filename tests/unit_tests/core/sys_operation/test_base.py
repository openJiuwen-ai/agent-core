# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""What a sys_operation event records: clipped payloads, and its status.

A sys_operation event records what an operation did, not the bytes it moved.
Clipping runs on every logged operation, so it has to stay both cheap and
total: anything it raises on would take the operation down with it.

The status goes out with every event and is what a reader filters the stream on,
so it has to say something true at the point the event is emitted.
"""
from __future__ import annotations

from collections import namedtuple

from openjiuwen.core.common.logging.events import EventStatus, LogEventType
from openjiuwen.core.sys_operation.base import (
    BaseOperation,
    LOG_PAYLOAD_MAX_CHARS,
    OperationMode,
    _LOG_PAYLOAD_MAX_DEPTH,
    _LOG_PAYLOAD_MAX_ITEMS,
    _clip_log_payload,
)
from openjiuwen.core.sys_operation.config import LocalWorkConfig


def test_short_values_pass_through_untouched() -> None:
    payload = {"path": "/tmp/a.txt", "mode": "text", "line_range": (1, 20)}

    assert _clip_log_payload(payload) == {
        "path": "/tmp/a.txt",
        "mode": "text",
        "line_range": [1, 20],
    }


def test_long_text_is_clipped_with_the_omitted_count() -> None:
    body = "x" * (LOG_PAYLOAD_MAX_CHARS + 500)

    clipped = _clip_log_payload({"content": body})["content"]

    assert clipped.startswith("x" * LOG_PAYLOAD_MAX_CHARS)
    assert clipped.endswith("...(+500 chars omitted)")


def test_long_binary_content_is_replaced_by_its_size() -> None:
    blob = b"\x00" * (LOG_PAYLOAD_MAX_CHARS + 1)

    assert _clip_log_payload(blob) == f"<{LOG_PAYLOAD_MAX_CHARS + 1} bytes omitted>"


def test_long_sequences_keep_a_head_and_report_the_rest() -> None:
    """A directory listing is many short entries, which no length limit catches."""
    entries = [f"file_{index}.txt" for index in range(_LOG_PAYLOAD_MAX_ITEMS + 10)]

    clipped = _clip_log_payload(entries)

    assert len(clipped) == _LOG_PAYLOAD_MAX_ITEMS + 1
    assert clipped[:_LOG_PAYLOAD_MAX_ITEMS] == entries[:_LOG_PAYLOAD_MAX_ITEMS]
    assert clipped[-1] == "...(+10 items omitted)"


def test_namedtuple_payloads_do_not_raise() -> None:
    """Sequences come back as plain lists, so odd sequence types cannot break it.

    Rebuilding a ``namedtuple`` from a list raises, and this runs while a log
    event is being assembled -- the operation itself would fail with it.
    """
    entry = namedtuple("_Entry", "name size")(name="a.txt", size=12)

    assert _clip_log_payload(entry) == ["a.txt", 12]


def test_sets_are_rendered_as_lists() -> None:
    assert sorted(_clip_log_payload({"a", "b"})) == ["a", "b"]
    assert sorted(_clip_log_payload(frozenset({"a", "b"}))) == ["a", "b"]


def test_pathological_nesting_is_summarized_instead_of_walked() -> None:
    payload: object = "leaf"
    for _ in range(_LOG_PAYLOAD_MAX_DEPTH + 2):
        payload = {"next": payload}

    rendered = repr(_clip_log_payload(payload))

    assert "omitted at depth" in rendered


def _operation() -> BaseOperation:
    """A bare operation, enough to build events from."""
    return BaseOperation(
        name="shell",
        mode=OperationMode.LOCAL,
        description="local shell operation",
        run_config=LocalWorkConfig(),
    )


def test_start_events_report_pending_rather_than_success() -> None:
    """A start event precedes the work, so it cannot report an outcome yet.

    Observed in production: every start event carried the ``SUCCESS`` default,
    which reads as a completed operation to anyone filtering the stream on status.
    """
    event = _operation()._create_sys_operation_event(
        event_type=LogEventType.SYS_OP_START,
        method_name="execute_cmd",
    )

    assert event.status is EventStatus.PENDING


def test_end_events_keep_reporting_success() -> None:
    """The operation completed; what the command exited with is a separate fact."""
    event = _operation()._create_sys_operation_event(
        event_type=LogEventType.SYS_OP_END,
        method_name="execute_cmd",
        method_result={"data": {"exit_code": 1}},
    )

    assert event.status is EventStatus.SUCCESS
    assert event.method_result["data"]["exit_code"] == 1


def test_an_explicit_status_is_kept_on_a_start_event() -> None:
    """The default only applies where the caller expressed no status of its own."""
    event = _operation()._create_sys_operation_event(
        event_type=LogEventType.SYS_OP_START,
        method_name="execute_cmd",
        status=EventStatus.CANCELLED,
    )

    assert event.status is EventStatus.CANCELLED


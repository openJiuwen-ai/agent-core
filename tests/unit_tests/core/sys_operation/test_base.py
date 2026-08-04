# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Log payload clipping for sys_operation events.

A sys_operation event records what an operation did, not the bytes it moved.
Clipping runs on every logged operation, so it has to stay both cheap and
total: anything it raises on would take the operation down with it.
"""
from __future__ import annotations

from collections import namedtuple

from openjiuwen.core.sys_operation.base import (
    LOG_PAYLOAD_MAX_CHARS,
    _LOG_PAYLOAD_MAX_DEPTH,
    _LOG_PAYLOAD_MAX_ITEMS,
    _clip_log_payload,
)


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

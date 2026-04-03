# coding: utf-8
"""Tests for auto-detecting placeholder style ({} vs %) in log messages."""

import logging
from io import StringIO
from typing import (
    Any,
    Dict,
)

from openjiuwen.core.common.logging.base_impl import StructuredLoggerMixin
from openjiuwen.core.common.logging.default.default_impl import DefaultLogger
from openjiuwen.core.common.logging.loguru.loguru_impl import LoguruLogger

_fmt = StructuredLoggerMixin._auto_format_message


class TestAutoFormatMessage:
    """Unit tests for _auto_format_message static method."""

    def test_no_args_returns_msg(self) -> None:
        assert _fmt("hello world", ()) == "hello world"

    def test_percent_s(self) -> None:
        assert _fmt("hello %s", ("world",)) == "hello world"

    def test_percent_d(self) -> None:
        assert _fmt("count: %d", (42,)) == "count: 42"

    def test_percent_multiple(self) -> None:
        assert _fmt("%s has %d items", ("list", 3)) == "list has 3 items"

    def test_brace_positional(self) -> None:
        assert _fmt("hello {}", ("world",)) == "hello world"

    def test_brace_indexed(self) -> None:
        assert _fmt("{0} has {1} items", ("list", 3)) == "list has 3 items"

    def test_brace_format_spec(self) -> None:
        assert _fmt("pi is {:.2f}", (3.14159,)) == "pi is 3.14"

    def test_brace_repr(self) -> None:
        assert _fmt("value is {!r}", ("abc",)) == "value is 'abc'"

    def test_brace_priority_over_percent(self) -> None:
        result = _fmt("{}% done", (50,))
        assert result == "50% done"

    def test_no_placeholder_with_args_falls_through(self) -> None:
        result = _fmt("no placeholders here", ("extra",))
        assert result == "no placeholders here"

    def test_non_string_msg(self) -> None:
        assert _fmt(12345, ()) == "12345"


def _make_default_logger(stream: StringIO) -> DefaultLogger:
    config: Dict[str, Any] = {"level": "DEBUG", "output": []}
    dl = DefaultLogger("test_auto_fmt", config)
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    dl._logger.addHandler(handler)
    return dl


class TestDefaultLoggerAutoFormat:
    """Integration: DefaultLogger handles both placeholder styles."""

    def test_percent_style(self) -> None:
        buf = StringIO()
        dl = _make_default_logger(buf)
        dl.info("user %s logged in", "alice")
        assert "user alice logged in" in buf.getvalue()

    def test_brace_style(self) -> None:
        buf = StringIO()
        dl = _make_default_logger(buf)
        dl.info("user {} logged in", "bob")
        assert "user bob logged in" in buf.getvalue()

    def test_mixed_brace_percent_prefers_brace(self) -> None:
        buf = StringIO()
        dl = _make_default_logger(buf)
        dl.info("{}% complete", 75)
        assert "75% complete" in buf.getvalue()


def _make_loguru_logger(stream: StringIO) -> LoguruLogger:
    config: Dict[str, Any] = {
        "level": "DEBUG",
        "effective_level": "DEBUG",
        "sinks": [
            {
                "target": stream,
                "level": "DEBUG",
                "format": "{message}",
                "colorize": False,
            }
        ],
    }
    return LoguruLogger("test_auto_fmt_loguru", config)


class TestLoguruLoggerAutoFormat:
    """Integration: LoguruLogger handles both placeholder styles."""

    def test_percent_style(self) -> None:
        buf = StringIO()
        ll = _make_loguru_logger(buf)
        ll.info("user %s logged in", "charlie")
        assert "user charlie logged in" in buf.getvalue()
        ll.close()

    def test_brace_style(self) -> None:
        buf = StringIO()
        ll = _make_loguru_logger(buf)
        ll.info("user {} logged in", "dave")
        assert "user dave logged in" in buf.getvalue()
        ll.close()

# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Complete records are bounded before console/file output and rotation."""

import logging
import os
import sys
from io import StringIO

import pytest

from openjiuwen.core.common.logging.default import DefaultLogger


RECORD_LIMIT = 1024 * 1024


@pytest.fixture
def make_logger(tmp_path, monkeypatch):
    """Use isolated logger names, streams and files without global config edits."""
    loggers = []
    console = StringIO()

    def create(*, dated=False, output=None, log_format="%(levelname)s | %(message)s", max_bytes=20 * RECORD_LIMIT):
        monkeypatch.setattr(sys, "stdout", console)
        log = DefaultLogger(
            f"test_bounded_records_{len(loggers)}",
            {
                "level": "INFO",
                "propagate": False,
                "format": log_format,
                "output": output or ["console"],
                "log_file": str(tmp_path / "run" / "jiuwen.log"),
                "log_path": str(tmp_path),
                "log_date_dirs": dated,
                "max_bytes": max_bytes,
                "backup_count": 2,
            },
        )
        loggers.append(log)
        return log, console

    yield create
    for log in loggers:
        for handler in log.logger().handlers[:]:
            handler.close()
            log.logger().removeHandler(handler)


def _record(message, *, exc_info=None, stack_info=None):
    return logging.LogRecord("bounded", logging.INFO, __file__, 1, message, (), exc_info, sinfo=stack_info)


def _written_size(text):
    """Model FileHandler's native newline translation, including its terminator."""
    return len((text + "\n").replace("\n", os.linesep).encode("utf-8"))


@pytest.mark.parametrize("delta", [-1, 0, 1])
def test_exact_record_boundary(make_logger, delta):
    log, _ = make_logger(log_format="%(message)s")
    formatter = log.logger().handlers[0].formatter
    message = "x" * (RECORD_LIMIT - len(os.linesep) + delta)
    result = formatter.format(_record(message))

    assert _written_size(result) <= RECORD_LIMIT
    if delta <= 0:
        assert result == message
    else:
        assert result.endswith(f"[TRUNCATED original_bytes={len(message)}]")


@pytest.mark.parametrize("message", ["hello 中文", "", "line one\nline two"])
def test_normal_format_matches_stdlib_without_mutating_record(make_logger, message):
    log_format = "%(asctime)s | %(levelname)s | %(message)s"
    log, _ = make_logger(log_format=log_format)
    record = _record(message)
    before = record.__dict__.copy()
    result = log.logger().handlers[0].formatter.format(record)

    assert record.__dict__ == before
    assert result == logging.Formatter(log_format, datefmt="%Y-%m-%d %H:%M:%S").format(record)


@pytest.mark.parametrize("payload", [" " * (2 * RECORD_LIMIT), "中🙂" * (RECORD_LIMIT // 2)], ids=["spaces", "unicode"])
def test_oversized_message_preserves_prefix_and_valid_utf8(make_logger, payload):
    log, console = make_logger()
    message = "Added GLOBAL tag to resource. " + payload
    log.info(message)
    written = console.getvalue()

    assert len(written.encode("utf-8")) <= RECORD_LIMIT
    assert written.startswith("INFO | Added GLOBAL tag to resource. ")
    assert written.endswith(f"[TRUNCATED original_bytes={len(('INFO | ' + message).encode('utf-8'))}]\n")
    assert "\ufffd" not in written


@pytest.mark.parametrize("source", ["context", "padding", "exception", "stack"])
def test_complete_format_is_bounded_and_record_is_unchanged(make_logger, source):
    log_format = "%(levelname)s | %(trace_id)s | %(message)s"
    if source == "padding":
        log_format = f"%(levelname)s | %(message){2 * RECORD_LIMIT}s"
    log, _ = make_logger(log_format=log_format)
    exc_info = None
    if source == "exception":
        try:
            raise ValueError("exception detail " + "z" * (2 * RECORD_LIMIT))
        except ValueError:
            exc_info = sys.exc_info()
    record = _record(
        "short", exc_info=exc_info, stack_info="frame\n" * (RECORD_LIMIT // 3) if source == "stack" else None
    )
    record.trace_id = "trace" + (" " * (2 * RECORD_LIMIT) if source == "context" else "")
    before = record.__dict__.copy()
    formatter = log.logger().handlers[0].formatter
    result = formatter.format(record)

    assert _written_size(result) <= RECORD_LIMIT
    assert result.startswith("INFO | ")
    assert "[TRUNCATED original_bytes=" in result
    assert record.__dict__ == before
    # Formatting for rollover and then emit must produce the same bounded text.
    assert formatter.format(record) == result
    assert "[TRUNCATED" not in logging.Formatter(log_format).format(record)


@pytest.mark.parametrize("dated", [False, True])
def test_actual_console_and_files_remain_bounded_through_rotation(make_logger, tmp_path, dated):
    log, console = make_logger(dated=dated, output=["console", "file"], max_bytes=RECORD_LIMIT)
    for index in range(3):
        log.info("event %s %s", index, " " * (2 * RECORD_LIMIT))
    log.info("following normal record")
    for handler in log.logger().handlers:
        handler.flush()

    files = sorted(path for path in tmp_path.rglob("*") if path.is_file())
    assert len(files) >= 2, "oversized records must still permit normal rotation"
    contents = [path.read_bytes() for path in files]
    assert any(b"[TRUNCATED original_bytes=" in content for content in contents)
    for content in contents:
        for line in content.splitlines(keepends=True):
            assert len(line) <= RECORD_LIMIT
            line.decode("utf-8")
    assert any(b"following normal record" in content for content in contents)
    lines = console.getvalue().splitlines(keepends=True)
    assert len(lines) == 4
    assert all(len(line.encode("utf-8")) <= RECORD_LIMIT for line in lines)


@pytest.mark.parametrize("dated", [False, True])
def test_multiline_exception_actual_file_size_is_bounded(make_logger, tmp_path, dated):
    log, _ = make_logger(dated=dated, output=["file"])
    try:
        raise ValueError("detail\n" * (RECORD_LIMIT // 4))
    except ValueError:
        log.error("failure captured", exc_info=True)
    for handler in log.logger().handlers:
        handler.flush()

    files = [path for path in tmp_path.rglob("*") if path.is_file()]
    assert len(files) == 1
    data = files[0].read_bytes()
    assert len(data) <= RECORD_LIMIT
    assert data.startswith(b"ERROR | failure captured")
    assert b"Traceback (most recent call last)" in data
    assert b"[TRUNCATED original_bytes=" in data
    data.decode("utf-8")

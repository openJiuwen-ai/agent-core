# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Controlled slow-output regression tests; no models or real pipes required."""

import asyncio
import inspect
import json
import logging
import os
import subprocess
import sys
import threading
from io import StringIO
from pathlib import Path

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.common.logging import set_member_id, set_session_id
from openjiuwen.core.common.logging.default import DefaultLogger, queued_output
from openjiuwen.extensions.observability.span_context import set_current_request_id


class RecordHandler(logging.Handler):
    def __init__(self, records):
        super().__init__()
        self.records = records

    def emit(self, record):
        self.records.append(record)


class GatedStream(StringIO):
    def __init__(self):
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def write(self, value):
        self.entered.set()
        if not self.release.wait(5):
            raise TimeoutError("test did not release output")
        return super().write(value)


@pytest.fixture
def make_logger(tmp_path, monkeypatch):
    loggers = []
    streams = []

    def create(stream=None, **overrides):
        if stream is None:
            stream = StringIO()
        streams.append(stream)
        monkeypatch.setattr(sys, "stdout", stream)
        config = {
            "output": ["console"],
            "log_file": str(tmp_path / "test.log"),
            "level": "DEBUG",
            "propagate": False,
            "format": "%(message)s",
        }
        config.update(overrides)
        logger = DefaultLogger(f"queued_test_{tmp_path.name}", config)
        loggers.append(logger)
        return logger, stream

    yield create
    for stream in streams:
        if isinstance(stream, GatedStream):
            stream.release.set()
    for logger in loggers:
        logger.close()
        logger._queue_handler.listener._thread.join(3)
        for handler in logger.logger().handlers[:]:
            logger.remove_handler(handler)
        for log_filter in logger.logger().filters[:]:
            logger.remove_filter(log_filter)
    set_session_id()
    set_member_id("")
    set_current_request_id()


@pytest.mark.asyncio
@pytest.mark.parametrize("destination", ["console", "file"])
async def test_slow_sink_does_not_block_event_loop(make_logger, destination, monkeypatch):
    stream = GatedStream()
    logger, _ = make_logger(stream, output=[destination])
    if destination == "file":
        sink = logger._queue_handler.listener.handlers[0]
        original_emit = sink.emit

        def emit(record):
            stream.write("")
            original_emit(record)

        monkeypatch.setattr(sink, "emit", emit)
    progressed = threading.Event()

    # A watchdog makes a synchronous regression fail instead of hanging pytest.
    def watchdog():
        if not progressed.wait(3):
            stream.release.set()

    guard = threading.Thread(target=watchdog)
    guard.start()
    try:
        logger.info("first")
        assert await asyncio.to_thread(stream.entered.wait, 2)
        for index in range(10):
            logger.info("queued %s", index)
        asyncio.get_running_loop().call_soon(progressed.set)
        await asyncio.sleep(0)
        assert progressed.is_set()
        assert not stream.release.is_set(), "event loop only resumed after the sink was released"
        assert logger.get_queue_stats()["accepted"] == 11
    finally:
        progressed.set()
        stream.release.set()
        guard.join(3)
    assert await asyncio.to_thread(logger.flush)


@pytest.mark.asyncio
async def test_context_captured_before_listener_and_explicit_fields_preserved(make_logger):
    stream = GatedStream()
    logger, _ = make_logger(stream, format="%(trace_id)s|%(member_id)s|%(request_id)s|%(message)s")
    logger.info("gate")
    assert await asyncio.to_thread(stream.entered.wait, 2)

    async def session(index):
        set_session_id(f"session-{index}")
        set_member_id(f"member-{index}")
        set_current_request_id(f"request-{index}")
        await asyncio.sleep(0)
        logger.info("message-%s", index)

    await asyncio.gather(*(session(index) for index in range(20)))
    logger.logger().info("explicit", extra={"trace_id": "explicit", "member_id": "m", "request_id": "r"})
    stream.release.set()
    assert await asyncio.to_thread(logger.flush)
    lines = stream.getvalue().splitlines()
    for index in range(20):
        assert f"session-{index}|member-{index}|request-{index}|message-{index}" in lines
    assert "explicit|m|r|explicit" in lines


def test_traceback_location_structured_fields_and_sibling_record(make_logger):
    logger, stream = make_logger(format="%(filename)s:%(lineno)d|%(message)s")
    records: list[logging.LogRecord] = []
    handler = RecordHandler(records)
    logger.add_handler(handler)
    try:
        raise ValueError("queued traceback")
    except ValueError:
        line = inspect.currentframe().f_lineno + 1
        logger.error("failed", exc_info=True)  # noqa: G201 - exercise the error(exc_info) contract
    logger.info("structured", event_type="custom", module_id="module-1", metadata={"count": 3})
    logger.exception("outside exception")
    assert logger.flush()
    text = stream.getvalue()
    assert f"test_queued_logger.py:{line}|failed" in text
    assert text.count("Traceback (most recent call last)") == 1
    assert "ValueError: queued traceback" in text
    assert records[0].exc_info[0] is ValueError
    assert records[0].exc_text is None
    assert records[0].msg == "failed"
    payload = json.loads(records[1].msg)
    assert payload["module_id"] == "module-1"
    assert payload["metadata"] == {"count": 3}
    logger.remove_handler(handler)


def test_full_queue_all_levels_and_non_recursive_counters(make_logger, monkeypatch):
    monkeypatch.setattr(queued_output, "QUEUE_CAPACITY", 3)
    stream = GatedStream()
    logger, _ = make_logger(stream)
    logger.info("in flight")
    assert stream.entered.wait(2)
    for index in range(3):
        logger.info("accepted %s", index)
    for level in (logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR, logging.CRITICAL):
        logger.log(level, "discard")
    stats = logger.get_queue_stats()
    assert stats["queued"] == stats["peak"] == stats["capacity"] == 3
    assert stats["accepted"] == 4
    assert stats["dropped_by_level"] == {10: 1, 20: 1, 30: 1, 40: 1, 50: 1}
    assert logger.flush(timeout=0) is False
    stream.release.set()
    assert logger.flush()
    assert stream.getvalue().splitlines() == ["in flight", "accepted 0", "accepted 1", "accepted 2"]


def test_close_full_queue_keeps_active_sink_open_and_rejects_replacement(make_logger, monkeypatch):
    monkeypatch.setattr(queued_output, "QUEUE_CAPACITY", 2)
    stream = GatedStream()
    logger, _ = make_logger(stream)
    output = logger._queue_handler
    sink = output.listener.handlers[0]
    logger.info("in flight")
    assert stream.entered.wait(2)
    logger.info("tail 1")
    logger.info("tail 2")
    assert output.stop(timeout=0) is False
    assert not sink._closed
    logger.error("late")
    assert logger.get_queue_stats()["discarded_on_close"] == 2
    assert logger.get_queue_stats()["dropped_by_level"] == {20: 2, 40: 1}
    # Avoid spending the public two-second budget in a deterministic unit test.
    original_stop = output.stop
    monkeypatch.setattr(output, "stop", lambda: original_stop(timeout=0))
    with pytest.raises(BaseError, match="still blocked"):
        logger.reconfigure(logger.get_config())
    assert logger._queue_handler is output
    stream.release.set()
    output.listener._thread.join(3)
    assert not output.listener._thread.is_alive()
    assert sink._closed
    assert stream.getvalue() == "in flight\n"
    logger.reconfigure(logger.get_config())
    logger.info("recovered")
    assert logger.flush()
    assert stream.getvalue() == "in flight\nrecovered\n"


def test_output_failure_does_not_kill_consumer_or_other_sink(make_logger, monkeypatch, tmp_path):
    logger, stream = make_logger(output=["console", "file"])
    sinks = logger._queue_handler.listener.handlers

    def fail(_value):
        raise OSError("controlled output failure")

    monkeypatch.setattr(stream, "write", fail)
    logger.info("file survives")
    assert logger.flush()
    assert logger.get_queue_stats()["output_failures"] == 1
    assert "file survives" in (tmp_path / "test.log").read_text()
    # Also cover a handler that raises directly, instead of using handleError.
    monkeypatch.setattr(sinks[0], "emit", fail)
    logger.info("consumer survives")
    assert logger.flush()
    assert logger.get_queue_stats()["output_failures"] == 2
    assert "consumer survives" in (tmp_path / "test.log").read_text()


def test_repeated_setup_reconfigure_stale_close_and_filters(make_logger):
    first, stream = make_logger()
    old_thread = first._queue_handler.listener._thread
    custom_records: list[logging.LogRecord] = []
    custom = RecordHandler(custom_records)
    first.add_handler(custom)
    second, _ = make_logger(stream)
    assert not old_thread.is_alive()
    first.close()  # must not close second's active sinks
    assert custom in second.logger().handlers
    second.info("once")
    assert second.flush()
    assert stream.getvalue() == "once\n"
    assert len(custom_records) == 1

    class Redact(logging.Filter):
        def filter(self, record):
            record.msg = str(record.msg).replace("secret", "***")
            return record.msg != "skip"

    redactor = Redact()
    second.add_filter(redactor)
    second.info("secret")
    second.info("skip")
    second.reconfigure({**second.get_config(), "format": "new:%(message)s", "level": "ERROR"})
    second.info("hidden")
    second.error("secret")
    assert second.flush()
    assert stream.getvalue() == "once\n***\nnew:***\n"
    assert len(custom_records) == 3
    second.remove_filter(redactor)
    second.remove_handler(custom)
    second.error("removed")
    assert len(custom_records) == 3
    second.close()
    assert not second._queue_handler.listener._thread.is_alive()


def test_root_propagation_and_raw_logger_extensions(make_logger):
    logger, stream = make_logger(propagate=True)
    root = logging.getLogger()
    records: list[logging.LogRecord] = []
    handler = RecordHandler(records)
    root.addHandler(handler)
    try:
        logger.logger().info("raw %s", "record")
        assert len(records) == 1  # root/custom extensions retain synchronous semantics
        assert logger.flush()
        assert stream.getvalue() == "raw record\n"
        logger.reconfigure({**logger.get_config(), "propagate": False})
        logger.info("local")
        assert logger.flush()
        assert len(records) == 1
    finally:
        root.removeHandler(handler)


def test_file_rotation_and_level_filtering(make_logger, tmp_path):
    logger, _ = make_logger(output=["file"], level="WARNING", max_bytes=1024, backup_count=2)
    logger.info("hidden")
    for index in range(15):
        logger.warning("%s %s", index, "x" * 150)
    logger.close()
    files = list(tmp_path.glob("test.log*"))
    assert len(files) == 3
    text = "".join(path.read_text() for path in files)
    assert "hidden" not in text
    assert "14 " in text
    assert text.count("x" * 150) == 15


@pytest.mark.parametrize("blocked", [False, True])
def test_interpreter_exit_drains_or_bounds_wait(tmp_path, blocked):
    script = """
import sys, threading
from openjiuwen.core.common.logging.default import DefaultLogger, queued_output
queued_output.DRAIN_TIMEOUT = 0.1
class Sink:
    def __init__(self):
        self.entered = threading.Event()
    def write(self, value):
        self.entered.set()
        if BLOCKED:
            threading.Event().wait()
    def flush(self): pass
sink = Sink()
sys.stdout = sink
logger = DefaultLogger("exit_test", {"output": ["console", "file"], "propagate": False,
    "level": "INFO", "log_file": LOG_FILE, "format": "%(message)s"})
logger.info("drain on exit")
assert sink.entered.wait(2)
""".replace("BLOCKED", repr(blocked)).replace("LOG_FILE", repr(str(tmp_path / "exit.log")))
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[5])}
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=tmp_path, env=env, capture_output=True, timeout=8, check=False
    )
    assert result.returncode == 0, result.stderr.decode()
    if not blocked:
        assert (tmp_path / "exit.log").read_text() == "drain on exit\n"


@pytest.mark.parametrize("invalid", [{"format": "invalid"}, {"log_file": "/etc/passwd"}])
def test_failed_reconfigure_leaves_previous_output_active(make_logger, invalid):
    logger, stream = make_logger()
    previous = logger.get_config()
    output = logger._queue_handler
    with pytest.raises((BaseError, ValueError)):
        logger.reconfigure({**previous, **invalid})
    assert logger.get_config() == previous
    assert logger._queue_handler is output
    assert not output.stopping
    logger.info("still working")
    assert logger.flush()
    assert stream.getvalue() == "still working\n"


def test_mutable_raw_message_is_snapshotted_before_queue(make_logger):
    stream = GatedStream()
    logger, _ = make_logger(stream)
    logger.info("gate")
    assert stream.entered.wait(2)
    message = {"state": "before"}
    logger.logger().info("state=%s", message)
    message["state"] = "after"
    stream.release.set()
    assert logger.flush()
    assert "before" in stream.getvalue()
    assert "after" not in stream.getvalue()


def test_custom_handler_context_and_stale_wrapper_barrier(make_logger):
    first, stream = make_logger()
    custom_stream = StringIO()
    custom = logging.StreamHandler(custom_stream)
    custom.setFormatter(logging.Formatter("%(trace_id)s|%(message)s"))
    first.add_handler(custom)
    set_session_id("session")
    first.info("before")
    second, _ = make_logger(stream)
    second.info("after")
    assert second.flush()
    assert custom_stream.getvalue() == "session|before\nsession|after\n"
    gated = GatedStream()
    third, _ = make_logger(gated)
    first.info("via old wrapper")
    assert gated.entered.wait(2)
    assert first.flush(0) is False
    assert first.get_queue_stats() == third.get_queue_stats()
    first.close()
    assert not third._queue_handler.stopping
    gated.release.set()
    assert first.flush()
    first.remove_handler(custom)


def test_sink_close_attempted_even_when_flush_raises(make_logger, monkeypatch):
    logger, _ = make_logger()
    sink = logger._queue_handler.listener.handlers[0]
    closed = threading.Event()

    def fail():
        raise OSError("controlled flush failure")

    monkeypatch.setattr(sink, "flush", fail)
    monkeypatch.setattr(sink, "close", closed.set)
    logger.close()
    assert closed.is_set()
    assert logger.get_queue_stats()["output_failures"] == 1
    assert not logger._queue_handler.listener._thread.is_alive()


def test_console_unbuffered_writer_retries_short_writes(tmp_path, monkeypatch):
    chunks = []

    def short_write(_descriptor, data):
        chunks.append(bytes(data[:2]))
        return len(chunks[-1])

    with queued_output._WriteAllFileIO(tmp_path / "short.log", "w") as raw:
        monkeypatch.setattr(raw, "_write", short_write)
        assert raw.write(b"abcdefg") == 7
        assert b"".join(chunks) == b"abcdefg"
        monkeypatch.setattr(raw, "_write", lambda *_: 0)
        with pytest.raises(BlockingIOError):
            raw.write(b"x")


def test_exit_with_blocked_standard_stdout_does_not_hold_global_buffer(tmp_path):
    # A temporary fd plus Event-controlled raw writes reproduces the relevant
    # BufferedWriter lock ownership without relying on an actual OS pipe.
    script = r"""
import io, sys, threading
from openjiuwen.core.common.logging.default import DefaultLogger, queued_output
queued_output.DRAIN_TIMEOUT = 0.1
entered = threading.Event()
class BlockedRaw(queued_output._WriteAllFileIO):
    def write(self, data):
        entered.set()
        threading.Event().wait()
queued_output._WriteAllFileIO = BlockedRaw
sys.stdout = io.TextIOWrapper(io.BufferedWriter(io.FileIO(LOG_FILE, "w")))
logger = DefaultLogger("buffered_exit", {"output": ["console"], "propagate": False, "level": "INFO"})
logger.info("blocked")
assert entered.wait(2)
""".replace("LOG_FILE", repr(str(tmp_path / "stdout.log")))
    result = subprocess.run([sys.executable, "-c", script], cwd=tmp_path, capture_output=True, timeout=8, check=False)
    assert result.returncode == 0, result.stderr.decode()
    assert b"Fatal Python error" not in result.stderr


def test_close_seals_ingress_while_other_thread_keeps_logging(make_logger):
    logger, stream = make_logger()
    sealed = threading.Event()
    finished = threading.Event()

    def producer():
        sealed.wait(3)
        for _ in range(50):
            logger.warning("after seal")
        finished.set()

    thread = threading.Thread(target=producer)
    thread.start()
    logger.info("before seal")
    logger._queue_handler.request_stop()
    sealed.set()
    logger.close()
    assert finished.wait(3)
    thread.join(3)
    logger._queue_handler.listener._thread.join(3)
    assert stream.getvalue() == "before seal\n"
    assert logger.get_queue_stats()["dropped_by_level"] == {logging.WARNING: 50}


def test_propagate_setting_reaches_default_backend():
    from openjiuwen.core.common.logging.default.config_provider import build_default_logger_config

    assert build_default_logger_config({"propagate": False}, "test")["propagate"] is False
    assert build_default_logger_config({}, "test")["propagate"] is True


def test_custom_text_wrapper_is_not_bypassed(tmp_path):
    import io

    class RedactingStream(io.TextIOWrapper):
        def write(self, text):
            return super().write(text.replace("secret", "***"))

    with RedactingStream(io.FileIO(tmp_path / "custom.log", "w"), write_through=True) as stream:
        handler = queued_output.ConsoleHandler(stream)
        assert handler.stream is stream
        handler.handle(logging.makeLogRecord({"msg": "secret"}))
        handler.close()
        assert not stream.closed
    assert (tmp_path / "custom.log").read_text() == "***\n"


def test_discarded_newer_records_do_not_complete_an_older_flush(make_logger, monkeypatch):
    stream = GatedStream()
    logger, _ = make_logger(stream)
    output = logger._queue_handler
    logger.info("in flight")
    assert stream.entered.wait(2)
    started = threading.Event()
    checked = threading.Event()
    observed = []
    wait_for = output.condition.wait_for

    def observe_wait(predicate, timeout):
        def observe():
            result = predicate()
            started.set()
            if output.discarded:
                observed.append(result)
                checked.set()
            return result

        return wait_for(observe, timeout)

    monkeypatch.setattr(output.condition, "wait_for", observe_wait)
    waiter = threading.Thread(target=logger.flush)
    waiter.start()
    try:
        assert started.wait(2)
        for _ in range(3):
            logger.info("newer tail")
        output.stop(0)
        assert checked.wait(2)
        assert observed[0] is False
    finally:
        stream.release.set()
        waiter.join(3)
    assert not waiter.is_alive()


def test_listener_start_failure_closes_owned_sinks(tmp_path, monkeypatch):
    handler = logging.FileHandler(tmp_path / "startup.log")

    def fail(_listener):
        raise RuntimeError("no thread available")

    monkeypatch.setattr(queued_output.OutputListener, "start", fail)
    with pytest.raises(RuntimeError, match="no thread available"):
        queued_output.QueuedOutput([handler])
    assert handler.stream is None
    assert handler._closed  # type: ignore[attr-defined]  # stdlib runtime ownership flag


def test_custom_level_drop_counters_are_bounded(make_logger, monkeypatch):
    monkeypatch.setattr(queued_output, "QUEUE_CAPACITY", 1)
    stream = GatedStream()
    logger, _ = make_logger(stream)
    logger.info("in flight")
    assert stream.entered.wait(2)
    logger.info("queued")
    for level in range(60, 1060):
        logger.log(level, "dropped")
    assert logger.get_queue_stats()["dropped_by_level"] == {logging.NOTSET: 1000}

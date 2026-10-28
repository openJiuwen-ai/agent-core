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


@pytest.mark.parametrize("same_name", [False, True])
def test_reconfiguration_timeout_keeps_old_output_writable(make_logger, monkeypatch, same_name):
    monkeypatch.setattr(queued_output, "QUEUE_CAPACITY", 2)
    stream = GatedStream()
    logger, _ = make_logger(stream)
    output = logger._queue_handler
    config = logger.get_config()
    logger.info("in flight")
    assert stream.entered.wait(2)
    logger.info("pending")
    wait_for = output.condition.wait_for
    stop = output.stop
    with monkeypatch.context() as patch:
        patch.setattr(output.condition, "wait_for", lambda predicate, timeout: wait_for(predicate, 0))
        patch.setattr(output, "stop", lambda: stop(0))
        for _ in range(2):
            with pytest.raises(BaseError, match="still blocked"):
                if same_name:
                    make_logger(stream, format="new:%(message)s", level="ERROR")
                else:
                    logger.reconfigure({**config, "format": "new:%(message)s", "level": "ERROR"})
            assert logger.get_config() == config
            assert logger._queue_handler is output
            assert not output.stopping
            assert not output.listener.handlers[0]._closed
        logger.info("after rejection")
        # Rejecting again with a full queue must not discard its pending tail.
        with pytest.raises(BaseError, match="still blocked"):
            logger.reconfigure(config)
        assert output.stats()["accepted"] == 3
        assert output.stats()["discarded_on_close"] == 0
        assert output.stats()["dropped_by_level"] == {}
    stream.release.set()
    assert logger.flush()
    assert stream.getvalue().splitlines() == ["in flight", "pending", "after rejection"]
    logger.reconfigure({**config, "format": "new:%(message)s"})
    assert not output.listener._thread.is_alive()
    logger.info("retry succeeded")
    assert logger.flush()
    assert stream.getvalue().endswith("new:retry succeeded\n")


def test_reconfigure_accepts_producers_while_waiting_and_forwards_old_handler(make_logger, monkeypatch):
    stream = GatedStream()
    logger, _ = make_logger(stream)
    previous = logger._queue_handler
    logger.info("in flight")
    assert stream.entered.wait(2)
    waiting = threading.Event()
    wait_for = previous.condition.wait_for
    errors = []

    def observe_wait(predicate, timeout):
        waiting.set()
        return wait_for(predicate, timeout)

    def reconfigure():
        try:
            logger.reconfigure({**logger.get_config(), "format": "new:%(message)s"})
        except Exception as error:  # noqa: BLE001 - report worker failures in the test thread
            errors.append(error)

    monkeypatch.setattr(previous.condition, "wait_for", observe_wait)
    worker = threading.Thread(target=reconfigure)
    worker.start()
    try:
        assert waiting.wait(2)
        logger.info("during wait")
        assert previous.stats()["accepted"] == 2
    finally:
        stream.release.set()
        worker.join(3)
    assert not worker.is_alive()
    assert not errors
    # logging.callHandlers may have obtained this handler before the list swap.
    previous.handle(logging.makeLogRecord({"msg": "already selected", "levelno": logging.INFO}))
    assert logger.flush()
    assert stream.getvalue().splitlines() == ["in flight", "during wait", "new:already selected"]
    assert previous.stats()["dropped_by_level"] == {}
    assert not previous.listener._thread.is_alive()


def test_reconfigure_listener_start_failure_keeps_old_output(make_logger, monkeypatch):
    logger, stream = make_logger()
    config = logger.get_config()
    previous = logger._queue_handler

    def fail_start(_listener):
        raise RuntimeError("controlled replacement startup failure")

    with monkeypatch.context() as patch:
        patch.setattr(queued_output.OutputListener, "start", fail_start)
        with pytest.raises(RuntimeError, match="replacement startup failure"):
            logger.reconfigure({**config, "level": "ERROR", "format": "new:%(message)s"})
    assert logger.get_config() == config
    assert logger._queue_handler is previous
    assert not previous.stopping
    logger.info("still working")
    assert logger.flush()
    assert stream.getvalue() == "still working\n"


def test_stale_handler_does_not_retain_retired_queues(make_logger):
    import gc
    import weakref

    first, stream = make_logger()
    stale = first._queue_handler
    current, _ = make_logger(stream)
    retired = []
    for _ in range(3):
        retired.append(weakref.ref(current._queue_handler))
        current.reconfigure(current.get_config())
    gc.collect()
    assert all(reference() is None for reference in retired)
    stale.handle(logging.makeLogRecord({"msg": "latest queue", "levelno": logging.INFO}))
    assert current.flush()
    assert stream.getvalue() == "latest queue\n"


def test_reconfigure_slow_retired_sink_close_bounds_consumer_count(make_logger, monkeypatch):
    logger, stream = make_logger()
    previous = logger._queue_handler
    sink = previous.listener.handlers[0]
    entered = threading.Event()
    release = threading.Event()
    close = sink.close
    stop = previous.stop

    def slow_close():
        entered.set()
        assert release.wait(5)
        close()

    monkeypatch.setattr(sink, "close", slow_close)
    monkeypatch.setattr(previous, "stop", lambda: stop(0))
    try:
        logger.reconfigure({**logger.get_config(), "format": "new:%(message)s"})
        assert entered.wait(2)
        current = logger._queue_handler
        for _ in range(2):
            with pytest.raises(BaseError, match="still blocked"):
                logger.reconfigure(logger.get_config())
            assert logger._queue_handler is current
            assert not current.stopping
        logger.info("current still working")
        assert logger.flush()
        assert stream.getvalue() == "new:current still working\n"
    finally:
        release.set()
        previous.listener._thread.join(3)
    assert not previous.listener._thread.is_alive()
    logger.reconfigure({**logger.get_config(), "format": "recovered:%(message)s"})
    assert not current.listener._thread.is_alive()
    logger.info("retry")
    assert logger.flush()
    assert stream.getvalue().endswith("recovered:retry\n")


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


@pytest.mark.parametrize("invalid", [{"format": "invalid"}, {"log_file": "/etc/passwd"}, {"level": "BASIC_FORMAT"}])
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


def test_reconfigure_from_consumer_rejects_without_sealing_output(make_logger, monkeypatch):
    logger, stream = make_logger()
    output = logger._queue_handler
    sink = output.listener.handlers[0]
    emit = sink.emit
    rejected = threading.Event()

    def reentrant_emit(record):
        with pytest.raises(BaseError, match="still blocked"):
            logger.reconfigure(logger.get_config())
        rejected.set()
        emit(record)

    monkeypatch.setattr(sink, "emit", reentrant_emit)
    logger.info("before")
    assert logger.flush()
    assert rejected.is_set()
    assert not output.stopping
    logger.info("after")
    assert logger.flush()
    assert stream.getvalue() == "before\nafter\n"


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


def test_console_setup_failure_closes_new_file_and_preserves_active_logger(make_logger, monkeypatch):
    from openjiuwen.core.common.logging.default import default_impl

    logger, stream = make_logger()
    previous_config = logger.get_config()
    previous_output = logger._queue_handler
    created = []
    file_handler = default_impl.SafeRotatingFileHandler

    def track_file(*args, **kwargs):
        handler = file_handler(*args, **kwargs)
        created.append(handler)
        return handler

    def fail_console(*args, **kwargs):
        raise OSError("console unavailable")

    monkeypatch.setattr(default_impl, "SafeRotatingFileHandler", track_file)
    monkeypatch.setattr(default_impl, "ConsoleHandler", fail_console)
    with pytest.raises(OSError, match="console unavailable"):
        logger.reconfigure({**previous_config, "output": ["file", "console"]})
    assert len(created) == 1
    assert created[0].stream is None
    assert logger.get_config() == previous_config
    assert logger._queue_handler is previous_output
    assert not previous_output.stopping
    logger.info("original output survives")
    assert logger.flush()
    assert stream.getvalue() == "original output survives\n"


@pytest.mark.parametrize("phase", ["start", "stop"])
def test_one_sink_close_failure_does_not_leak_other_sinks(tmp_path, monkeypatch, phase):
    first = logging.FileHandler(tmp_path / "first.log")
    second = logging.FileHandler(tmp_path / "second.log")
    original_close = first.close

    def fail_close():
        original_close()
        raise OSError("controlled close failure")

    monkeypatch.setattr(first, "close", fail_close)
    if phase == "start":

        def fail_start(_listener):
            raise RuntimeError("controlled start failure")

        monkeypatch.setattr(queued_output.OutputListener, "start", fail_start)
        with pytest.raises(RuntimeError, match="controlled start failure"):
            queued_output.QueuedOutput([first, second])
    else:
        output = queued_output.QueuedOutput([first, second])
        output.close()
        assert not output.listener._thread.is_alive()
        assert output.stats()["output_failures"] == 1
    assert first.stream is None
    assert second.stream is None


def test_consumer_flush_never_waits_for_its_own_record(make_logger, monkeypatch):
    logger, _ = make_logger()
    output = logger._queue_handler
    results = []
    original_wait = output.condition.wait_for

    def reject_consumer_wait(predicate, timeout):
        assert threading.current_thread() is not output.listener._thread
        return original_wait(predicate, timeout)

    def emit(_record):
        results.append(logger.flush())

    monkeypatch.setattr(output.condition, "wait_for", reject_consumer_wait)
    monkeypatch.setattr(output.listener.handlers[0], "emit", emit)
    logger.info("reentrant flush")
    assert logger.flush()
    assert results == [False]
    assert output.stats()["output_failures"] == 0


def test_close_before_first_dequeue_does_not_leak_consumer(monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    original_monitor = queued_output.OutputListener._monitor

    def delayed_monitor(listener):
        entered.set()
        release.wait(3)
        original_monitor(listener)

    monkeypatch.setattr(queued_output.OutputListener, "_monitor", delayed_monitor)
    output = queued_output.QueuedOutput([])
    try:
        assert entered.wait(2)
        # Stop removes the sentinel while the consumer has not begun dequeuing.
        assert output.stop(timeout=0) is False
        assert output.queue.empty()
        assert output.stats()["discarded_on_close"] == 0
    finally:
        release.set()
        output.close()
        output.listener._thread.join(3)
    assert not output.listener._thread.is_alive()
    assert output.queue.unfinished_tasks == 0
    assert output.stats()["dropped_by_level"] == {}


def test_listener_respects_each_output_level():
    all_records: list[logging.LogRecord] = []
    errors: list[logging.LogRecord] = []
    all_sink = RecordHandler(all_records)
    error_sink = RecordHandler(errors)
    error_sink.setLevel(logging.ERROR)
    output = queued_output.QueuedOutput([all_sink, error_sink])
    try:
        for level, message in [(logging.INFO, "info"), (logging.ERROR, "error")]:
            output.handle(logging.makeLogRecord({"msg": message, "levelno": level}))
        assert output.drain()
        assert [record.msg for record in all_records] == ["info", "error"]
        assert [record.msg for record in errors] == ["error"]
    finally:
        output.close()


@pytest.mark.parametrize("encoding,errors", [("utf-8", "strict"), ("ascii", "backslashreplace")])
def test_console_duplicate_preserves_encoding_and_stream_ownership(tmp_path, encoding, errors):
    import io

    path = tmp_path / "console.log"
    with io.TextIOWrapper(io.BufferedWriter(io.FileIO(path, "w")), encoding=encoding, errors=errors) as stream:
        handler = queued_output.ConsoleHandler(stream)
        duplicate = handler.stream.fileno()
        try:
            assert duplicate != stream.fileno()
            handler.handle(logging.makeLogRecord({"msg": "中文"}))
        finally:
            handler.close()
        with pytest.raises(OSError):
            os.fstat(duplicate)
        assert not stream.closed
        stream.write("original stream still works")
    expected = ("中文" + os.linesep + "original stream still works").encode(encoding, errors)
    assert path.read_bytes() == expected


def test_console_wrapper_failure_closes_duplicated_descriptor(tmp_path, monkeypatch):
    import io

    duplicates = []
    descriptors = []

    class FailedRaw(queued_output._WriteAllFileIO):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            duplicates.append(self)
            descriptors.append(self.fileno())

        def writable(self):
            raise OSError("cannot construct text wrapper")

    with io.TextIOWrapper(io.FileIO(tmp_path / "console.log", "w")) as stream:
        monkeypatch.setattr(queued_output, "_WriteAllFileIO", FailedRaw)
        with pytest.raises(OSError, match="cannot construct text wrapper"):
            queued_output.ConsoleHandler(stream)
        assert len(duplicates) == 1
        assert duplicates[0].closed
        with pytest.raises(OSError):
            os.fstat(descriptors[0])
        assert not stream.closed


def test_closed_stdout_failure_is_counted_without_recursive_stderr(tmp_path, capsys):
    import io

    stream = io.TextIOWrapper(io.FileIO(tmp_path / "closed.log", "w"))
    stream.close()
    output = queued_output.QueuedOutput([queued_output.ConsoleHandler(stream)])
    try:
        output.handle(logging.makeLogRecord({"msg": "closed output", "levelno": logging.ERROR}))
        assert output.drain()
        assert output.stats()["output_failures"] == 1
    finally:
        output.close()
    assert not output.listener._thread.is_alive()
    assert output.stats()["output_failures"] == 2  # emit and the final flush
    assert capsys.readouterr().err == ""


def test_shutdown_seals_all_outputs_and_uses_one_shared_deadline(monkeypatch):
    from types import SimpleNamespace

    streams = [GatedStream(), GatedStream()]
    outputs = [queued_output.QueuedOutput([logging.StreamHandler(stream)]) for stream in streams]
    timeouts = []
    try:
        for output, stream in zip(outputs, streams):
            output.handle(logging.makeLogRecord({"msg": "blocked", "levelno": logging.INFO}))
            assert stream.entered.wait(2)

        def observe_stop(output):
            stop = output.stop

            def bounded_stop(timeout):
                assert all(item.stopping for item in outputs)
                timeouts.append(timeout)
                return stop(timeout=0)

            return bounded_stop

        for output in outputs:
            monkeypatch.setattr(output, "stop", observe_stop(output))
        # Model elapsed shutdown time without sleeping or altering other clocks.
        clock = iter([100.0, 100.75, 102.5])
        monkeypatch.setattr(queued_output, "time", SimpleNamespace(monotonic=lambda: next(clock)))
        monkeypatch.setattr(queued_output, "DRAIN_TIMEOUT", 2.0)
        monkeypatch.setattr(queued_output, "_OUTPUTS", outputs)
        queued_output._shutdown_outputs()
        assert timeouts == [1.25, 0.0]
    finally:
        for stream in streams:
            stream.release.set()
        for output in outputs:
            output.close()
            output.listener._thread.join(3)
    assert all(not output.listener._thread.is_alive() for output in outputs)


def test_consumer_close_does_not_join_itself_or_close_active_sink(make_logger, monkeypatch):
    logger, stream = make_logger()
    output = logger._queue_handler
    sink = output.listener.handlers[0]
    emit = sink.emit
    entered = threading.Event()
    release = threading.Event()
    closed_during_emit = []

    def close_while_emitting(record):
        entered.set()
        release.wait(3)
        logger.close()
        closed_during_emit.append(sink._closed)
        emit(record)

    monkeypatch.setattr(sink, "emit", close_while_emitting)
    try:
        logger.info("active")
        assert entered.wait(2)
        logger.info("pending")
    finally:
        release.set()
        output.listener._thread.join(3)
    assert not output.listener._thread.is_alive()
    assert closed_during_emit == [False]
    assert sink._closed
    assert stream.getvalue() == "active\n"
    stats = output.stats()
    assert stats["accepted"] == stats["completed"] == 2
    assert stats["discarded_on_close"] == 1
    assert stats["dropped_by_level"] == {logging.INFO: 1}
    assert stats["output_failures"] == 0


def test_console_close_flushes_before_closing_owned_stream(tmp_path, monkeypatch):
    """The owned stream must remain usable if a base close flushes and clears it."""
    import io

    with io.TextIOWrapper(io.FileIO(tmp_path / "console-close.log", "w")) as stream:
        handler = queued_output.ConsoleHandler(stream)
        owned_stream = handler.stream

        def flushing_base_close(base_handler):
            base_handler.flush()
            logging.Handler.close(base_handler)
            base_handler.stream = None

        monkeypatch.setattr(logging.StreamHandler, "close", flushing_base_close)
        handler.close()
        handler.close()  # logging.shutdown may close a handler more than once.
        assert handler._closed  # type: ignore[attr-defined]
        assert owned_stream.closed
        assert not stream.closed


def test_normal_owned_console_close_has_no_output_failure(tmp_path, monkeypatch):
    """Exercise the actual fd-backed console sink and its listener lifecycle."""
    import io

    with io.TextIOWrapper(io.FileIO(tmp_path / "console.log", "w")) as stream:
        with monkeypatch.context() as patch:
            patch.setattr(sys, "stdout", stream)
            logger = DefaultLogger(
                f"owned_console_{tmp_path.name}",
                {"output": ["console"], "level": "DEBUG", "propagate": False, "format": "%(message)s"},
            )
            sink = logger._queue_handler.listener.handlers[0]
            assert isinstance(sink, queued_output.ConsoleHandler)
            assert sink._owns_stream
            logger.debug("before close")
            assert logger.flush()
            logger.close()
            assert sink._closed  # type: ignore[attr-defined]
            assert sink.stream.closed
            assert not stream.closed
            assert logger.get_queue_stats()["output_failures"] == 0
        assert (tmp_path / "console.log").read_text() == "before close\n"


def test_failed_reconfiguration_preserves_error_if_new_sink_close_fails(tmp_path, monkeypatch):
    """A cleanup failure must not hide the reconfiguration error or leak a sink."""
    import io

    from openjiuwen.core.common.exception.codes import StatusCode
    from openjiuwen.core.common.logging.default import default_impl

    entered = threading.Event()
    release = threading.Event()
    new_sinks: list[logging.Handler] = []
    with io.TextIOWrapper(io.FileIO(tmp_path / "stdout.log", "w")) as stream, monkeypatch.context() as patch:
        patch.setattr(sys, "stdout", stream)
        config = {"output": ["console"], "level": "INFO", "propagate": False, "log_file": str(tmp_path / "log")}
        logger = DefaultLogger(f"cleanup_error_{tmp_path.name}", config)
        output = logger._queue_handler
        original_emit = output.listener.handlers[0].emit
        wait_for = output.condition.wait_for
        console_handler = default_impl.ConsoleHandler
        file_handler = default_impl.SafeRotatingFileHandler

        def blocked_emit(record):
            entered.set()
            release.wait(3)
            original_emit(record)

        def track_console(*args, **kwargs):
            handler = console_handler(*args, **kwargs)
            close = handler.close

            def fail_close():
                close()
                raise OSError("controlled console close failure")

            patch.setattr(handler, "close", fail_close)
            new_sinks.append(handler)
            return handler

        def track_file(*args, **kwargs):
            handler = file_handler(*args, **kwargs)
            new_sinks.append(handler)
            return handler

        try:
            patch.setattr(output.listener.handlers[0], "emit", blocked_emit)
            logger.info("in flight")
            assert entered.wait(2)
            with patch.context() as failure_patch:
                failure_patch.setattr(output.condition, "wait_for", lambda predicate, timeout: wait_for(predicate, 0))
                failure_patch.setattr(default_impl, "ConsoleHandler", track_console)
                failure_patch.setattr(default_impl, "SafeRotatingFileHandler", track_file)
                with pytest.raises(BaseError) as exc_info:
                    logger.reconfigure({**config, "output": ["console", "file"]})
            assert exc_info.value.code == StatusCode.COMMON_LOG_CONFIG_INVALID.code
            assert "still blocked" in str(exc_info.value)
            assert any("controlled console close failure" in note for note in exc_info.value.__notes__)
            assert len(new_sinks) == 2
            assert all(sink._closed for sink in new_sinks)  # type: ignore[attr-defined]
            assert isinstance(new_sinks[0], default_impl.SafeRotatingFileHandler)
            assert new_sinks[0].stream is None
            assert not stream.closed
            assert logger._queue_handler is output
            assert not output.stopping
            logger.info("after rejection")
            release.set()
            assert logger.flush()
            assert output.stats()["dropped_by_level"] == {}
            assert "after rejection" in (tmp_path / "stdout.log").read_text()
        finally:
            release.set()
            logger.close()
            output.listener._thread.join(3)
        assert not output.listener._thread.is_alive()


def test_console_setup_error_survives_file_handler_close_failure(make_logger, monkeypatch):
    from openjiuwen.core.common.logging.default import default_impl

    logger, stream = make_logger()
    config = logger.get_config()
    previous_output = logger._queue_handler
    created = []
    original_file_handler = default_impl.SafeRotatingFileHandler

    def track_file(*args, **kwargs):
        handler = original_file_handler(*args, **kwargs)
        close = handler.close

        def fail_close():
            close()
            raise OSError("controlled file close failure")

        monkeypatch.setattr(handler, "close", fail_close)
        created.append(handler)
        return handler

    def fail_console(*args, **kwargs):
        raise OSError("controlled console setup failure")

    with monkeypatch.context() as patch:
        patch.setattr(default_impl, "SafeRotatingFileHandler", track_file)
        patch.setattr(default_impl, "ConsoleHandler", fail_console)
        with pytest.raises(OSError, match="controlled console setup failure") as exc_info:
            logger.reconfigure({**config, "output": ["file", "console"]})
    assert any("controlled file close failure" in note for note in exc_info.value.__notes__)
    assert len(created) == 1
    assert created[0].stream is None
    assert logger._queue_handler is previous_output
    assert not previous_output.stopping
    logger.info("still available")
    assert logger.flush()
    assert stream.getvalue() == "still available\n"


def test_queue_handler_flush_waits_for_inflight_output(make_logger, monkeypatch):
    """QueuedOutput.flush overrides Handler.flush and waits for the listener."""
    stream = GatedStream()
    logger, _ = make_logger(stream)
    output = logger._queue_handler
    logger.info("in flight")
    assert stream.entered.wait(2)
    entered_wait = threading.Event()
    finished = threading.Event()
    original_wait = output.condition.wait_for

    def observe_wait(predicate, timeout):
        assert not predicate()
        entered_wait.set()
        return original_wait(predicate, timeout)

    def flush_handler():
        output.flush()
        finished.set()

    monkeypatch.setattr(output.condition, "wait_for", observe_wait)
    worker = threading.Thread(target=flush_handler)
    worker.start()
    try:
        assert entered_wait.wait(2)
        assert not finished.is_set()
    finally:
        stream.release.set()
        worker.join(3)
        monkeypatch.setattr(output.condition, "wait_for", original_wait)
    assert finished.is_set()
    assert stream.getvalue() == "in flight\n"

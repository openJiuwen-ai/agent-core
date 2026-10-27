# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Bounded, best-effort delivery for DefaultLogger's owned output handlers.

Only built-in sinks belong here. User handlers and propagation retain stdlib
semantics. A blocked OS write cannot be interrupted safely: stop bounds the
caller's wait, discards the pending tail, and lets the daemon close its sinks
when that write returns. Never close a sink underneath an active write.
"""

import atexit
import copy
import io
import logging
import os
import queue
import threading
import time
import weakref
from collections import Counter
from logging.handlers import QueueHandler, QueueListener

# Absorb a short burst (4096 records), not an indefinitely slow destination.
# This bounds record count, not bytes: individual messages/tracebacks can be large.
QUEUE_CAPACITY = 4096
DRAIN_TIMEOUT = 2.0
_OUTPUTS: weakref.WeakSet["QueuedOutput"] = weakref.WeakSet()


class _WriteAllFileIO(io.FileIO):
    """Unbuffered fd output with BufferedWriter's write-all guarantee."""

    _write = staticmethod(os.write)

    def write(self, data):
        view = memoryview(data)
        written = 0
        while written < len(view):
            count = self._write(self.fileno(), view[written:])
            if not count:
                raise BlockingIOError("console descriptor did not accept output")
            written += count
        return written


class ConsoleHandler(logging.StreamHandler):
    """Own an unbuffered duplicate of standard stdout, never its buffer lock.

    CPython flushes sys.stdout independently of logging.shutdown. A daemon
    holding its BufferedWriter lock can abort interpreter finalization, even
    after a bounded listener stop. Custom streams without a real descriptor
    retain their write/flush contract and remain responsible for shutdown safety.
    """

    def __init__(self, stream):
        # Exact types intentionally avoid bypassing custom I/O behavior.
        # pylint: disable=unidiomatic-typecheck
        self._owns_stream = False
        # Preserve overridden write/flush, transform wrappers and Windows'
        # native _WindowsConsoleIO. Only plain file/pipe stdout is duplicated.
        plain_text = type(stream) is io.TextIOWrapper
        buffer = stream.buffer if plain_text else None
        raw_stream = buffer.raw if type(buffer) is io.BufferedWriter else buffer
        if plain_text and type(raw_stream) is io.FileIO:
            try:
                descriptor = stream.fileno()
            except (OSError, ValueError):
                pass  # In-memory capture wrappers have no descriptor to duplicate.
            else:
                raw = _WriteAllFileIO(os.dup(descriptor), "w", closefd=True)
                try:
                    stream = io.TextIOWrapper(raw, encoding=stream.encoding, errors=stream.errors, write_through=True)
                except Exception:
                    raw.close()
                    raise
                self._owns_stream = True
        super().__init__(stream)

    def close(self):
        try:
            if self._owns_stream:
                self.stream.close()
        finally:
            super().close()


class OutputListener(QueueListener):
    """Single consumer with bounded stop and failure isolation between sinks."""

    queue: queue.Queue
    _sentinel = None

    def __init__(self, owner: "QueuedOutput", handlers: list[logging.Handler]):
        super().__init__(owner.queue, *handlers, respect_handler_level=True)
        self.owner = owner

    def handle(self, record):
        for handler in self.handlers:
            if record.levelno >= handler.level:
                try:
                    # Formatters may cache exc_text or mutate records. Isolate sinks.
                    handler.handle(copy.copy(record))
                except Exception:  # noqa: BLE001 - a failed sink must not kill the consumer
                    self.owner.record_failure(record)

    def _monitor(self):
        try:
            while True:
                try:
                    record = self.queue.get(timeout=0.05)
                except queue.Empty:
                    if self.owner.stopping:
                        break
                    continue
                if record is self._sentinel:
                    self.queue.task_done()
                    break
                try:
                    self.handle(record)
                finally:
                    with self.owner.condition:
                        self.owner.completed += 1
                        self.owner.condition.notify_all()
                    self.queue.task_done()
                del record  # Do not retain exception frames while the queue is idle.
                if self.owner.stopping and self.queue.empty():
                    break
        finally:
            for handler in self.handlers:
                try:
                    handler.flush()
                except Exception:  # noqa: BLE001 - still close every owned sink
                    self.owner.record_failure(None)
                finally:
                    try:
                        handler.close()
                    except Exception:  # noqa: BLE001 - continue closing the other sinks
                        self.owner.record_failure(None)


class QueuedOutput(QueueHandler):
    """Non-waiting enqueue; drop newest at every level when full or closed.

    Counters replace overflow logging so a stalled destination cannot generate
    recursive diagnostics. ``flush`` is an explicit, bounded drain barrier.
    """

    queue: queue.Queue

    def __init__(self, handlers: list[logging.Handler]):
        super().__init__(queue.Queue(maxsize=QUEUE_CAPACITY))
        self.condition = threading.Condition()
        self.stopping = False
        self.accepted = 0
        self.completed = 0
        self.peak = 0
        self.dropped: Counter[int] = Counter()
        self.discarded = 0
        self.failures = 0
        self.listener = OutputListener(self, handlers)
        # logging.shutdown acquires every registered handler's lock before
        # flush/close. Remove ONLY our owned sinks from that private registry;
        # otherwise it can wait forever on a blocked listener after our bounded
        # atexit drain. The listener exclusively owns their flush/close instead.
        with logging._lock:  # type: ignore[attr-defined]  # pylint: disable=protected-access
            for reference in logging._handlerList[:]:  # type: ignore[attr-defined]  # pylint: disable=protected-access
                if reference() in handlers:
                    logging._removeHandlerRef(reference)  # type: ignore[attr-defined]  # pylint: disable=protected-access
        for handler in handlers:
            # StreamHandler and RotatingFileHandler swallow emit errors via
            # handleError. Count those too, without writing to stderr recursively.
            handler.handleError = self.record_failure  # type: ignore[method-assign]
        try:
            self.listener.start()
        except Exception:
            self.stopping = True
            for handler in handlers:
                try:
                    handler.close()
                except Exception:  # noqa: BLE001 - clean up every sink on startup failure
                    self.record_failure(None)
            super().close()
            raise
        _OUTPUTS.add(self)

    def prepare(self, record):
        # Stdlib QueueHandler.prepare formats on the producer and clears
        # exc_info/exc_text. This queue is in-process, so retain the traceback
        # and defer sink formatting, without mutating records seen by siblings.
        prepared = copy.copy(record)
        prepared.msg = record.getMessage()
        prepared.args = None
        return prepared

    def enqueue(self, record):
        with self.condition:
            if not self.stopping:
                try:
                    self.queue.put_nowait(record)
                except queue.Full:
                    pass
                else:
                    self.accepted += 1
                    self.peak = max(self.peak, self.queue.qsize())
                    return
            self._count_drop(record)

    def _count_drop(self, record):
        # Keep counter cardinality bounded even for arbitrary custom log levels.
        level = record.levelno if record.levelno in (10, 20, 30, 40, 50) else logging.NOTSET
        self.dropped[level] += 1

    def record_failure(self, _record):
        """Count sink failures without invoking any logging path."""
        with self.condition:
            self.failures += 1

    def stats(self):
        """Return a consistent snapshot; completed includes shutdown discards."""
        with self.condition:
            return {
                "capacity": self.queue.maxsize,
                "queued": self.queue.qsize(),
                "peak": self.peak,
                "accepted": self.accepted,
                "completed": self.completed,
                "dropped_by_level": dict(self.dropped),
                "discarded_on_close": self.discarded,
                "output_failures": self.failures,
            }

    def drain(self, timeout=DRAIN_TIMEOUT):
        """Wait for records accepted before this call, not future producers."""
        if threading.current_thread() is self.listener._thread:  # pylint: disable=protected-access
            return False
        with self.condition:
            target = self.accepted
            # Shutdown may discard records newer than this barrier while an
            # older record is still in flight. Do not let those discards make
            # the count appear to have crossed the barrier prematurely.
            return self.condition.wait_for(
                lambda: self.completed >= (self.accepted if self.discarded else target), timeout
            )

    def flush(self):
        if not self.stopping:
            self.drain()

    def request_stop(self):
        """Seal ingress before draining; no sentinel space is required."""
        with self.condition:
            if self.stopping:
                return
            self.stopping = True
            try:
                self.queue.put_nowait(self.listener._sentinel)  # pylint: disable=protected-access
            except queue.Full:
                pass

    def stop(self, timeout=DRAIN_TIMEOUT):
        """Bound the wait, discard the tail on timeout, leave active I/O alone."""
        self.request_stop()
        thread = self.listener._thread  # pylint: disable=protected-access
        if thread is not threading.current_thread():
            thread.join(timeout)
        if thread.is_alive():
            with self.condition:
                while True:
                    try:
                        record = self.queue.get_nowait()
                    except queue.Empty:
                        break
                    if record is not self.listener._sentinel:  # pylint: disable=protected-access
                        self.discarded += 1
                        self._count_drop(record)
                        self.completed += 1
                    self.queue.task_done()
                self.condition.notify_all()
        return not thread.is_alive()

    def close(self):
        if not self.stopping:
            self.stop()
        super().close()


def _shutdown_outputs():
    """One shared deadline, even when several logger destinations are stuck."""
    deadline = time.monotonic() + DRAIN_TIMEOUT
    outputs = list(_OUTPUTS)
    for output in outputs:
        output.request_stop()
    for output in outputs:
        output.stop(max(0.0, deadline - time.monotonic()))


atexit.register(_shutdown_outputs)

"""Opt-in experiment guard; native frameworks are supplied by the worker only."""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
from pathlib import Path


class InputMonitor:
    """Tag owned events and fence input after unowned input or monitor failure.

    Logs event types and ownership only, never key codes or typed user content.
    Unowned input is conservatively contamination, not a claim of human intent.
    """

    def __init__(self, quartz, evidence_dir, *, observer=None):
        self.q = quartz
        self.token = secrets.randbits(62) + 1
        self.contaminated = threading.Event()
        self.stopped = threading.Event()
        self.ready = threading.Event()
        self.reason = ""
        self.lock = threading.Lock()
        self.thread = None
        self.last_window = None
        self.observer = observer
        self.path = Path(evidence_dir) / f"input-monitor-{os.getpid()}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, event, **data):
        """Record timing and ownership without user content."""
        with self.lock:
            with self.path.open("a") as output:
                output.write(json.dumps(dict(event=event, at=time.time(), monotonic=time.monotonic(), **data)) + "\n")

    def external(self, reason, **data):
        """Permanently fence this executor on the first contamination signal."""
        if not self.contaminated.is_set():
            self.reason = reason
            self.contaminated.set()
            self.record("contaminated", reason=reason, **data)

    def classify(self, event_type, tag, source_pid):
        """Pure ownership decision shared by the callback and isolated tests."""
        if tag != self.token:
            self.external("unowned keyboard/mouse event", event_type=event_type, source_pid=source_pid)

    def check(self):
        """Refuse new input as soon as contamination is observed."""
        if self.contaminated.is_set():
            raise RuntimeError("CONTAMINATED: " + self.reason)

    def window(self, **snapshot):
        """Record changes to foreground PID and configured window geometry."""
        if snapshot != self.last_window:
            self.record("window", **snapshot)
            self.last_window = snapshot

    def post(self, event):
        """Mark an owned event before posting; callers fence before each pair."""
        self.q.CGEventSetIntegerValueField(event, self.q.kCGEventSourceUserData, self.token)
        self.q.CGEventPost(self.q.kCGHIDEventTap, event)

    def start(self):
        """Install a listen-only global tap; unavailable monitoring fails closed."""

        def run():
            q = self.q
            tap = source = None
            try:
                types = [
                    q.kCGEventKeyDown,
                    q.kCGEventKeyUp,
                    q.kCGEventFlagsChanged,
                    q.kCGEventLeftMouseDown,
                    q.kCGEventLeftMouseUp,
                    q.kCGEventRightMouseDown,
                    q.kCGEventRightMouseUp,
                    q.kCGEventOtherMouseDown,
                    q.kCGEventOtherMouseUp,
                    q.kCGEventMouseMoved,
                    q.kCGEventLeftMouseDragged,
                    q.kCGEventRightMouseDragged,
                    q.kCGEventOtherMouseDragged,
                    q.kCGEventScrollWheel,
                ]

                def callback(_proxy, event_type, event, _refcon):
                    try:
                        if event_type not in types:
                            self.external("input monitor disabled", event_type=int(event_type))
                        else:
                            self.classify(
                                int(event_type),
                                q.CGEventGetIntegerValueField(event, q.kCGEventSourceUserData),
                                q.CGEventGetIntegerValueField(event, q.kCGEventSourceUnixProcessID),
                            )
                    except Exception:  # callback errors must fence, not silently lose observation
                        self.external("input monitor callback failed")
                    return event

                tap = q.CGEventTapCreate(
                    q.kCGSessionEventTap,
                    q.kCGHeadInsertEventTap,
                    q.kCGEventTapOptionListenOnly,
                    sum(1 << int(kind) for kind in types),
                    callback,
                    None,
                )
                if tap is None:
                    raise RuntimeError("listen-only input monitoring unavailable")
                source = q.CFMachPortCreateRunLoopSource(None, tap, 0)
                q.CFRunLoopAddSource(q.CFRunLoopGetCurrent(), source, q.kCFRunLoopCommonModes)
                q.CGEventTapEnable(tap, True)
                self.record("monitor_started", worker_pid=os.getpid())
                self.ready.set()
                while not self.stopped.is_set():
                    q.CFRunLoopRunInMode(q.kCFRunLoopDefaultMode, 0.05, False)
                    if self.observer:
                        self.window(**self.observer())
            except Exception as exc:
                self.external("input monitor unavailable: " + type(exc).__name__)
                self.ready.set()
            finally:
                if tap is not None:
                    q.CGEventTapEnable(tap, False)
                if source is not None:
                    q.CFRunLoopRemoveSource(q.CFRunLoopGetCurrent(), source, q.kCFRunLoopCommonModes)

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()
        if not self.ready.wait(2):
            self.external("input monitor startup timeout")
        self.check()

    def close(self):
        """Stop monitoring owned by this worker."""
        self.stopped.set()
        if self.thread:
            self.thread.join(timeout=1)

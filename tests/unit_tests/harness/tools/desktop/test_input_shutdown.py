"""Real worker signal/teardown path, with fake event posting and no GUI/framework access."""

import asyncio
import json
import sys
from pathlib import Path

import pytest

from openjiuwen.harness.tools.desktop import worker
from openjiuwen.harness.tools.desktop.process import stop_process

HEADLESS_WORKER = r"""
import json
import sys
import time
import threading
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import worker
from macos_input import InputEvents

events = Path(sys.argv[2])
blocked = sys.argv[3] in {"blocked-focus", "blocked-held-key"}
contamination = sys.argv[3] == "contamination"

def record(value):
    with events.open("a") as stream:
        stream.write(json.dumps(value) + "\n")

class Quartz:
    kCGEventLeftMouseDown = "down"
    kCGEventLeftMouseUp = "up"
    kCGEventLeftMouseDragged = "drag"
    kCGMouseButtonLeft = 0
    kCGMouseEventClickState = "count"
    kCGEventFlagMaskControl = 2

    def CGEventCreateKeyboardEvent(self, source, key, down):
        return dict(kind="key-down" if down else "key-up", key=key)

    def CGEventCreateMouseEvent(self, source, kind, point, button):
        return dict(kind=kind, point=point, button=button)

    def CGEventSetIntegerValueField(self, event, name, value):
        event[name] = value

    def CGEventSetFlags(self, event, flags):
        event["flags"] = flags

class Monitor:
    contaminated = threading.Event()
    reason = "external input during drag"

    def close(self):
        record({"kind": "monitor-closed"})

class FakeDesktop:
    monitor = None

    def __init__(self, settings):
        self.pressed = False
        self.monitor = Monitor() if contamination else None
        self.input = InputEvents(Quartz(), self.post, self.focus, lambda _: True, worker.STOPPING)

    def post(self, event):
        record(event)
        if event["kind"] == "down" or (event["kind"] == "key-down" and event["key"] == 124):
            self.pressed = True
            if contamination:
                self.monitor.contaminated.set()

    def focus(self):
        if blocked and self.pressed:
            record({"kind": "blocked-focus"})
            time.sleep(5)  # The watchdog must release without waiting on this boundary.
        if worker.STOPPING.is_set():
            raise RuntimeError("stopping")

    def act(self, action, observation):
        if action.kind == "hold_key":
            with self.input.modified(action.keys[:-1]) as flags:
                self.input.key(124, flags, duration=action.duration)
        elif action.kind == "hold_mouse":
            self.input.click((action.x, action.y), modifiers=action.modifiers, duration=action.duration)
        else:
            self.input.drag([tuple(point) for point in action.path], action.button, action.duration, action.modifiers)

    def release_inputs(self):
        self.input.release()

worker.NativeDesktop = FakeDesktop
worker.main()
"""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode",
    ["dragging", "blocked-focus", "contamination", "modified-drag", "held-key", "held-mouse", "blocked-held-key"],
)
async def test_worker_shutdown_releases_all_input_before_process_is_reaped(tmp_path, mode):
    script = tmp_path / "headless_worker.py"
    script.write_text(HEADLESS_WORKER)
    events_path = tmp_path / "events.jsonl"
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(script),
        str(Path(worker.__file__).parent),
        str(events_path),
        mode,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    try:
        process.stdin.write(b'{"method":"start","settings":{}}\n')
        await process.stdin.drain()
        ready = await asyncio.wait_for(process.stdout.readline(), 5)
        assert json.loads(ready)["ok"] is True
        action = {"kind": "drag", "path": [[0, 0], [500, 0]], "duration": 2}
        if mode == "modified-drag":
            action["modifiers"] = ["ctrl"]
        elif mode in {"held-key", "blocked-held-key"}:
            action = {"kind": "hold_key", "keys": ["ctrl", "right"], "duration": 2}
        elif mode == "held-mouse":
            action = {"kind": "hold_mouse", "x": 0, "y": 0, "modifiers": ["ctrl"], "duration": 2}
        process.stdin.write(
            (
                json.dumps(
                    {
                        "method": "act",
                        "action": action,
                        "observation": {},
                    }
                )
                + "\n"
            ).encode()
        )
        await process.stdin.drain()
        expected = "blocked-focus" if mode.startswith("blocked-") else "key-down" if mode == "held-key" else "down"
        async with asyncio.timeout(3):
            while True:
                rows = [json.loads(row) for row in events_path.read_text().splitlines()] if events_path.exists() else []
                if any(row["kind"] == expected and (expected != "key-down" or row["key"] == 124) for row in rows):
                    break
                await asyncio.sleep(0.005)
        if mode == "contamination":
            response = json.loads(await asyncio.wait_for(process.stdout.readline(), 3))
            assert response["ok"] is False
            assert response["error"].startswith("CONTAMINATED: external input during drag;")
        process.stdin.close()  # The real driver fences the pipe before signalling its group.
        await asyncio.wait_for(stop_process(process), 3)
        rows = [json.loads(row) for row in events_path.read_text().splitlines()]
        if action["kind"] == "hold_key":
            input_rows = [row for row in rows if row.get("key") == 124]
            assert [row["kind"] for row in input_rows] == ["key-down", "key-up"]
        else:
            input_rows = [row for row in rows if row["kind"] in {"down", "drag", "up"}]
            assert input_rows[0]["kind"] == "down"
            assert input_rows[-1]["kind"] == "up"
            assert sum(row["kind"] == "down" for row in input_rows) == 1
            assert sum(row["kind"] == "up" for row in input_rows) == 1
        if mode in {"modified-drag", "held-key", "held-mouse", "blocked-held-key"}:
            modifiers = [row for row in rows if row.get("key") == 59]
            assert [row["kind"] for row in modifiers] == ["key-down", "key-up"]
            assert rows.index(modifiers[0]) < rows.index(input_rows[0])
            assert rows.index(modifiers[-1]) > rows.index(input_rows[-1])
        if mode == "contamination":
            assert rows[-1]["kind"] == "monitor-closed"
        assert process.returncode is not None
    finally:
        if process.stdin is not None:
            process.stdin.close()
        await stop_process(process)

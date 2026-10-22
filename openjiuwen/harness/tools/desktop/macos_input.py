"""Bounded macOS event primitives with explicit release; Quartz is injected by the worker."""

from __future__ import annotations

import math
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from time import monotonic

try:
    from .errors import InputNotStarted
except ImportError:  # Private worker execution.
    from errors import InputNotStarted  # pylint: disable=import-error

MODIFIER_CODES = {"cmd": 55, "ctrl": 59, "alt": 58, "shift": 56}
MODIFIER_FLAGS = {"cmd": "Command", "ctrl": "Control", "alt": "Alternate", "shift": "Shift"}

# Virtual key positions from the macOS SDK HIToolbox/Events.h. Letters and
# punctuation name the US ANSI positions, not characters in the active layout.
KEY_CODES = {
    "a": 0,
    "s": 1,
    "d": 2,
    "f": 3,
    "h": 4,
    "g": 5,
    "z": 6,
    "x": 7,
    "c": 8,
    "v": 9,
    "b": 11,
    "q": 12,
    "w": 13,
    "e": 14,
    "r": 15,
    "y": 16,
    "t": 17,
    "1": 18,
    "2": 19,
    "3": 20,
    "4": 21,
    "6": 22,
    "5": 23,
    "=": 24,
    "9": 25,
    "7": 26,
    "-": 27,
    "8": 28,
    "0": 29,
    "]": 30,
    "o": 31,
    "u": 32,
    "[": 33,
    "i": 34,
    "p": 35,
    "return": 36,
    "l": 37,
    "j": 38,
    "'": 39,
    "k": 40,
    ";": 41,
    "\\": 42,
    ",": 43,
    "/": 44,
    "n": 45,
    "m": 46,
    ".": 47,
    "tab": 48,
    "space": 49,
    "`": 50,
    "backspace": 51,
    "escape": 53,
    "f17": 64,
    "f18": 79,
    "f19": 80,
    "f20": 90,
    "f5": 96,
    "f6": 97,
    "f7": 98,
    "f3": 99,
    "f8": 100,
    "f9": 101,
    "f11": 103,
    "f13": 105,
    "f16": 106,
    "f14": 107,
    "f10": 109,
    "f12": 111,
    "f15": 113,
    "home": 115,
    "pageup": 116,
    "delete": 117,
    "f4": 118,
    "end": 119,
    "f2": 120,
    "pagedown": 121,
    "f1": 122,
    "left": 123,
    "right": 124,
    "down": 125,
    "up": 126,
}


def interpolate_drag(points: list[tuple[float, float]], duration: float) -> list[tuple[float, float]]:
    """Bound path spacing to eight desktop units and at least sixty samples/second."""
    result = [points[0]]
    for start, end in zip(points, points[1:]):
        count = max(1, math.ceil(math.dist(start, end) / 8), math.ceil(duration * 60 / (len(points) - 1)))
        if len(result) + count > 512:
            raise ValueError("drag path exceeds 512 motion samples; use a shorter path")
        for index in range(1, count + 1):
            fraction = index / count
            result.append((start[0] + (end[0] - start[0]) * fraction, start[1] + (end[1] - start[1]) * fraction))
    return result


class InputEvents:
    """One serial input stream; emergency release may be invoked from a watchdog."""

    def __init__(self, quartz, post_event, check_focus, point_is_target, stopping):
        self.q, self.post = quartz, post_event
        self.check_focus, self.point_is_target = check_focus, point_is_target
        self.stopping = stopping
        self._lock = threading.RLock()
        self._release_event = None
        self._modifier_releases = []

    def check_stop(self):
        """Fence presses and movement; release deliberately bypasses this fence."""
        if self.stopping.is_set():
            raise RuntimeError("native executor is stopping")

    def release(self):
        """Release a potentially posted press, including after loss of focus or Stop.

        On post failure retain the release for the worker's cleanup/watchdog retry.
        No new down event is ever emitted by this method.
        """
        with self._lock:
            errors = []
            if self._release_event is not None:
                try:
                    self.release_primary()
                except Exception as exc:
                    errors.append(exc)
            for index in range(len(self._modifier_releases) - 1, -1, -1):
                try:
                    self.post(self._modifier_releases[index])
                except Exception as exc:
                    errors.append(exc)
                else:
                    self._modifier_releases.pop(index)
            if errors:
                raise RuntimeError("input release failed: " + "; ".join(str(error) for error in errors)) from errors[0]

    def release_primary(self) -> None:
        """Release only the main key/button, keeping gesture modifiers pressed."""
        with self._lock:
            if self._release_event is not None:
                self.post(self._release_event)
                self._release_event = None

    def keyboard_pair(self, keycode: int, flags: int = 0) -> tuple[object, object]:
        """Build both events before registering or posting a press."""
        down = self.q.CGEventCreateKeyboardEvent(None, keycode, True)
        up = self.q.CGEventCreateKeyboardEvent(None, keycode, False)
        if down is None or up is None:
            raise RuntimeError("could not create native keyboard events")
        self.q.CGEventSetFlags(down, flags)
        self.q.CGEventSetFlags(up, 0)
        return down, up

    @contextmanager
    def modified(self, modifiers: Sequence[str] = ()) -> Iterator[int]:
        """Own all modifier releases for one bounded gesture, including partial posts."""
        with self._lock:
            self.check_stop()
            if self._release_event is not None or self._modifier_releases:
                raise RuntimeError("previous native input has not been released")
        flags = 0
        try:
            for modifier in modifiers:
                self.check_focus()
                flags |= getattr(self.q, "kCGEventFlagMask" + MODIFIER_FLAGS[modifier])
                down, up = self.keyboard_pair(MODIFIER_CODES[modifier], flags)
                with self._lock:
                    self.check_stop()
                    self._modifier_releases.append(up)
                    self.post(down)
            yield flags
        finally:
            self.release()

    def press(self, event, release):
        """Record release before posting: a failed post has an unknown physical outcome."""
        with self._lock:
            self.check_stop()
            if self._release_event is not None:
                raise RuntimeError("previous native input has not been released")
            self._release_event = release
            self.post(event)

    def post_checked(self, event):
        """Post unheld movement/scroll with a final stop fence."""
        with self._lock:
            self.check_stop()
            self.post(event)

    def key(self, keycode, flags=0, text=None, duration=0):
        """Post one paired key; modifiers are event flags and are cleared on key-up."""
        down, up = self.keyboard_pair(keycode, flags)
        if text is not None:
            for event in (down, up):
                self.q.CGEventKeyboardSetUnicodeString(event, len(text.encode("utf-16-le")) // 2, text)
        try:
            self.press(down, up)
            if duration:
                self.hold(duration)
        finally:
            self.release_primary()

    def hold(self, duration: float, point: tuple[float, float] | None = None) -> None:
        """Bound focus/Stop polling independently of screenshot or model latency."""
        ticks = max(1, math.ceil(duration / 0.02))
        deadline = monotonic() + duration
        for _ in range(ticks):
            remaining = deadline - monotonic()
            if remaining <= 0:
                break
            self.pause(min(duration / ticks, remaining))
            if point is None:
                self.check_stop()
                self.check_focus()
            else:
                self.guard_point(point)
            with self._lock:
                self.check_stop()
                if self._release_event is None:
                    raise RuntimeError("held input was released before completion")

    def mouse_event(self, button, phase, point, count=1, flags=0):
        """Build the native event type, button and click count as one consistent tuple."""
        prefix = {"left": "Left", "right": "Right", "middle": "Other"}[button]
        button_name = {"left": "Left", "right": "Right", "middle": "Center"}[button]
        event = self.q.CGEventCreateMouseEvent(
            None,
            getattr(self.q, f"kCGEvent{prefix}Mouse{phase}"),
            point,
            getattr(self.q, f"kCGMouseButton{button_name}"),
        )
        if event is None:
            raise RuntimeError("could not create native mouse event")
        self.q.CGEventSetIntegerValueField(event, self.q.kCGMouseEventClickState, count)
        self.q.CGEventSetFlags(event, flags)
        return event

    def guard_point(self, point, *, before_input=False):
        """Recheck foreground, geometry and point ownership immediately before input."""
        self.check_stop()
        self.check_focus()
        if not self.point_is_target(point):
            if before_input:
                raise InputNotStarted("input point is occluded by another window; no input was sent")
            raise RuntimeError("input point is occluded by another window")
        self.check_focus()

    def pause(self, seconds):
        """Wait cooperatively so a drag or click sequence does not delay Stop."""
        if self.stopping.wait(seconds):
            self.check_stop()

    def move(self, point: tuple[float, float], modifiers: Sequence[str] = ()) -> None:
        """Move without clicking; hover effects are observed by the next capture."""
        self.guard_point(point, before_input=True)
        with self.modified(modifiers) as flags:
            self.move_event(point, flags)

    def move_event(self, point: tuple[float, float], flags: int = 0) -> None:
        """Move with already-owned modifiers and a final recipient check."""
        self.guard_point(point)
        event = self.q.CGEventCreateMouseEvent(None, self.q.kCGEventMouseMoved, point, self.q.kCGMouseButtonLeft)
        if event is None:
            raise RuntimeError("could not create native pointer event")
        self.q.CGEventSetFlags(event, flags)
        self.post_checked(event)

    def click(self, point, button="left", count=1, modifiers=(), duration=0):
        """Perform counted clicks or one bounded press with gesture-owned modifiers."""
        self.guard_point(point, before_input=True)
        with self.modified(modifiers) as flags:
            for index in range(1, count + 1):
                if index > 1:
                    self.pause(0.05)
                self.guard_point(point)
                down = self.mouse_event(button, "Down", point, index, flags)
                up = self.mouse_event(button, "Up", point, index, flags)
                self.press(down, up)
                if duration:
                    self.hold(duration, point)
                if index < count:
                    self.release_primary()

    def drag(self, points, button="left", duration=0.3, modifiers=()):
        """Validate/sample before pressing, then recheck each motion and always release."""
        samples = interpolate_drag(points, duration)
        self.guard_point(samples[0], before_input=True)
        with self.modified(modifiers) as flags:
            self.guard_point(samples[0])
            down = self.mouse_event(button, "Down", samples[0], flags=flags)
            up = self.mouse_event(button, "Up", samples[0], flags=flags)
            self.press(down, up)
            for point in samples[1:]:
                self.pause(duration / (len(samples) - 1))
                self.guard_point(point)
                motion = self.mouse_event(button, "Dragged", point, flags=flags)
                release = self.mouse_event(button, "Up", point, flags=flags)
                with self._lock:
                    self.check_stop()
                    if self._release_event is None:
                        raise RuntimeError("drag was released before completion")
                    self._release_event = release
                    self.post(motion)

    def scroll(self, point, direction, amount, modifiers=()):
        """Position the pointer inside the selected pane, then emit line-based scrolling."""
        self.guard_point(point, before_input=True)
        with self.modified(modifiers) as flags:
            self.move_event(point, flags)
            self.guard_point(point)
            delta = amount * (1 if direction in {"up", "left"} else -1)
            vertical, horizontal = (delta, 0) if direction in {"up", "down"} else (0, delta)
            event = self.q.CGEventCreateScrollWheelEvent(None, self.q.kCGScrollEventUnitLine, 2, vertical, horizontal)
            if event is None:
                raise RuntimeError("could not create native scroll event")
            self.q.CGEventSetFlags(event, flags)
            self.post_checked(event)

# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Native desktop tools for the cua agent, named and shaped like cua-driver's.

Perception is screenshot-only: ``get_window_state`` captures a window and every
pointer tool takes ``x``/``y`` in that screenshot's pixels (window-local, origin
top-left). Input is real foreground input, so a window-scoped action first
brings its window to the front and fails rather than type into another one.

Results follow the cua-driver text contract the cua rails read: a reply starting
with ``✅`` is an action that ran, one starting with ``❌`` is one that could not.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from openjiuwen.core.foundation.tool import Tool, ToolCard
from openjiuwen.core.foundation.tool.schema import ToolOutput
from openjiuwen.harness.tools.cua.backend import DesktopBackend, WindowInfo, create_desktop_backend

_OK = "✅"
_FAIL = "❌"

# cua-driver's zoom pads the requested region by 20% on every side so the
# target stays in frame when the coordinates are slightly off.
_ZOOM_PADDING = 0.2

# How long launch_app waits for the new app's first window.
_LAUNCH_WINDOW_TIMEOUT_S = 10.0
_LAUNCH_POLL_S = 0.25


class CuaToolError(Exception):
    """A desktop call that cannot be carried out; its message becomes the ❌ reply."""


@dataclass(frozen=True)
class _ZoomOrigin:
    window_id: int
    x: int
    y: int


class CuaDesktop:
    """Backend plus the per-run state the tools share (zoom origins)."""

    def __init__(self, backend_factory: Callable[[], DesktopBackend] = create_desktop_backend) -> None:
        self._backend_factory = backend_factory
        self._backend: DesktopBackend | None = None
        self._lock = threading.Lock()
        self.zoom_origins: dict[int, _ZoomOrigin] = {}

    @property
    def backend(self) -> DesktopBackend:
        """The desktop backend, built on first use."""
        with self._lock:
            if self._backend is None:
                self._backend = self._backend_factory()
            return self._backend

    def window(self, pid: int, window_id: int | None) -> WindowInfo:
        """The pid's window: ``window_id`` when given, else its frontmost on-screen one."""
        windows = [w for w in self.backend.list_windows() if w.pid == pid]
        if window_id is not None:
            for win in windows:
                if win.window_id == window_id:
                    return win
            raise CuaToolError(
                f"window_id {window_id} is not a window of pid {pid}. Enumerate with list_windows(pid={pid})."
            )
        on_screen = sorted((w for w in windows if w.is_on_screen), key=lambda w: w.z_index)
        if not on_screen:
            raise CuaToolError(f"pid {pid} has no on-screen window. Call list_windows or launch_app first.")
        return on_screen[0]

    def front(self, win: WindowInfo) -> WindowInfo:
        """Bring ``win`` to the foreground and return its (possibly moved) bounds."""
        if not self.backend.activate(win.window_id):
            raise CuaToolError(
                f"could not bring window {win.window_id} (pid {win.pid}) to the foreground, so no input "
                "was sent. Retry once; if it persists, report that the window cannot be focused."
            )
        return self.window(win.pid, win.window_id)

    def target_point(self, args: dict, x_key: str = "x", y_key: str = "y") -> tuple[int, int, WindowInfo | None]:
        """Screen point for ``args[x_key], args[y_key]``.

        With ``pid`` the point is window-local (or zoom-local with ``from_zoom``)
        and the window is brought to the front first; without it the point is
        already screen-absolute.
        """
        x, y = _number(args, x_key, required=True), _number(args, y_key, required=True)
        pid = _integer(args, "pid")
        if pid is None:
            return round(x), round(y), None
        win = self.front(self.window(pid, _integer(args, "window_id")))
        if args.get("from_zoom"):
            origin = self.zoom_origins.get(pid)
            if origin is None or origin.window_id != win.window_id:
                raise CuaToolError(f"from_zoom=true but there is no zoom of this window for pid {pid}; zoom first.")
            x, y = x + origin.x, y + origin.y
        return win.x + round(x), win.y + round(y), win


def _integer(args: dict, key: str, *, required: bool = False, default: int | None = None) -> int | None:
    value = args.get(key)
    if value is None or value == "":
        if required:
            raise CuaToolError(f"Missing required integer field {key}.")
        return default
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise CuaToolError(f"{key} must be an integer, got {value!r}.") from exc


def _number(args: dict, key: str, *, required: bool = False) -> float | None:
    value = args.get(key)
    if value is None or value == "":
        if required:
            raise CuaToolError(f"Missing required number field {key}.")
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise CuaToolError(f"{key} must be a number, got {value!r}.") from exc


def _string_list(args: dict, key: str) -> list[str]:
    value = args.get(key) or []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        raise CuaToolError(f"{key} must be an array of strings.")
    return [str(item) for item in value]


def _ok(text: str, **extra: Any) -> ToolOutput:
    return ToolOutput(success=True, data={"content": f"{_OK} {text}", **extra})


def _image_item(tool_name: str, png: bytes) -> dict:
    return {
        "type": "image",
        "source": "cua",
        "source_path": tool_name,
        "mime_type": "image/png",
        "data_url": f"data:image/png;base64,{base64.b64encode(png).decode('ascii')}",
    }


def _png_size(png: bytes) -> tuple[int, int]:
    # IHDR: width and height are the big-endian u32s at bytes 16..24.
    return int.from_bytes(png[16:20], "big"), int.from_bytes(png[20:24], "big")


def _where(win: WindowInfo | None) -> str:
    return f" on pid {win.pid} window {win.window_id}" if win is not None else ""


# ---------------------------------------------------------------------------
# Handlers. Each runs in a worker thread: pyautogui and Win32 calls block.
# ---------------------------------------------------------------------------


def _list_windows(desktop: CuaDesktop, args: dict) -> ToolOutput:
    pid = _integer(args, "pid")
    windows = [w for w in desktop.backend.list_windows() if pid is None or w.pid == pid]
    if args.get("on_screen_only"):
        windows = [w for w in windows if w.is_on_screen]
    apps = {w.pid for w in windows}
    on_screen = sum(1 for w in windows if w.is_on_screen)
    payload = json.dumps({"windows": [w.to_dict() for w in windows]}, ensure_ascii=False)
    return _ok(f"Found {len(windows)} window(s) across {len(apps)} app(s); {on_screen} on-screen.\n{payload}")


def _list_apps(desktop: CuaDesktop, args: dict) -> ToolOutput:
    foreground = desktop.backend.foreground_window_id()
    apps: dict[int, dict] = {}
    for win in desktop.backend.list_windows():
        app = apps.setdefault(win.pid, {"pid": win.pid, "name": win.app_name, "active": False, "window_count": 0})
        app["window_count"] += 1
        app["active"] = app["active"] or win.window_id == foreground
    payload = json.dumps({"apps": list(apps.values())}, ensure_ascii=False)
    return _ok(f"Found {len(apps)} running app(s) with windows.\n{payload}")


def _get_window_state(desktop: CuaDesktop, args: dict) -> ToolOutput:
    pid = _integer(args, "pid", required=True)
    win = desktop.window(pid, _integer(args, "window_id", required=True))
    if win.minimized:
        raise CuaToolError(f"window {win.window_id} is minimized; call bring_to_front(pid={pid}) first.")
    # A screen grab shows whatever is on top, so the window must be.
    win = desktop.front(win)
    png = desktop.backend.screenshot((win.x, win.y, win.width, win.height))
    width, height = _png_size(png)
    state = json.dumps(win.to_dict(), ensure_ascii=False)
    return _ok(
        f"Captured pid {pid} window {win.window_id} ({win.title!r}). Screenshot {width}x{height} attached: "
        f"x,y for click/drag/scroll/type_text are pixels in this screenshot, origin top-left.\n{state}",
        multimodal=[_image_item("get_window_state", png)],
    )


def _zoom(desktop: CuaDesktop, args: dict) -> ToolOutput:
    pid = _integer(args, "pid", required=True)
    win = desktop.front(desktop.window(pid, _integer(args, "window_id", required=True)))
    x1, y1 = _number(args, "x1", required=True), _number(args, "y1", required=True)
    x2, y2 = _number(args, "x2", required=True), _number(args, "y2", required=True)
    left, right = sorted((x1, x2))
    top, bottom = sorted((y1, y2))
    pad_x, pad_y = (right - left) * _ZOOM_PADDING, (bottom - top) * _ZOOM_PADDING
    left, top = max(0, round(left - pad_x)), max(0, round(top - pad_y))
    right, bottom = min(win.width, round(right + pad_x)), min(win.height, round(bottom + pad_y))
    if right <= left or bottom <= top:
        raise CuaToolError(f"zoom region lies outside the {win.width}x{win.height} window.")
    png = desktop.backend.screenshot((win.x + left, win.y + top, right - left, bottom - top))
    desktop.zoom_origins[pid] = _ZoomOrigin(win.window_id, left, top)
    return _ok(
        f"Zoomed window {win.window_id} region ({left},{top})-({right},{bottom}) at native resolution "
        "(20% padding added). Pass from_zoom=true with x,y read off this image.",
        multimodal=[_image_item("zoom", png)],
    )


def _get_screen_size(desktop: CuaDesktop, args: dict) -> ToolOutput:
    width, height = desktop.backend.screen_size()
    return _ok(f"Main display is {width}x{height} physical pixels.")


def _get_cursor_position(desktop: CuaDesktop, args: dict) -> ToolOutput:
    x, y = desktop.backend.cursor_position()
    return _ok(f"Cursor is at screen ({x}, {y}).")


def _click_handler(default_button: str, default_count: int, label: str) -> Callable[[CuaDesktop, dict], ToolOutput]:
    def _handler(desktop: CuaDesktop, args: dict) -> ToolOutput:
        button = str(args.get("button") or default_button)
        if button not in ("left", "right", "middle"):
            raise CuaToolError(f"button must be left, right or middle, got {button!r}.")
        count = _integer(args, "count", default=default_count)
        if count not in (1, 2, 3):
            raise CuaToolError(f"count must be 1, 2 or 3, got {count!r}.")
        sx, sy, win = desktop.target_point(args)
        desktop.backend.click(sx, sy, button, count)  # type: ignore[arg-type]
        return _ok(f"Sent {label} at screen ({sx},{sy}){_where(win)}.")

    return _handler


def _move_cursor(desktop: CuaDesktop, args: dict) -> ToolOutput:
    sx, sy, win = desktop.target_point(args)
    desktop.backend.move_cursor(sx, sy)
    return _ok(f"Moved cursor to screen ({sx},{sy}){_where(win)}.")


def _drag(desktop: CuaDesktop, args: dict) -> ToolOutput:
    button = str(args.get("button") or "left")
    if button not in ("left", "right", "middle"):
        raise CuaToolError(f"button must be left, right or middle, got {button!r}.")
    duration_ms = _integer(args, "duration_ms", default=500)
    fx, fy, win = desktop.target_point(args, "from_x", "from_y")
    # The window is already in front; resolve the end point without refocusing.
    tx, ty = _number(args, "to_x", required=True), _number(args, "to_y", required=True)
    if win is not None:
        if args.get("from_zoom"):
            origin = desktop.zoom_origins[win.pid]
            tx, ty = tx + origin.x, ty + origin.y
        tx, ty = win.x + tx, win.y + ty
    end = (round(tx), round(ty))
    desktop.backend.drag((fx, fy), end, button, max(duration_ms, 0) / 1000)  # type: ignore[arg-type]
    return _ok(f"Sent drag from screen ({fx},{fy}) → {end} in {duration_ms}ms{_where(win)}.")


def _scroll(desktop: CuaDesktop, args: dict) -> ToolOutput:
    direction = str(args.get("direction") or "")
    if direction not in ("up", "down", "left", "right"):
        raise CuaToolError(f"direction must be up, down, left or right, got {direction!r}.")
    amount = _integer(args, "amount", default=3)
    if args.get("x") is not None and args.get("y") is not None:
        sx, sy, win = desktop.target_point(args)
    else:
        pid = _integer(args, "pid")
        win = desktop.front(desktop.window(pid, _integer(args, "window_id"))) if pid is not None else None
        # Over the window's centre: the wheel scrolls what is under the pointer.
        sx, sy = (win.x + win.width // 2, win.y + win.height // 2) if win is not None else (None, None)
    desktop.backend.scroll(sx, sy, direction, amount)  # type: ignore[arg-type]
    at = f" at screen ({sx},{sy})" if sx is not None else ""
    return _ok(f"Scrolled {direction} {amount} tick(s){at}{_where(win)}.")


def _focus_for_keys(desktop: CuaDesktop, args: dict) -> WindowInfo | None:
    """Front the target window, clicking x,y first when given (the px focus form)."""
    if args.get("x") is not None and args.get("y") is not None:
        sx, sy, win = desktop.target_point(args)
        desktop.backend.click(sx, sy, "left", 1)
        return win
    pid = _integer(args, "pid")
    if pid is None:
        return None
    return desktop.front(desktop.window(pid, _integer(args, "window_id")))


def _type_text(desktop: CuaDesktop, args: dict) -> ToolOutput:
    text = args.get("text")
    if not isinstance(text, str) or text == "":
        raise CuaToolError("Missing required string field text.")
    win = _focus_for_keys(desktop, args)
    desktop.backend.type_text(text)
    return _ok(f"Typed {len(text)} char(s){_where(win)}.")


def _press_key(desktop: CuaDesktop, args: dict) -> ToolOutput:
    key = str(args.get("key") or "").strip()
    if not key:
        raise CuaToolError("Missing required string field key.")
    modifiers = _string_list(args, "modifiers")
    win = _focus_for_keys(desktop, args)
    desktop.backend.press_key(key, modifiers)
    return _ok(f"Sent {'+'.join([*modifiers, key])}{_where(win)}.")


def _hotkey(desktop: CuaDesktop, args: dict) -> ToolOutput:
    keys = _string_list(args, "keys")
    if len(keys) < 2:
        raise CuaToolError('keys must hold modifier(s) and one key, e.g. ["ctrl", "c"].')
    win = _focus_for_keys(desktop, args)
    desktop.backend.hotkey(keys)
    return _ok(f"Pressed {'+'.join(keys)}{_where(win)}.")


def _launch_argv(args: dict) -> tuple[list[str], str]:
    extra = _string_list(args, "additional_arguments")
    path = args.get("path")
    if path:
        return [str(path), *extra], str(path)
    aumid = args.get("aumid") or (args.get("bundle_id") if "!" in str(args.get("bundle_id") or "") else None)
    if aumid:
        if sys.platform != "win32":
            raise CuaToolError("aumid / bundle_id launches are Windows-only; pass name or path.")
        return ["explorer.exe", f"shell:AppsFolder\\{aumid}"], str(aumid)
    name = args.get("name") or args.get("bundle_id")
    if not name:
        raise CuaToolError("Provide name, path or aumid.")
    resolved = shutil.which(str(name))
    if resolved is not None:
        return [resolved, *extra], str(name)
    if sys.platform == "win32" and ":" in str(name):
        # A URI such as ms-settings: has no executable; the shell resolves it
        # the way the Run dialog does.
        return ["explorer.exe", str(name)], str(name)
    raise CuaToolError(f"{name!r} is not an executable on PATH; pass its full path, or an aumid on Windows.")


def _launch_app(desktop: CuaDesktop, args: dict) -> ToolOutput:
    argv, label = _launch_argv(args)
    backend = desktop.backend
    before = {w.window_id for w in backend.list_windows()}
    pid = backend.launch(argv)
    # The launched process is often a stub (Win11 notepad/calc, explorer for an
    # AUMID) whose real window belongs to another pid, so watch for any new window.
    deadline = time.monotonic() + _LAUNCH_WINDOW_TIMEOUT_S
    new_windows: list[WindowInfo] = []
    while time.monotonic() < deadline:
        new_windows = [w for w in backend.list_windows() if w.window_id not in before]
        if new_windows:
            break
        time.sleep(_LAUNCH_POLL_S)
    if not new_windows:
        return _ok(
            f"Launched {label} (pid {pid}) but no new window appeared within "
            f"{_LAUNCH_WINDOW_TIMEOUT_S:.0f}s; call list_windows to look for it."
        )
    window_pid = new_windows[0].pid
    payload = json.dumps({"windows": [w.to_dict() for w in new_windows]}, ensure_ascii=False)
    return _ok(f"Launched {label} (pid {window_pid}).\n{payload}")


def _bring_to_front(desktop: CuaDesktop, args: dict) -> ToolOutput:
    pid = _integer(args, "pid", required=True)
    previous = desktop.backend.foreground_window_id()
    win = desktop.front(desktop.window(pid, _integer(args, "window_id")))
    return _ok(f"bring_to_front: pid {pid} window {win.window_id} is now foreground (was {previous}).")


def _kill_app(desktop: CuaDesktop, args: dict) -> ToolOutput:
    pid = _integer(args, "pid", required=True)
    if pid == os.getpid():
        raise CuaToolError("refusing to kill the agent's own process.")
    desktop.backend.kill(pid)
    return _ok(f"Killed pid {pid}.")


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

_PID = {"type": "integer", "description": "Target process ID (from list_windows / launch_app)."}
_WINDOW_ID = {
    "type": "integer",
    "description": "Target window id from list_windows. Optional: defaults to the pid's frontmost on-screen window.",
}
_X = {
    "type": "number",
    "description": "X in window-local screenshot pixels of the latest get_window_state of this window. "
    "Without pid: screen-absolute X.",
}
_Y = {"type": "number", "description": "Y in window-local screenshot pixels (see x)."}
_FROM_ZOOM = {
    "type": "boolean",
    "description": "When true, x and y are pixels in the last zoom image for this pid.",
}
_BUTTON = {"type": "string", "enum": ["left", "right", "middle"], "description": 'Mouse button. Default "left".'}


def _object(properties: dict, required: tuple[str, ...] = ()) -> dict:
    return {"type": "object", "properties": properties, "required": list(required)}


_POINT_PROPS = {"pid": _PID, "window_id": _WINDOW_ID, "x": _X, "y": _Y, "from_zoom": _FROM_ZOOM}
_FOCUS_PROPS = {
    "pid": _PID,
    "window_id": _WINDOW_ID,
    "x": {
        "type": "number",
        "description": "Optional window-local screenshot X to click first, to focus a field. Pass with y.",
    },
    "y": {"type": "number", "description": "Window-local screenshot Y of the field (see x)."},
}

_FOREGROUND_NOTE = (
    " Input is real mouse/keyboard input: with pid the window is brought to the foreground first, "
    "and the call fails instead of sending input when it cannot be."
)

_SPECS: tuple[tuple[str, str, dict, Callable[[CuaDesktop, dict], ToolOutput]], ...] = (
    (
        "list_windows",
        "List top-level windows: window_id, pid, app_name, title, bounds {x,y,width,height} in screen "
        "pixels, z_index (0 = frontmost), is_on_screen, minimized.",
        _object(
            {
                "pid": {"type": "integer", "description": "Optional pid filter."},
                "on_screen_only": {"type": "boolean", "description": "Drop minimized windows. Default false."},
            }
        ),
        _list_windows,
    ),
    (
        "list_apps",
        "List running apps that own at least one window: pid, name, active (owns the foreground "
        "window), window_count. To open an app, call launch_app directly.",
        _object({}),
        _list_apps,
    ),
    (
        "get_window_state",
        "Bring a window to the front and capture it: returns its bounds and a screenshot of exactly that "
        "window. Every x,y you pass to click/drag/scroll/type_text/press_key/hotkey for this window is a "
        "pixel in the latest such screenshot (origin top-left). Call it again after acting to verify.",
        _object(
            {"pid": _PID, "window_id": {"type": "integer", "description": "Window id from list_windows."}},
            ("pid", "window_id"),
        ),
        _get_window_state,
    ),
    (
        "zoom",
        "Capture a region of a window at native resolution to read small text or icons. x1,y1,x2,y2 are "
        "window-local screenshot pixels; 20% padding is added on every side. Afterwards pass "
        "from_zoom=true to pointer tools to use x,y read off the zoom image.",
        _object(
            {
                "pid": _PID,
                "window_id": {"type": "integer", "description": "Window id from list_windows."},
                "x1": {"type": "number", "description": "Left edge (window-local screenshot pixels)."},
                "y1": {"type": "number", "description": "Top edge."},
                "x2": {"type": "number", "description": "Right edge."},
                "y2": {"type": "number", "description": "Bottom edge."},
            },
            ("pid", "window_id", "x1", "y1", "x2", "y2"),
        ),
        _zoom,
    ),
    ("get_screen_size", "Return the main display size in physical pixels.", _object({}), _get_screen_size),
    (
        "get_cursor_position",
        "Return the mouse cursor position in screen pixels (origin top-left).",
        _object({}),
        _get_cursor_position,
    ),
    (
        "click",
        "Click at x,y." + _FOREGROUND_NOTE,
        _object(
            {
                **_POINT_PROPS,
                "button": _BUTTON,
                "count": {"type": "integer", "description": "1 (single), 2 (double), 3 (triple). Default 1."},
            },
            ("x", "y"),
        ),
        _click_handler("left", 1, "click"),
    ),
    (
        "double_click",
        "Double-click at x,y." + _FOREGROUND_NOTE,
        _object(_POINT_PROPS, ("x", "y")),
        _click_handler("left", 2, "double-click"),
    ),
    (
        "right_click",
        "Right-click at x,y." + _FOREGROUND_NOTE,
        _object(_POINT_PROPS, ("x", "y")),
        _click_handler("right", 1, "right-click"),
    ),
    (
        "move_cursor",
        "Move the mouse to x,y without clicking (hover menus, tooltips)." + _FOREGROUND_NOTE,
        _object(_POINT_PROPS, ("x", "y")),
        _move_cursor,
    ),
    (
        "drag",
        "Press at (from_x, from_y), move to (to_x, to_y) over duration_ms, release." + _FOREGROUND_NOTE,
        _object(
            {
                "pid": _PID,
                "window_id": _WINDOW_ID,
                "from_x": {"type": "number", "description": "Drag-start X in window-local screenshot pixels."},
                "from_y": {"type": "number", "description": "Drag-start Y in window-local screenshot pixels."},
                "to_x": {"type": "number", "description": "Drag-end X in window-local screenshot pixels."},
                "to_y": {"type": "number", "description": "Drag-end Y in window-local screenshot pixels."},
                "from_zoom": _FROM_ZOOM,
                "button": _BUTTON,
                "duration_ms": {"type": "integer", "description": "Duration of the drag path. Default 500."},
            },
            ("from_x", "from_y", "to_x", "to_y"),
        ),
        _drag,
    ),
    (
        "scroll",
        "Scroll with the mouse wheel at x,y (default: the window's centre)." + _FOREGROUND_NOTE,
        _object(
            {
                **_POINT_PROPS,
                "direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
                "amount": {"type": "integer", "description": "Number of wheel ticks. Default 3."},
            },
            ("direction",),
        ),
        _scroll,
    ),
    (
        "type_text",
        "Type text into the focused field of the window. Pass x,y to click a field first. Special keys "
        "(Return, Escape, arrows, Tab) go through press_key / hotkey." + _FOREGROUND_NOTE,
        _object({**_FOCUS_PROPS, "text": {"type": "string", "description": "Text to type."}}, ("text",)),
        _type_text,
    ),
    (
        "press_key",
        "Press and release one key: return, tab, escape, up/down/left/right, space, delete, backspace, "
        "home, end, pageup, pagedown, f1-f12, a letter or a digit. Optional modifiers are held while it "
        "is pressed." + _FOREGROUND_NOTE,
        _object(
            {
                **_FOCUS_PROPS,
                "key": {"type": "string", "description": "Key name."},
                "modifiers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional modifiers held during the press (ctrl/shift/alt/win).",
                },
            },
            ("key",),
        ),
        _press_key,
    ),
    (
        "hotkey",
        'Press a key combination, modifiers first, e.g. ["ctrl", "s"].' + _FOREGROUND_NOTE,
        _object(
            {
                **_FOCUS_PROPS,
                "keys": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": 'Modifier(s) then one key, e.g. ["ctrl", "c"].',
                },
            },
            ("keys",),
        ),
        _hotkey,
    ),
    (
        "launch_app",
        "Start an app and return the windows it opened (window_id, pid, title, bounds). The returned "
        "pid is the window owner's, which can differ from the launcher's. Provide path, name (an "
        "executable on PATH) or, on Windows, aumid for a packaged app.",
        _object(
            {
                "path": {"type": "string", "description": "Full path to an executable."},
                "name": {
                    "type": "string",
                    "description": "Executable name on PATH, e.g. notepad; on Windows also a URI such as ms-settings:.",
                },
                "aumid": {
                    "type": "string",
                    "description": "Windows packaged-app id, e.g. Microsoft.WindowsCalculator_8wekyb3d8bbwe!App.",
                },
                "bundle_id": {"type": "string", "description": "Alias of aumid when it contains '!', else of name."},
                "additional_arguments": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Extra command-line arguments.",
                },
            }
        ),
        _launch_app,
    ),
    (
        "bring_to_front",
        "Restore and raise a window of pid to the foreground.",
        _object({"pid": _PID, "window_id": _WINDOW_ID}, ("pid",)),
        _bring_to_front,
    ),
    (
        "kill_app",
        "Force-terminate a process by pid. Unsaved state is lost; prefer closing the window first.",
        _object({"pid": {"type": "integer", "description": "PID of the process to terminate."}}, ("pid",)),
        _kill_app,
    ),
)

CUA_DESKTOP_TOOL_NAMES: tuple[str, ...] = tuple(spec[0] for spec in _SPECS)


class CuaDesktopTool(Tool):
    """One cua desktop tool; the work runs in a worker thread."""

    def __init__(self, card: ToolCard, desktop: CuaDesktop, handler: Callable[[CuaDesktop, dict], ToolOutput]):
        super().__init__(card)
        self._desktop = desktop
        self._handler = handler

    async def invoke(self, inputs: Any, **kwargs: Any) -> ToolOutput:
        args = dict(inputs) if isinstance(inputs, dict) else {}
        try:
            return await asyncio.to_thread(self._handler, self._desktop, args)
        except CuaToolError as exc:
            return ToolOutput(success=False, error=f"{_FAIL} {exc}")
        except Exception as exc:  # noqa: BLE001 - every desktop failure becomes a ❌ reply the rails read
            return ToolOutput(success=False, error=f"{_FAIL} {self.card.name} failed: {type(exc).__name__}: {exc}")

    async def stream(self, inputs: Any, **kwargs: Any):
        yield await self.invoke(inputs, **kwargs)


def build_cua_desktop_tools(
    desktop: CuaDesktop | None = None,
    names: tuple[str, ...] | None = None,
) -> list[Tool]:
    """Build the cua desktop tools sharing one ``CuaDesktop``.

    Args:
        desktop: Shared backend holder; a fresh one per agent by default.
        names: Restrict to these tool names (default: all).
    """
    desktop = desktop or CuaDesktop()
    wanted = set(CUA_DESKTOP_TOOL_NAMES if names is None else names)
    unknown = wanted - set(CUA_DESKTOP_TOOL_NAMES)
    if unknown:
        raise ValueError(f"unknown cua desktop tools: {sorted(unknown)}")
    return [
        CuaDesktopTool(
            ToolCard(id=f"tool.cua.{name}", name=name, description=description, input_params=params), desktop, handler
        )
        for name, description, params, handler in _SPECS
        if name in wanted
    ]


__all__ = [
    "CUA_DESKTOP_TOOL_NAMES",
    "CuaDesktop",
    "CuaDesktopTool",
    "CuaToolError",
    "build_cua_desktop_tools",
]

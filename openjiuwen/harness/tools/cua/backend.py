# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Desktop backends for the cua tools: window discovery, screenshots and real input.

Every coordinate here is a screen-absolute physical pixel. Translating the
model's window-local screenshot pixels into screen pixels is the tools' job
(``desktop_tools.py``), not the backend's.

pyautogui and the platform window APIs are imported lazily, when a backend is
built, so importing this module never touches a display.
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any, Literal, Protocol, Sequence

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error, raise_error

MouseButton = Literal["left", "right", "middle"]
ScrollDirection = Literal["up", "down", "left", "right"]


@dataclass(frozen=True)
class WindowInfo:
    """One top-level window, bounds in screen-absolute physical pixels."""

    window_id: int
    pid: int
    app_name: str
    title: str
    x: int
    y: int
    width: int
    height: int
    is_on_screen: bool
    minimized: bool
    z_index: int

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready form, bounds nested under ``bounds``."""
        return {
            "window_id": self.window_id,
            "pid": self.pid,
            "app_name": self.app_name,
            "title": self.title,
            "bounds": {"x": self.x, "y": self.y, "width": self.width, "height": self.height},
            "z_index": self.z_index,
            "is_on_screen": self.is_on_screen,
            "minimized": self.minimized,
        }


class DesktopBackend(Protocol):
    """What the cua tools need from a desktop. Input is always foreground."""

    def list_windows(self) -> list[WindowInfo]:
        """Every top-level window, front to back."""

    def activate(self, window_id: int) -> bool:
        """Restore and raise the window; True when it is now the foreground window."""

    def foreground_window_id(self) -> int | None:
        """The window holding keyboard focus, or None when there is none."""

    def screenshot(self, region: tuple[int, int, int, int] | None = None) -> bytes:
        """PNG of ``(left, top, width, height)`` in screen pixels, or of the whole desktop."""

    def screen_size(self) -> tuple[int, int]:
        """Desktop ``(width, height)`` in screen pixels."""

    def cursor_position(self) -> tuple[int, int]:
        """Cursor ``(x, y)`` in screen pixels."""

    def click(self, x: int, y: int, button: MouseButton, count: int) -> None:
        """Click ``count`` times at a screen point."""

    def move_cursor(self, x: int, y: int) -> None:
        """Move the cursor to a screen point without clicking."""

    def drag(self, from_xy: tuple[int, int], to_xy: tuple[int, int], button: MouseButton, duration_s: float) -> None:
        """Press at ``from_xy``, move to ``to_xy`` over ``duration_s``, release."""

    def scroll(self, x: int | None, y: int | None, direction: ScrollDirection, ticks: int) -> None:
        """Scroll ``ticks`` notches, at a point or wherever the cursor is."""

    def type_text(self, text: str) -> None:
        """Type ``text`` into the focused control."""

    def press_key(self, key: str, modifiers: Sequence[str]) -> None:
        """Press one key while holding ``modifiers``."""

    def hotkey(self, keys: Sequence[str]) -> None:
        """Press ``keys`` together, released in reverse order."""

    def launch(self, args: Sequence[str]) -> int:
        """Start a process; returns its pid."""

    def kill(self, pid: int) -> None:
        """Kill the process and wait for it to exit."""


# cua-driver key vocabulary -> pyautogui key names, where they differ.
_KEY_ALIASES = {
    "return": "enter",
    "escape": "esc",
    "control": "ctrl",
    "windows": "win",
    "cmd": "win",
    "option": "alt",
    "pageup": "pageup",
    "pagedown": "pagedown",
}


def normalize_key(key: str) -> str:
    """Map a cua-driver key name to pyautogui's spelling."""
    lowered = str(key).strip().lower()
    return _KEY_ALIASES.get(lowered, lowered)


def _block_mouseinfo_without_tkinter() -> None:
    """Turn mouseinfo's process exit into the ImportError pyautogui tolerates.

    pyautogui imports mouseinfo, which on Linux without tkinter calls
    ``sys.exit()`` at import time and takes the whole agent process down.
    A ``None`` entry in ``sys.modules`` makes ``import mouseinfo`` raise
    ImportError instead; only pyautogui's interactive mouseInfo() is lost.
    """
    if "mouseinfo" in sys.modules:
        return
    try:
        import tkinter  # noqa: F401, PLC0415  # pylint: disable=unused-import
    except ImportError:
        sys.modules["mouseinfo"] = None


def _process_name(pid: int) -> str:
    try:
        import psutil  # noqa: PLC0415

        return psutil.Process(pid).name()
    except Exception:  # noqa: BLE001 - access denied / exited: name is cosmetic
        return ""


class PyAutoGuiBackend(DesktopBackend):
    """Input, screen and process operations shared by every platform.

    Subclasses add window discovery and activation.
    """

    # Sleep after every pyautogui call; the library default (0.1s) doubles the
    # cost of a 20-step drag for no benefit to the target app.
    _PAUSE_S = 0.05

    def __init__(self) -> None:
        _block_mouseinfo_without_tkinter()
        import pyautogui  # noqa: PLC0415 - lazy: needs a display

        # Fail-safe stays on: slamming the mouse into a screen corner aborts the
        # agent's next action, which is the user's emergency stop.
        pyautogui.PAUSE = self._PAUSE_S
        self._gui = pyautogui

    def screenshot(self, region: tuple[int, int, int, int] | None = None) -> bytes:
        from PIL import ImageGrab  # noqa: PLC0415

        bbox = None
        if region is not None:
            left, top, width, height = region
            bbox = (left, top, left + width, top + height)
        image = ImageGrab.grab(bbox=bbox, all_screens=True)
        out = io.BytesIO()
        image.save(out, format="PNG")
        return out.getvalue()

    def screen_size(self) -> tuple[int, int]:
        width, height = self._gui.size()
        return int(width), int(height)

    def cursor_position(self) -> tuple[int, int]:
        x, y = self._gui.position()
        return int(x), int(y)

    def click(self, x: int, y: int, button: MouseButton, count: int) -> None:
        self._gui.click(x=x, y=y, clicks=count, interval=0.05, button=button)

    def move_cursor(self, x: int, y: int) -> None:
        self._gui.moveTo(x, y)

    def drag(self, from_xy: tuple[int, int], to_xy: tuple[int, int], button: MouseButton, duration_s: float) -> None:
        self._gui.moveTo(*from_xy)
        self._gui.dragTo(*to_xy, duration=duration_s, button=button)

    def scroll(self, x: int | None, y: int | None, direction: ScrollDirection, ticks: int) -> None:
        if x is not None and y is not None:
            self._gui.moveTo(x, y)
        if direction in ("up", "down"):
            self._gui.scroll(ticks if direction == "up" else -ticks)
        else:
            self._gui.hscroll(ticks if direction == "right" else -ticks)

    def type_text(self, text: str) -> None:
        self._gui.write(text, interval=0.01)

    def press_key(self, key: str, modifiers: Sequence[str]) -> None:
        if modifiers:
            self._gui.hotkey(*[normalize_key(m) for m in modifiers], normalize_key(key))
        else:
            self._gui.press(normalize_key(key))

    def hotkey(self, keys: Sequence[str]) -> None:
        self._gui.hotkey(*[normalize_key(k) for k in keys])

    def launch(self, args: Sequence[str]) -> int:
        # Not a ``with`` block: the launched app must outlive this call.
        process = subprocess.Popen(list(args))  # noqa: S603  # pylint: disable=consider-using-with
        return int(process.pid)

    def kill(self, pid: int) -> None:
        import psutil  # noqa: PLC0415

        process = psutil.Process(pid)
        process.kill()
        process.wait(timeout=5)


# Win32 constants used by WindowsBackend.
_GWL_EXSTYLE = -20
_WS_EX_TOOLWINDOW = 0x00000080
_DWMWA_EXTENDED_FRAME_BOUNDS = 9
_DWMWA_CLOAKED = 14
_SW_RESTORE = 9
_INPUT_KEYBOARD = 1
_KEYEVENTF_KEYUP = 0x0002
_KEYEVENTF_UNICODE = 0x0004


class WindowsBackend(PyAutoGuiBackend):
    """Win32 window discovery and activation via ctypes."""

    def __init__(self) -> None:
        import ctypes  # noqa: PLC0415
        from ctypes import wintypes  # noqa: PLC0415

        # Physical pixels everywhere: without DPI awareness GetWindowRect,
        # the screen grab and SendInput disagree on any scaled display.
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor aware
        except (AttributeError, OSError):
            ctypes.windll.user32.SetProcessDPIAware()
        super().__init__()
        self._ctypes = ctypes
        self._wintypes = wintypes
        self._user32 = ctypes.windll.user32
        self._dwmapi = ctypes.windll.dwmapi
        self._kernel32 = ctypes.windll.kernel32

    def list_windows(self) -> list[WindowInfo]:
        ctypes, wintypes, user32 = self._ctypes, self._wintypes, self._user32
        handles: list[int] = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def _collect(hwnd, _lparam):  # noqa: ANN001 - ctypes callback
            handles.append(int(hwnd))
            return True

        user32.EnumWindows(_collect, 0)  # top-to-bottom z-order
        windows = []
        for z_index, hwnd in enumerate(handles):
            info = self._window_info(hwnd, z_index)
            if info is not None:
                windows.append(info)
        return windows

    def _window_info(self, hwnd: int, z_index: int) -> WindowInfo | None:
        ctypes, wintypes, user32 = self._ctypes, self._wintypes, self._user32
        if not user32.IsWindowVisible(hwnd):
            return None
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return None
        if user32.GetWindowLongW(hwnd, _GWL_EXSTYLE) & _WS_EX_TOOLWINDOW:
            return None
        cloaked = wintypes.DWORD(0)
        self._dwmapi.DwmGetWindowAttribute(hwnd, _DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
        if cloaked.value:
            # Suspended UWP frames and windows on other virtual desktops.
            return None
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        left, top, right, bottom = self._bounds(hwnd)
        minimized = bool(user32.IsIconic(hwnd))
        return WindowInfo(
            window_id=hwnd,
            pid=int(pid.value),
            app_name=_process_name(int(pid.value)),
            title=buffer.value,
            x=left,
            y=top,
            width=right - left,
            height=bottom - top,
            is_on_screen=not minimized,
            minimized=minimized,
            z_index=z_index,
        )

    def _bounds(self, hwnd: int) -> tuple[int, int, int, int]:
        """Visible frame bounds; GetWindowRect would include the invisible resize border."""
        ctypes, wintypes = self._ctypes, self._wintypes
        rect = wintypes.RECT()
        result = self._dwmapi.DwmGetWindowAttribute(
            hwnd, _DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(rect), ctypes.sizeof(rect)
        )
        if result != 0:
            self._user32.GetWindowRect(hwnd, ctypes.byref(rect))
        return rect.left, rect.top, rect.right, rect.bottom

    def foreground_window_id(self) -> int | None:
        hwnd = self._user32.GetForegroundWindow()
        return int(hwnd) if hwnd else None

    def activate(self, window_id: int) -> bool:
        user32, kernel32 = self._user32, self._kernel32
        if user32.IsIconic(window_id):
            user32.ShowWindow(window_id, _SW_RESTORE)
        foreground = user32.GetForegroundWindow()
        if foreground == window_id:
            return True
        # Windows refuses SetForegroundWindow from a process that does not own
        # the foreground; sharing the foreground thread's input state lifts it.
        current_thread = kernel32.GetCurrentThreadId()
        foreground_thread = user32.GetWindowThreadProcessId(foreground, None) if foreground else 0
        attached = bool(foreground_thread) and foreground_thread != current_thread
        if attached:
            user32.AttachThreadInput(current_thread, foreground_thread, True)
        try:
            user32.BringWindowToTop(window_id)
            user32.SetForegroundWindow(window_id)
        finally:
            if attached:
                user32.AttachThreadInput(current_thread, foreground_thread, False)
        # Let the window manager finish the switch before anyone screenshots it.
        time.sleep(0.15)
        return int(user32.GetForegroundWindow() or 0) == window_id

    def type_text(self, text: str) -> None:
        """Unicode-safe typing: pyautogui.write drops every non-ASCII character."""
        ctypes, wintypes = self._ctypes, self._wintypes

        class _KeybdInput(ctypes.Structure):
            _fields_ = [
                ("wVk", wintypes.WORD),
                ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t),
            ]

        class _MouseInput(ctypes.Structure):
            # Present only so the union has the size Windows expects.
            _fields_ = [
                ("dx", wintypes.LONG),
                ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t),
            ]

        class _InputUnion(ctypes.Union):
            _fields_ = [("ki", _KeybdInput), ("mi", _MouseInput)]

        class _Input(ctypes.Structure):
            _fields_ = [("type", wintypes.DWORD), ("u", _InputUnion)]

        for index, line in enumerate(text.split("\n")):
            if index:
                self._gui.press("enter")
            for unit in _utf16_units(line):
                for flags in (_KEYEVENTF_UNICODE, _KEYEVENTF_UNICODE | _KEYEVENTF_KEYUP):
                    event = _Input(type=_INPUT_KEYBOARD)
                    event.u.ki = _KeybdInput(0, unit, flags, 0, 0)
                    self._user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(event))
                time.sleep(0.005)


def _utf16_units(text: str) -> list[int]:
    """UTF-16 code units; characters outside the BMP become surrogate pairs."""
    return list(memoryview(text.encode("utf-16-le")).cast("H"))


def _x11_keysym(char: str) -> int:
    """X11 keysym for one character: Latin-1 maps directly, the rest to 0x01000000 + code point."""
    code = ord(char)
    return code if 0x20 <= code <= 0x7E or 0xA0 <= code <= 0xFF else 0x01000000 + code


def _ensure_xauthority() -> None:
    """Let python-xlib connect when there is no X auth cookie file at all.

    python-xlib (and so pyautogui) refuses to open a display when neither
    ``XAUTHORITY`` nor ``~/.Xauthority`` exists, which is the norm in minimal
    containers whose Xvfb needs no auth. With no cookie file there is no auth
    to lose, so point it at an empty file; a server that does need a cookie
    still refuses, as it would have anyway.
    """
    if os.environ.get("XAUTHORITY") or os.path.exists(os.path.expanduser("~/.Xauthority")):
        return
    os.environ["XAUTHORITY"] = os.devnull


class X11Backend(PyAutoGuiBackend):
    """X11 window discovery and activation via python-xlib and EWMH.

    Window bounds are the client window's root-relative geometry (decorations
    excluded), which is exactly what the screenshot shows and where window-local
    x,y land.
    """

    def __init__(self) -> None:
        if not os.environ.get("DISPLAY"):
            raise_error(
                StatusCode.COMPONENT_TOOL_INIT_FAILED,
                error_msg="DISPLAY is not set; the cua desktop tools need an X11 display (Wayland is unsupported)",
            )
        _ensure_xauthority()
        from Xlib import X, Xatom, display  # noqa: PLC0415 - lazy: needs a display
        from Xlib.ext import xtest  # noqa: PLC0415

        super().__init__()
        self._xconst = X
        self._xatom = Xatom
        self._xtest = xtest
        self._display = display.Display()
        self._root = self._display.screen().root

    def _atom(self, name: str) -> int:
        return self._display.intern_atom(name)

    def _property(self, window: Any, name: str, prop_type: int | None = None) -> Any:
        try:
            prop = window.get_full_property(self._atom(name), prop_type or self._xconst.AnyPropertyType)
        except Exception:  # noqa: BLE001 - window vanished between list and read
            return None
        return prop.value if prop is not None else None

    def _stacking(self) -> list[int]:
        """Managed client windows, bottom to top."""
        for name in ("_NET_CLIENT_LIST_STACKING", "_NET_CLIENT_LIST"):
            value = self._property(self._root, name, self._xatom.WINDOW)
            if value is not None:
                return [int(w) for w in value]
        # No EWMH window manager: fall back to the root's mapped children.
        return [int(child.id) for child in self._root.query_tree().children]

    def list_windows(self) -> list[WindowInfo]:
        stacking = self._stacking()
        windows = []
        for z_index, window_id in enumerate(reversed(stacking)):  # 0 = frontmost
            info = self._window_info(window_id, z_index)
            if info is not None:
                windows.append(info)
        return windows

    def _window_info(self, window_id: int, z_index: int) -> WindowInfo | None:
        window = self._display.create_resource_object("window", window_id)
        try:
            attributes = window.get_attributes()
            geometry = window.get_geometry()
            origin = window.translate_coords(self._root, 0, 0)
        except Exception:  # noqa: BLE001 - window vanished between list and read
            return None
        hidden_atom = self._atom("_NET_WM_STATE_HIDDEN")
        state = self._property(window, "_NET_WM_STATE", self._xatom.ATOM)
        minimized = bool(state is not None and hidden_atom in state)
        if attributes.map_state != self._xconst.IsViewable and not minimized:
            return None
        window_type = self._property(window, "_NET_WM_WINDOW_TYPE", self._xatom.ATOM)
        if window_type is not None and {
            self._atom("_NET_WM_WINDOW_TYPE_DOCK"),
            self._atom("_NET_WM_WINDOW_TYPE_DESKTOP"),
        } & set(window_type):
            return None
        title = self._property(window, "_NET_WM_NAME", self._atom("UTF8_STRING"))
        if title is None:
            title = self._property(window, "WM_NAME")
        if isinstance(title, bytes):
            title = title.decode("utf-8", errors="replace")
        pid_value = self._property(window, "_NET_WM_PID", self._xatom.CARDINAL)
        pid = int(pid_value[0]) if pid_value is not None and len(pid_value) else 0
        return WindowInfo(
            window_id=window_id,
            pid=pid,
            app_name=_process_name(pid) if pid else "",
            title=str(title or ""),
            # translate_coords maps the root origin into the window, so negate it.
            x=-int(origin.x),
            y=-int(origin.y),
            width=int(geometry.width),
            height=int(geometry.height),
            is_on_screen=not minimized,
            minimized=minimized,
            z_index=z_index,
        )

    def foreground_window_id(self) -> int | None:
        value = self._property(self._root, "_NET_ACTIVE_WINDOW", self._xatom.WINDOW)
        if value is not None and len(value) and int(value[0]):
            return int(value[0])
        focus = self._display.get_input_focus().focus
        return int(focus.id) if hasattr(focus, "id") else None

    def activate(self, window_id: int) -> bool:
        from Xlib.protocol import event  # noqa: PLC0415

        xconst = self._xconst
        window = self._display.create_resource_object("window", window_id)
        window.map()
        # Source indication 2 = pager: window managers honour it without the
        # focus-stealing prevention they apply to applications.
        message = event.ClientMessage(
            window=window,
            client_type=self._atom("_NET_ACTIVE_WINDOW"),
            data=(32, [2, xconst.CurrentTime, 0, 0, 0]),
        )
        self._root.send_event(message, event_mask=xconst.SubstructureRedirectMask | xconst.SubstructureNotifyMask)
        window.configure(stack_mode=xconst.Above)
        self._display.flush()
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            if self.foreground_window_id() == window_id:
                break
            time.sleep(0.05)
        else:
            # No EWMH window manager answered: focus it directly.
            try:
                window.set_input_focus(xconst.RevertToParent, xconst.CurrentTime)
                self._display.sync()
            except Exception:  # noqa: BLE001 - reported through the return value
                return False
        # Let the window manager finish the switch before anyone screenshots it.
        time.sleep(0.15)
        return self.foreground_window_id() == window_id

    def type_text(self, text: str) -> None:
        """pyautogui types ASCII; other characters go through a spare keycode.

        pyautogui.write drops every character it has no key for. The remap
        binds a free keycode to the character's keysym, presses it via XTest
        and restores the mapping -- the technique xdotool uses.
        """
        ascii_run: list[str] = []
        for char in text:
            if char == "\n" or 0x20 <= ord(char) <= 0x7E:
                ascii_run.append(char)
                continue
            if ascii_run:
                self._gui.write("".join(ascii_run), interval=0.01)
                ascii_run.clear()
            self._type_keysym(_x11_keysym(char))
        if ascii_run:
            self._gui.write("".join(ascii_run), interval=0.01)

    def _type_keysym(self, keysym: int) -> None:
        xconst, display = self._xconst, self._display
        first = display.display.info.min_keycode
        count = display.display.info.max_keycode - first + 1
        mapping = display.get_keyboard_mapping(first, count)
        spare = next((first + i for i, syms in enumerate(mapping) if not any(syms)), None)
        if spare is None:
            raise_error(
                StatusCode.COMPONENT_TOOL_EXECUTION_ERROR,
                error_msg="no spare X keycode to type a non-ASCII character",
            )
        width = len(mapping[spare - first])
        display.change_keyboard_mapping(spare, [[keysym] * width])
        display.sync()
        try:
            self._xtest.fake_input(display, xconst.KeyPress, spare)
            self._xtest.fake_input(display, xconst.KeyRelease, spare)
            display.sync()
            time.sleep(0.01)
        finally:
            display.change_keyboard_mapping(spare, [[0] * width])
            display.sync()


def create_desktop_backend() -> DesktopBackend:
    """Build the backend for this host, failing loud where none exists."""
    if sys.platform == "win32":
        return WindowsBackend()
    if sys.platform.startswith("linux"):
        return X11Backend()
    raise build_error(
        StatusCode.COMPONENT_TOOL_INIT_FAILED,
        error_msg=f"cua desktop tools have no backend for platform {sys.platform!r}",
    )

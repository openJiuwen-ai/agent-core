# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for the native cua desktop tools against a fake backend."""

from __future__ import annotations

import base64
import os
import struct
import sys
import zlib

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.foundation.tool.base import render_tool_output
from openjiuwen.harness.tools.cua import backend as backend_module
from openjiuwen.harness.tools.cua import desktop_tools as desktop_tools_module
from openjiuwen.harness.tools.cua.backend import (
    WindowInfo,
    _utf16_units,
    _x11_keysym,
    create_desktop_backend,
    normalize_key,
)
from openjiuwen.harness.tools.cua.desktop_tools import (
    CUA_DESKTOP_TOOL_NAMES,
    CuaDesktop,
    build_cua_desktop_tools,
)


def _png(width: int, height: int) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    rows = b"".join(b"\x00" + b"\x00" * width * 3 for _ in range(height))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


def _window(window_id: int, pid: int, *, x: int = 100, y: int = 50, z: int = 0, minimized: bool = False) -> WindowInfo:
    return WindowInfo(
        window_id=window_id,
        pid=pid,
        app_name="app.exe",
        title=f"win{window_id}",
        x=x,
        y=y,
        width=400,
        height=300,
        is_on_screen=not minimized,
        minimized=minimized,
        z_index=z,
    )


class _FakeBackend:
    def __init__(self, windows: list[WindowInfo], *, can_activate: bool = True) -> None:
        self.windows = list(windows)
        self.can_activate = can_activate
        self.calls: list[tuple] = []
        self.foreground: int | None = None
        self.launched_windows: list[WindowInfo] = []

    def list_windows(self) -> list[WindowInfo]:
        return list(self.windows)

    def activate(self, window_id: int) -> bool:
        self.calls.append(("activate", window_id))
        if self.can_activate:
            self.foreground = window_id
        return self.can_activate

    def foreground_window_id(self) -> int | None:
        return self.foreground

    def screenshot(self, region=None) -> bytes:
        self.calls.append(("screenshot", region))
        width, height = (region[2], region[3]) if region else (1920, 1080)
        return _png(width, height)

    def screen_size(self):
        return 1920, 1080

    def cursor_position(self):
        return 5, 6

    def click(self, x, y, button, count):
        self.calls.append(("click", x, y, button, count))

    def move_cursor(self, x, y):
        self.calls.append(("move_cursor", x, y))

    def drag(self, from_xy, to_xy, button, duration_s):
        self.calls.append(("drag", from_xy, to_xy, button, duration_s))

    def scroll(self, x, y, direction, ticks):
        self.calls.append(("scroll", x, y, direction, ticks))

    def type_text(self, text):
        self.calls.append(("type_text", text))

    def press_key(self, key, modifiers):
        self.calls.append(("press_key", key, list(modifiers)))

    def hotkey(self, keys):
        self.calls.append(("hotkey", list(keys)))

    def launch(self, args):
        self.calls.append(("launch", list(args)))
        self.windows.extend(self.launched_windows)
        return 111

    def kill(self, pid):
        self.calls.append(("kill", pid))

    def input_calls(self) -> list[tuple]:
        return [
            c
            for c in self.calls
            if c[0] in {"click", "move_cursor", "drag", "scroll", "type_text", "press_key", "hotkey"}
        ]


def _tools(backend: _FakeBackend) -> dict:
    return {tool.card.name: tool for tool in build_cua_desktop_tools(CuaDesktop(lambda: backend))}


async def _call(backend: _FakeBackend, tool_name: str, **args):
    return await _tools(backend)[tool_name].invoke(args)


def test_tools_carry_the_bare_cua_driver_names() -> None:
    """Rails and permission rules key on these names; the model sees them unprefixed."""
    tools = build_cua_desktop_tools(CuaDesktop(lambda: _FakeBackend([])))

    assert [t.card.name for t in tools] == list(CUA_DESKTOP_TOOL_NAMES)
    assert {"click", "get_window_state", "type_text", "kill_app", "launch_app"} <= set(CUA_DESKTOP_TOOL_NAMES)
    assert all(t.card.id == f"tool.cua.{t.card.name}" for t in tools)
    assert not {"get_accessibility_tree", "set_value"} & set(CUA_DESKTOP_TOOL_NAMES)


def test_names_filter_rejects_unknown_tools() -> None:
    assert [t.card.name for t in build_cua_desktop_tools(names=("click",))] == ["click"]
    with pytest.raises(ValueError, match="set_value"):
        build_cua_desktop_tools(names=("click", "set_value"))


def test_backend_is_not_built_until_a_tool_runs() -> None:
    """Building the agent must not touch a display: headless CI imports and wires these tools."""

    def _explode():
        raise AssertionError("backend built eagerly")

    build_cua_desktop_tools(CuaDesktop(_explode))


@pytest.mark.asyncio
async def test_click_translates_window_pixels_to_screen_after_fronting_the_window() -> None:
    backend = _FakeBackend([_window(7, 42, x=100, y=50)])

    result = await _call(backend, "click", pid=42, window_id=7, x=10, y=20)

    assert result.success
    assert render_tool_output(result).startswith("✅")
    assert backend.calls[0] == ("activate", 7)
    assert backend.input_calls() == [("click", 110, 70, "left", 1)]


@pytest.mark.asyncio
async def test_no_input_is_sent_when_the_window_cannot_be_fronted() -> None:
    """Foreground-only input would land in whatever window does have focus."""
    backend = _FakeBackend([_window(7, 42)], can_activate=False)

    result = await _call(backend, "type_text", pid=42, window_id=7, text="secret")

    assert not result.success
    assert render_tool_output(result).startswith("❌")
    assert backend.input_calls() == []


@pytest.mark.asyncio
async def test_window_of_another_pid_is_rejected_without_input() -> None:
    backend = _FakeBackend([_window(7, 42), _window(8, 99)])

    result = await _call(backend, "click", pid=42, window_id=8, x=1, y=1)

    assert render_tool_output(result).startswith("❌")
    assert "window_id 8 is not a window of pid 42" in result.error
    assert backend.input_calls() == []


@pytest.mark.asyncio
async def test_window_defaults_to_the_pids_frontmost_on_screen_window() -> None:
    backend = _FakeBackend(
        [_window(1, 42, x=0, z=3), _window(2, 42, x=500, z=1), _window(3, 42, x=900, z=0, minimized=True)]
    )

    await _call(backend, "click", pid=42, x=1, y=1)

    assert backend.input_calls() == [("click", 501, 51, "left", 1)]


@pytest.mark.asyncio
async def test_without_pid_coordinates_are_screen_absolute_and_nothing_is_fronted() -> None:
    backend = _FakeBackend([_window(7, 42)])

    await _call(backend, "double_click", x=300, y=400)

    assert backend.calls == [("click", 300, 400, "left", 2)]


@pytest.mark.asyncio
async def test_get_window_state_attaches_a_screenshot_of_exactly_the_window() -> None:
    """The model's x,y are pixels in this image, so it must be the window's bounds, as a
    multimodal item in the shape CuaScreenshotDownscaleRail rewrites."""
    backend = _FakeBackend([_window(7, 42, x=100, y=50)])

    result = await _call(backend, "get_window_state", pid=42, window_id=7)

    assert result.success
    assert ("screenshot", (100, 50, 400, 300)) in backend.calls
    assert backend.calls.index(("activate", 7)) < backend.calls.index(("screenshot", (100, 50, 400, 300)))
    [item] = result.data["multimodal"]
    assert item["type"] == "image" and item["mime_type"] == "image/png"
    assert item["data_url"].startswith("data:image/png;base64,")
    assert base64.b64decode(item["data_url"].split(",", 1)[1])[:4] == b"\x89PNG"
    assert result.data["content"].startswith("✅")
    assert "400x300" in result.data["content"]


@pytest.mark.asyncio
async def test_get_window_state_refuses_a_minimized_window() -> None:
    backend = _FakeBackend([_window(7, 42, minimized=True)])

    result = await _call(backend, "get_window_state", pid=42, window_id=7)

    assert "bring_to_front" in result.error
    assert not any(c[0] == "screenshot" for c in backend.calls)


@pytest.mark.asyncio
async def test_from_zoom_maps_zoom_pixels_back_through_the_padded_region() -> None:
    backend = _FakeBackend([_window(7, 42, x=100, y=50)])
    desktop = CuaDesktop(lambda: backend)
    tools = {t.card.name: t for t in build_cua_desktop_tools(desktop)}

    # 100x50 region at (200,100) with 20% padding -> origin (180, 90).
    zoomed = await tools["zoom"].invoke({"pid": 42, "window_id": 7, "x1": 200, "y1": 100, "x2": 300, "y2": 150})
    assert zoomed.success
    assert ("screenshot", (280, 140, 140, 70)) in backend.calls

    await tools["click"].invoke({"pid": 42, "window_id": 7, "x": 5, "y": 6, "from_zoom": True})

    assert backend.input_calls() == [("click", 100 + 180 + 5, 50 + 90 + 6, "left", 1)]


@pytest.mark.asyncio
async def test_from_zoom_without_a_zoom_is_rejected() -> None:
    backend = _FakeBackend([_window(7, 42)])

    result = await _call(backend, "click", pid=42, window_id=7, x=5, y=6, from_zoom=True)

    assert "zoom first" in result.error
    assert backend.input_calls() == []


@pytest.mark.asyncio
async def test_type_text_with_coordinates_clicks_the_field_first() -> None:
    backend = _FakeBackend([_window(7, 42, x=100, y=50)])

    result = await _call(backend, "type_text", pid=42, window_id=7, x=10, y=10, text="héllo 世界")

    assert result.success
    assert backend.input_calls() == [("click", 110, 60, "left", 1), ("type_text", "héllo 世界")]


@pytest.mark.asyncio
async def test_keys_go_to_the_fronted_window() -> None:
    backend = _FakeBackend([_window(7, 42)])

    await _call(backend, "press_key", pid=42, key="return", modifiers=["shift"])
    await _call(backend, "hotkey", pid=42, keys=["ctrl", "s"])

    assert [c for c in backend.calls if c[0] == "activate"] == [("activate", 7), ("activate", 7)]
    assert backend.input_calls() == [("press_key", "return", ["shift"]), ("hotkey", ["ctrl", "s"])]


@pytest.mark.asyncio
async def test_hotkey_needs_a_modifier_and_a_key() -> None:
    result = await _call(_FakeBackend([]), "hotkey", keys=["s"])

    assert render_tool_output(result).startswith("❌")


@pytest.mark.asyncio
async def test_drag_maps_both_ends_into_the_window() -> None:
    backend = _FakeBackend([_window(7, 42, x=100, y=50)])

    await _call(backend, "drag", pid=42, window_id=7, from_x=1, from_y=2, to_x=30, to_y=40, duration_ms=200)

    assert backend.input_calls() == [("drag", (101, 52), (130, 90), "left", 0.2)]


@pytest.mark.asyncio
async def test_scroll_without_a_point_scrolls_over_the_window_centre() -> None:
    backend = _FakeBackend([_window(7, 42, x=100, y=50)])

    await _call(backend, "scroll", pid=42, direction="down", amount=2)

    assert backend.input_calls() == [("scroll", 300, 200, "down", 2)]


@pytest.mark.asyncio
async def test_launch_app_reports_the_pid_that_owns_the_new_window(monkeypatch) -> None:
    """Win11 notepad/calc launch through a stub process; the agent must act on the window owner."""
    backend = _FakeBackend([_window(7, 42)])
    backend.launched_windows = [_window(9, 4321)]
    monkeypatch.setattr("shutil.which", lambda name: f"C:/bin/{name}.exe")

    result = await _call(backend, "launch_app", name="notepad")

    assert result.success
    assert ("launch", ["C:/bin/notepad.exe"]) in backend.calls
    assert "(pid 4321)" in result.data["content"]
    assert '"window_id": 9' in result.data["content"]


@pytest.mark.asyncio
async def test_launch_app_rejects_an_unknown_executable(monkeypatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: None)

    result = await _call(_FakeBackend([]), "launch_app", name="nope")

    assert render_tool_output(result).startswith("❌")


@pytest.mark.asyncio
async def test_kill_app_refuses_the_agents_own_process() -> None:
    backend = _FakeBackend([])

    result = await _call(backend, "kill_app", pid=os.getpid())

    assert render_tool_output(result).startswith("❌")
    assert not any(c[0] == "kill" for c in backend.calls)


@pytest.mark.asyncio
async def test_backend_exceptions_become_failure_replies() -> None:
    """The rails only see text: an exception must surface as a ❌ reply, not crash the turn."""

    class _Broken(_FakeBackend):
        def click(self, x, y, button, count):
            raise RuntimeError("FailSafeException: mouse in a corner")

    result = await _call(_Broken([_window(7, 42)]), "click", pid=42, x=1, y=1)

    assert not result.success
    assert render_tool_output(result) == "❌ click failed: RuntimeError: FailSafeException: mouse in a corner"


@pytest.mark.asyncio
async def test_missing_required_argument_is_a_failure_reply() -> None:
    result = await _call(_FakeBackend([_window(7, 42)]), "click", pid=42)

    assert result.error == "❌ Missing required number field x."


def test_create_desktop_backend_fails_loud_on_unsupported_platforms(monkeypatch) -> None:
    monkeypatch.setattr(backend_module.sys, "platform", "darwin")

    with pytest.raises(BaseError, match="darwin"):
        create_desktop_backend()


@pytest.mark.parametrize(("key", "expected"), [("return", "enter"), ("Escape", "esc"), ("control", "ctrl"), ("a", "a")])
def test_driver_key_names_map_to_pyautogui(key: str, expected: str) -> None:
    assert normalize_key(key) == expected


def test_utf16_units_split_astral_characters_into_surrogates() -> None:
    """SendInput KEYEVENTF_UNICODE takes UTF-16 units; an emoji is two of them."""
    assert _utf16_units("a世") == [0x61, 0x4E16]
    assert _utf16_units("😀") == [0xD83D, 0xDE00]


def test_linux_without_a_display_fails_loud(monkeypatch) -> None:
    monkeypatch.setattr(backend_module.sys, "platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)

    with pytest.raises(BaseError, match="DISPLAY"):
        create_desktop_backend()


def test_x11_keysyms_cover_latin1_and_unicode() -> None:
    """Non-ASCII text goes through a keycode remap; the keysym must follow the
    X11 rule or the remap types the wrong character."""
    assert _x11_keysym("a") == ord("a")
    assert _x11_keysym("é") == 0xE9
    assert _x11_keysym("世") == 0x01000000 + 0x4E16


def test_missing_xauthority_file_does_not_block_an_auth_less_display(monkeypatch, tmp_path) -> None:
    """python-xlib refuses to connect when ~/.Xauthority is absent, which is
    the norm in minimal containers whose Xvfb needs no auth at all."""
    monkeypatch.delenv("XAUTHORITY", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    backend_module._ensure_xauthority()

    assert os.environ["XAUTHORITY"] == os.devnull


def test_an_existing_xauthority_is_left_alone(monkeypatch, tmp_path) -> None:
    cookie = tmp_path / ".Xauthority"
    cookie.write_bytes(b"cookie")
    monkeypatch.delenv("XAUTHORITY", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    backend_module._ensure_xauthority()

    assert "XAUTHORITY" not in os.environ


def test_missing_tkinter_cannot_exit_the_agent_process(monkeypatch) -> None:
    """mouseinfo (imported by pyautogui) calls sys.exit() on Linux without
    tkinter, killing the whole agent process; it must become an ImportError,
    which pyautogui already tolerates."""
    monkeypatch.setitem(sys.modules, "tkinter", None)
    monkeypatch.delitem(sys.modules, "mouseinfo", raising=False)

    backend_module._block_mouseinfo_without_tkinter()

    with pytest.raises(ImportError):
        import mouseinfo  # noqa: F401
    monkeypatch.delitem(sys.modules, "mouseinfo", raising=False)


@pytest.mark.asyncio
async def test_launch_app_opens_uris_through_the_windows_shell(monkeypatch) -> None:
    """Windows apps such as Settings have no executable on PATH, only a URI
    (ms-settings:); the shell resolves it like the Run dialog does."""
    backend = _FakeBackend([])
    backend.launched_windows = [_window(9, 4321)]
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setattr(desktop_tools_module.sys, "platform", "win32")

    result = await _call(backend, "launch_app", name="ms-settings:")

    assert result.success
    assert ("launch", ["explorer.exe", "ms-settings:"]) in backend.calls

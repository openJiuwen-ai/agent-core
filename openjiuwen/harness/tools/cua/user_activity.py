# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Probes that report how long ago the user last touched the desktop.

The desktop tools expose no "user is active" signal, so the takeover rail
asks the OS directly. On Windows ``GetLastInputInfo`` returns the tick of the
last mouse or keyboard event in the interactive session, which is exactly the
question "is a human using this machine right now". On X11 the
MIT-SCREEN-SAVER extension reports the same idle time. Other hosts have no
probe; the rail is inert without one.
"""

from __future__ import annotations

import os
import sys
from typing import Optional, Protocol


class UserActivityProbe(Protocol):
    """Reports how long ago the user last touched the desktop."""

    def seconds_since_last_input(self) -> Optional[float]:
        """Seconds since the last user input event, or ``None`` when unknown."""


class WindowsLastInputProbe:
    """``GetLastInputInfo``-backed probe (Windows only).

    Both ``dwTime`` and ``GetTickCount`` are 32-bit millisecond counters that
    wrap together every ~49.7 days, so the difference is taken modulo 2**32.
    """

    @staticmethod
    def seconds_since_last_input() -> Optional[float]:
        """Seconds since the last mouse or keyboard event, or ``None`` if the call fails."""
        try:
            import ctypes
            from ctypes import wintypes

            class LastInputInfo(ctypes.Structure):
                """Win32 LASTINPUTINFO structure."""

                _fields_ = [("cb_size", wintypes.UINT), ("dw_time", wintypes.DWORD)]

            info = LastInputInfo(cb_size=ctypes.sizeof(LastInputInfo))
            if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
                return None
            now = ctypes.windll.kernel32.GetTickCount()
            return ((int(now) - int(info.dw_time)) & 0xFFFFFFFF) / 1000.0
        except Exception:  # noqa: BLE001 - a broken probe must never block desktop work
            return None


def _x11_screensaver_query_info(display, opcode: int, drawable):
    """Send MIT-SCREEN-SAVER QueryInfo and return its reply.

    Built on python-xlib's request framework instead of its screensaver
    helper: pyautogui's mouseinfo installs python3-xlib 0.15 into the same
    ``Xlib`` package, which overwrites python-xlib 0.33 and has no helper.
    """
    from Xlib.protocol import rq

    class _QueryInfo(rq.ReplyRequest):
        _request = rq.Struct(
            rq.Card8("opcode"),
            rq.Opcode(1),
            rq.RequestLength(),
            rq.Drawable("drawable"),
        )
        _reply = rq.Struct(
            rq.ReplyCode(),
            rq.Card8("state"),
            rq.Card16("sequence_number"),
            rq.ReplyLength(),
            rq.Window("saver_window"),
            rq.Card32("til_or_since"),
            rq.Card32("idle"),
            rq.Card32("event_mask"),
            rq.Card8("kind"),
            rq.Pad(7),
        )

    return _QueryInfo(display=display.display, opcode=opcode, drawable=drawable)


class X11IdleProbe:
    """MIT-SCREEN-SAVER-backed probe (X11 only): the server's idle time since the last input."""

    def __init__(self) -> None:
        self._display = None

    def seconds_since_last_input(self) -> Optional[float]:
        """Seconds since the last mouse or keyboard event, or ``None`` if the query fails."""
        try:
            if self._display is None:
                from Xlib import display

                self._display = display.Display()
            extension = self._display.query_extension("MIT-SCREEN-SAVER")
            if extension is None or not extension.present:
                return None
            info = _x11_screensaver_query_info(self._display, extension.major_opcode, self._display.screen().root)
            return int(info.idle) / 1000.0
        except Exception:  # noqa: BLE001 - a broken probe must never block desktop work
            return None


def build_default_user_activity_probe() -> Optional[UserActivityProbe]:
    """The platform probe, or ``None`` where user activity cannot be observed."""
    if sys.platform == "win32":
        return WindowsLastInputProbe()
    if sys.platform.startswith("linux") and os.environ.get("DISPLAY"):
        return X11IdleProbe()
    return None


__all__ = [
    "UserActivityProbe",
    "WindowsLastInputProbe",
    "X11IdleProbe",
    "build_default_user_activity_probe",
]

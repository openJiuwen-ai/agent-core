# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""The user-activity probe answers "how long since a human touched the desktop"."""

import os
import sys
from types import SimpleNamespace

import pytest

from openjiuwen.harness.tools.cua import user_activity
from openjiuwen.harness.tools.cua.user_activity import (
    WindowsLastInputProbe,
    X11IdleProbe,
    build_default_user_activity_probe,
)


@pytest.mark.skipif(sys.platform != "win32", reason="GetLastInputInfo is Windows-only")
def test_windows_probe_reports_a_non_negative_age() -> None:
    age = WindowsLastInputProbe().seconds_since_last_input()
    assert age is not None
    assert 0.0 <= age < 60 * 60 * 24 * 60


def test_default_probe_matches_the_platform() -> None:
    probe = build_default_user_activity_probe()
    if sys.platform == "win32":
        assert isinstance(probe, WindowsLastInputProbe)
    elif sys.platform.startswith("linux") and os.environ.get("DISPLAY"):
        assert isinstance(probe, X11IdleProbe)
    else:
        assert probe is None


def test_linux_gets_the_x11_probe_only_with_a_display(monkeypatch) -> None:
    # Without DISPLAY there is no X server to ask; an always-None probe would
    # make the takeover rail claim it is active while observing nothing.
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("DISPLAY", ":99")
    assert isinstance(build_default_user_activity_probe(), X11IdleProbe)
    monkeypatch.delenv("DISPLAY")
    assert build_default_user_activity_probe() is None


def test_x11_probe_reports_none_instead_of_raising_without_a_server(monkeypatch) -> None:
    monkeypatch.setenv("DISPLAY", ":12345")
    assert X11IdleProbe().seconds_since_last_input() is None


def test_x11_probe_does_not_depend_on_python_xlibs_screensaver_helper(monkeypatch) -> None:
    """pyautogui's mouseinfo drags in python3-xlib 0.15, which overwrites
    python-xlib 0.33 in the same Xlib package and has no screensaver helper.
    The probe must issue the MIT-SCREEN-SAVER request itself."""

    class _Display:
        def screen(self):
            return SimpleNamespace(root="root")

        def query_extension(self, name):
            assert name == "MIT-SCREEN-SAVER"
            return SimpleNamespace(present=True, major_opcode=144)

    sent = {}

    def _query_info(display, opcode, drawable):
        sent.update(opcode=opcode, drawable=drawable)
        return SimpleNamespace(idle=2500)

    probe = X11IdleProbe()
    probe._display = _Display()
    monkeypatch.setattr(user_activity, "_x11_screensaver_query_info", _query_info)

    assert probe.seconds_since_last_input() == 2.5
    assert sent == {"opcode": 144, "drawable": "root"}

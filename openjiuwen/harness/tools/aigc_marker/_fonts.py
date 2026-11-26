# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Resolve the HarmonyOS Sans font installed by 小艺 Work."""

import os
import platform

FONT_CANDIDATES = (
    "HarmonyOS_Sans_SC.ttf",
    "HarmonyOS_Sans_TC.ttf",
)


def _font_dirs() -> list[str]:
    if platform.system() == "Windows":
        candidates = [
            os.path.expandvars(r"%WINDIR%\Fonts"),
            os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Windows\Fonts"),
        ]
    else:
        candidates = [
            "/usr/share/fonts",
            "/usr/local/share/fonts",
            os.path.expanduser("~/.local/share/fonts"),
        ]
    return [path for path in candidates if path and os.path.isdir(path)]


def get_font_path(_font_file: str | None = None) -> str | None:
    """Return the client-installed CJK font; legacy filenames are ignored."""
    injected = os.environ.get("CLAW_AIGC_FONT_PATH", "").strip().strip('"')
    if injected and os.path.isfile(injected):
        return injected
    for directory in _font_dirs():
        for root, _dirs, files in os.walk(directory):
            by_lower = {name.lower(): name for name in files}
            for candidate in FONT_CANDIDATES:
                match = by_lower.get(candidate.lower())
                if match:
                    return os.path.join(root, match)
    return None

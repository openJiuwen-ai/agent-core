"""Opt-in macOS shared-window control using the existing host model."""

# Keep native frameworks out of headless host startup.
# pylint: disable=undefined-all-variable
__all__ = ["DirectComputerUseTool", "DesktopSettings"]


def __getattr__(name):
    if name == "DirectComputerUseTool":
        from .direct_tool import DirectComputerUseTool

        return DirectComputerUseTool
    if name == "DesktopSettings":
        from .schema import DesktopSettings

        return DesktopSettings
    raise AttributeError(name)

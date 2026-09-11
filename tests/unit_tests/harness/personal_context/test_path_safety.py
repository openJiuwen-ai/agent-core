"""Platform-independent checks for Windows extended-path conversion."""

from importlib import import_module
from pathlib import PureWindowsPath
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("module_name", ["path_safety", "context_pipeline", "file_tools", "fetch.gitcode"])
@pytest.mark.parametrize(
    ("absolute", "expected"),
    [
        ("C:\\", "\\\\?\\C:\\"),
        (r"C:\Users\mega\主题.md", r"\\?\C:\Users\mega\主题.md"),
        (r"\\server\share\主题.md", r"\\?\UNC\server\share\主题.md"),
        (r"\\?\C:\Users\mega\主题.md", r"\\?\C:\Users\mega\主题.md"),
        (r"\\?\UNC\server\share\主题.md", r"\\?\UNC\server\share\主题.md"),
    ],
)
def test_extended_path_preserves_windows_drive_and_unc(
    monkeypatch: pytest.MonkeyPatch, module_name: str, absolute: str, expected: str
) -> None:
    module = import_module(f"openjiuwen.harness.personal_context.{module_name}")
    monkeypatch.setattr(module, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(module, "Path", PureWindowsPath)
    path = SimpleNamespace(absolute=lambda: absolute)

    assert str(module._extended_path(path)) == expected

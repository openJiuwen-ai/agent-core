#!/usr/bin/env python
# coding: utf-8

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from openjiuwen.harness.tools.browser_move.drivers.managed_browser import (
    ManagedBrowserDriver,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.profiles import (
    BrowserProfile,
)


def _make_driver() -> ManagedBrowserDriver:
    return ManagedBrowserDriver(
        BrowserProfile(
            name="test-profile",
            driver_type="managed",
            cdp_url="http://127.0.0.1:9333",
            user_data_dir=".",
            debug_port=9333,
            host="127.0.0.1",
        )
    )


def test_start_reuses_existing_endpoint_without_spawning() -> None:
    driver = _make_driver()
    with patch.object(driver, "_is_endpoint_ready", return_value=True), patch(
        "openjiuwen.harness.tools.browser_move.drivers.managed_browser.subprocess.Popen"
    ) as mock_popen:
        endpoint = driver.start()

    assert endpoint == "http://127.0.0.1:9333"
    assert driver.owns_process is False
    mock_popen.assert_not_called()


def test_stop_does_not_terminate_external_browser() -> None:
    driver = _make_driver()
    process = MagicMock()
    process.poll.return_value = None
    setattr(driver, "_process", process)
    setattr(driver, "_owns_process", False)

    driver.stop()

    process.terminate.assert_not_called()
    process.kill.assert_not_called()


def test_resolve_binary_uses_explicit_chrome_path(tmp_path: Path) -> None:
    chrome = tmp_path / "chrome.exe"
    chrome.write_bytes(b"")
    driver = _make_driver()
    driver.profile.browser_binary = str(chrome)

    assert driver._resolve_binary() == str(chrome)


def test_resolve_binary_autodetects_when_path_is_empty() -> None:
    driver = _make_driver()
    driver.profile.browser_binary = ""

    with patch(
        "openjiuwen.harness.tools.browser_move.drivers.managed_browser._candidate_chrome_binaries",
        return_value=["detected-chrome"],
    ):
        assert driver._resolve_binary() == "detected-chrome"


def test_resolve_binary_does_not_fallback_for_invalid_explicit_path() -> None:
    driver = _make_driver()
    driver.profile.browser_binary = "C:/missing/chrome.exe"

    with patch(
        "openjiuwen.harness.tools.browser_move.drivers.managed_browser._candidate_chrome_binaries",
        return_value=["detected-chrome"],
    ), pytest.raises(RuntimeError, match="Configured Chrome binary not found"):
        driver._resolve_binary()

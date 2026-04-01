#!/usr/bin/env python
# coding: utf-8

from __future__ import annotations

from unittest.mock import MagicMock, patch

from openjiuwen.deepagents.tools.browser_move.drivers.managed_browser import (
    ManagedBrowserDriver,
)
from openjiuwen.deepagents.tools.browser_move.playwright_runtime.profiles import (
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
        "openjiuwen.deepagents.tools.browser_move.drivers.managed_browser.subprocess.Popen"
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

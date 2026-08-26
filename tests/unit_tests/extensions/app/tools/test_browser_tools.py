# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Unit tests for openjiuwen.extensions.app.tools.browser_tools.

These mock ``playwright.async_api`` entirely -- no real browser/network is
exercised here (that's covered by the live end-to-end run). The focus is the
retry cap and the read-only, exception-safe contract of the tool.
"""

from unittest.mock import AsyncMock, patch

import pytest

from openjiuwen.extensions.app.tools import browser_tools


class TestInspectPageOnce:
    @pytest.mark.asyncio
    async def test_missing_playwright_raises_clear_runtime_error(self):
        with patch.dict("sys.modules", {"playwright.async_api": None}):
            with pytest.raises(RuntimeError, match="playwright is not installed"):
                await browser_tools._inspect_page_once("https://example.com")

    @pytest.mark.asyncio
    async def test_navigation_failure_is_retryable(self):
        page = AsyncMock()
        page.goto.side_effect = RuntimeError("net::ERR_TIMED_OUT")
        browser = AsyncMock()
        browser.new_page = AsyncMock(return_value=page)
        playwright_ctx = AsyncMock()
        playwright_ctx.chromium.launch = AsyncMock(return_value=browser)
        async_playwright_cm = AsyncMock()
        async_playwright_cm.__aenter__ = AsyncMock(return_value=playwright_ctx)
        async_playwright_cm.__aexit__ = AsyncMock(return_value=False)

        with patch("playwright.async_api.async_playwright", return_value=async_playwright_cm):
            with pytest.raises(browser_tools._RetryableBrowserError):
                await browser_tools._inspect_page_once("https://example.com")
        browser.close.assert_awaited_once()  # closed even on failure


class TestInspectPage:
    @pytest.mark.asyncio
    async def test_returns_first_successful_attempt(self):
        expected = {"url": "https://example.com", "title": "Example", "text": "", "image_url": None, "form_fields": []}
        mock_once = AsyncMock(return_value=expected)
        with patch.object(browser_tools, "_inspect_page_once", mock_once):
            result = await browser_tools._inspect_page("https://example.com")
        assert result == expected
        mock_once.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_retries_up_to_the_cap_then_succeeds(self):
        mock_once = AsyncMock(
            side_effect=[
                browser_tools._RetryableBrowserError("timeout"),
                browser_tools._RetryableBrowserError("timeout"),
                {"url": "https://example.com", "title": "OK", "text": "", "image_url": None, "form_fields": []},
            ]
        )
        with patch.object(browser_tools, "_inspect_page_once", mock_once), patch("asyncio.sleep", AsyncMock()):
            result = await browser_tools._inspect_page("https://example.com")
        assert result["title"] == "OK"
        assert mock_once.await_count == 3

    @pytest.mark.asyncio
    async def test_raises_after_exhausting_max_attempts(self):
        mock_once = AsyncMock(side_effect=browser_tools._RetryableBrowserError("still down"))
        with patch.object(browser_tools, "_inspect_page_once", mock_once), patch("asyncio.sleep", AsyncMock()):
            with pytest.raises(browser_tools._RetryableBrowserError, match="still down"):
                await browser_tools._inspect_page("https://example.com")
        assert mock_once.await_count == browser_tools.MAX_BROWSER_ATTEMPTS


class TestBrowserInspectPageTool:
    @pytest.mark.asyncio
    async def test_returns_result_dict_on_success(self):
        expected = {
            "url": "https://example.com",
            "title": "Example",
            "text": "hi",
            "image_url": None,
            "form_fields": [],
        }
        with patch.object(browser_tools, "_inspect_page", AsyncMock(return_value=expected)):
            result = await browser_tools.browser_inspect_page.invoke({"url": "https://example.com"})
        assert result == expected

    @pytest.mark.asyncio
    async def test_never_raises_reports_error_field_instead(self):
        with patch.object(browser_tools, "_inspect_page", AsyncMock(side_effect=RuntimeError("boom"))):
            result = await browser_tools.browser_inspect_page.invoke({"url": "https://example.com"})
        assert result["error"] == "boom"
        assert result["form_fields"] == []
        assert result["image_url"] is None

    def test_exposes_no_click_fill_or_submit_capability(self):
        # Hard safety boundary: this module must never grow a tool that can
        # act on a live page (click/fill/submit/type) -- only read/inspect.
        exported_tool_names = {
            name for name, value in vars(browser_tools).items() if hasattr(value, "card") and hasattr(value, "invoke")
        }
        assert exported_tool_names == {"browser_inspect_page"}

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Playwright MCP Server Example
==============================
This server exposes browser automation tools via Playwright over the SSE transport.
The client (client_direct.py) connects using PlaywrightClient which supports both SSE
and Stdio transports.

Requirements:
    pip install fastmcp playwright
    playwright install chromium

Run:
    python server.py

The server starts on http://127.0.0.1:3003/sse

Note:
    - If you prefer to use the official Playwright MCP server (Node.js):
        npx @playwright/mcp@latest --port 3003
      Then connect with PlaywrightClient using the SSE URL.
    - This Python implementation shows a lightweight alternative using
      playwright-python and fastmcp.
"""

from contextlib import asynccontextmanager

from starlette.middleware import Middleware
from fastmcp import FastMCP

from openjiuwen.core.common.logging import logger


class PlaywrightConnectionLogger:
    """
    Pure ASGI middleware that logs when browser automation clients connect
    and disconnect via SSE.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        # Only track MCP client connections on the /sse endpoint
        if scope["type"] == "http" and scope.get("path") == "/sse":
            client = scope.get("client")
            addr = f"{client[0]}:{client[1]}" if client else "unknown"
            logger.info(f"Playwright MCP client connected via SSE from {addr}")
            try:
                await self.app(scope, receive, send)
            finally:
                logger.info(f"Playwright MCP client disconnected: {addr}")
            return
        await self.app(scope, receive, send)


@asynccontextmanager
async def lifespan(app):
    """Server-level lifecycle: fires once when the server starts and stops."""
    logger.info("Playwright MCP server started — ready to accept browser automation clients")
    yield
    logger.info("Playwright MCP server stopped — all client connections closed")


mcp = FastMCP(
    name="browser-playwright-server",
    lifespan=lifespan,
)


@mcp.tool()
async def browser_navigate(url: str) -> str:
    """
    Navigate the browser to the given URL and return the page title.

    Args:
        url: The URL to navigate to (e.g. 'https://example.com')
    """
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            title = await page.title()
            await browser.close()
            return f"Navigated to '{url}'. Page title: '{title}'"
    except ImportError:
        return "Error: playwright is not installed. Run: pip install playwright && playwright install chromium"
    except Exception as e:
        return f"Error navigating to '{url}': {e}"


@mcp.tool()
async def browser_get_text(url: str) -> str:
    """
    Navigate to a URL and extract all visible text content from the page.

    Args:
        url: The URL to scrape text from
    """
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            text = await page.inner_text("body")
            await browser.close()
            # Truncate to avoid overly large responses
            if len(text) > 2000:
                text = text[:2000] + "\n... (truncated)"
            return text
    except ImportError:
        return "Error: playwright is not installed. Run: pip install playwright && playwright install chromium"
    except Exception as e:
        return f"Error extracting text from '{url}': {e}"


@mcp.tool()
async def browser_get_links(url: str) -> list:
    """
    Navigate to a URL and return all hyperlinks found on the page.

    Args:
        url: The URL to extract links from
    """
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            links = await page.eval_on_selector_all(
                "a[href]",
                "elements => elements.map(el => ({text: el.innerText.trim(), href: el.href}))"
            )
            await browser.close()
            return links[:50]  # Return at most 50 links
    except ImportError:
        return ["Error: playwright is not installed. Run: pip install playwright && playwright install chromium"]
    except Exception as e:
        return [f"Error extracting links from '{url}': {e}"]


@mcp.tool()
async def browser_take_screenshot(url: str, output_path: str = "/tmp/screenshot.png") -> str:
    """
    Navigate to a URL and save a screenshot to the specified file path.

    Args:
        url: The URL to screenshot
        output_path: Local file path where the PNG screenshot will be saved
    """
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.screenshot(path=output_path, full_page=True)
            await browser.close()
            return f"Screenshot saved to: {output_path}"
    except ImportError:
        return "Error: playwright is not installed. Run: pip install playwright && playwright install chromium"
    except Exception as e:
        return f"Error taking screenshot of '{url}': {e}"


if __name__ == "__main__":
    logger.info("Starting Playwright MCP server on http://127.0.0.1:3003/sse ...")
    mcp.run(transport="sse", host="127.0.0.1", port=3003, middleware=[Middleware(PlaywrightConnectionLogger)])

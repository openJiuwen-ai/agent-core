# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Read-only headless-browser tool, for pages the plain HTTP fetch in
``image_tools.fetch_page_image``/``WebFetchWebpageTool`` can't handle
(JS-rendered content) -- e.g. inspecting a real booking site's images and
required form fields before recreating them as an A2UI form.

Deliberately exposes no click/fill/submit capability. This agent must never
complete a real booking/reservation/purchase itself -- see ``agent.py``'s
system prompt: it recreates what it finds as an A2UI form for the user to
fill in here, then hands off to the real site via an ``openUrl`` button
(``genui.open_url_button``) for the user to finish there themselves. Each
call launches a fresh headless Chromium instance and closes it before
returning, so no cookies/storage/session state is ever kept between calls.
"""

import asyncio
from typing import Any, Optional
from urllib.parse import urljoin

from openjiuwen.core.foundation.tool import tool

MAX_BROWSER_ATTEMPTS = 3
_NAV_TIMEOUT_MS = 20_000
_MAX_FORM_FIELDS = 25
_MAX_BODY_TEXT_CHARS = 2000
_BAD_IMAGE_SRC_HINTS = ("logo", "icon", "sprite", "avatar", "pixel", "spacer", "1x1", "blank.gif")

_META_IMAGE_JS = """() => {
    const tag = document.querySelector(
        'meta[property="og:image"], meta[property="og:image:url"], meta[name="twitter:image"]'
    );
    return tag ? tag.getAttribute('content') : null;
}"""

_MAIN_IMAGE_JS = """(hints) => {
    const imgs = Array.from(document.querySelectorAll('img'));
    for (const img of imgs) {
        const src = img.currentSrc || img.src || '';
        if (!src || src.startsWith('data:')) continue;
        const lower = src.toLowerCase();
        if (hints.some(h => lower.includes(h))) continue;
        if (img.naturalWidth && img.naturalWidth < 80) continue;
        return src;
    }
    return null;
}"""

_FORM_FIELDS_JS = """(maxFields) => {
    const out = [];
    const seen = new Set();
    for (const el of document.querySelectorAll('input, select, textarea')) {
        const type = (el.getAttribute('type') || el.tagName).toLowerCase();
        if (['hidden', 'submit', 'button', 'image', 'reset'].includes(type)) continue;
        const name = el.getAttribute('name') || el.id || '';
        if (!name || seen.has(name)) continue;
        seen.add(name);
        let label = '';
        if (el.id) {
            const lbl = document.querySelector(`label[for="${el.id}"]`);
            if (lbl) label = lbl.innerText.trim();
        }
        if (!label) {
            const closestLabel = el.closest('label');
            if (closestLabel) label = closestLabel.innerText.trim();
        }
        out.push({
            name,
            label: label || el.getAttribute('placeholder') || el.getAttribute('aria-label') || '',
            type,
            required: el.required || false,
        });
        if (out.length >= maxFields) break;
    }
    return out;
}"""


class _RetryableBrowserError(Exception):
    """A page load that may succeed on a fresh attempt (network hiccup, nav timeout)."""


async def _inspect_page_once(url: str) -> dict[str, Any]:
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise RuntimeError(
            "playwright is not installed -- run `uv sync --extra browser` and `uv run playwright install chromium`"
        ) from exc

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.goto(url, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT_MS)
                title = await page.title()
                final_url = page.url

                image_url = await page.evaluate(_META_IMAGE_JS)
                if not image_url:
                    image_url = await page.evaluate(_MAIN_IMAGE_JS, list(_BAD_IMAGE_SRC_HINTS))
                if image_url:
                    image_url = urljoin(final_url, image_url)

                form_fields = await page.evaluate(_FORM_FIELDS_JS, _MAX_FORM_FIELDS)

                body_text = await page.inner_text("body")
                if len(body_text) > _MAX_BODY_TEXT_CHARS:
                    body_text = body_text[:_MAX_BODY_TEXT_CHARS] + "... (truncated)"

                return {
                    "url": final_url,
                    "title": title,
                    "text": body_text,
                    "image_url": image_url,
                    "form_fields": form_fields,
                }
            finally:
                await browser.close()
    except Exception as exc:  # noqa: BLE001 -- navigation/JS errors are retryable
        raise _RetryableBrowserError(str(exc)) from exc


async def _inspect_page(url: str) -> dict[str, Any]:
    last_error: Optional[_RetryableBrowserError] = None
    for attempt in range(1, MAX_BROWSER_ATTEMPTS + 1):
        try:
            return await _inspect_page_once(url)
        except _RetryableBrowserError as exc:
            last_error = exc
            if attempt < MAX_BROWSER_ATTEMPTS:
                await asyncio.sleep(0.5 * attempt)
    raise last_error  # exhausted all attempts


@tool(
    description=(
        "Open a real webpage in a headless browser (handles JS-rendered pages the "
        "plain page fetch can't) and return its title, visible text, main image URL, "
        "and the form fields found on the page (name/label/type/required) -- read-only, "
        "it never clicks, fills, or submits anything. Use this to inspect a real "
        "booking/reservation site so you can recreate its images and required inputs "
        "with `show_card`/`ask_preferences_form` here in the app. Once the user submits "
        "that form, call `show_card` with an `openUrl`-style link (via `link_url`) back "
        "to this page's `url` so they can finish the booking on the real site themselves "
        "-- you must never attempt to complete a booking/reservation/purchase yourself."
    )
)
async def browser_inspect_page(url: str) -> dict[str, Any]:
    try:
        return await _inspect_page(url)
    except Exception as exc:  # noqa: BLE001 -- report the failure, don't crash the tool call
        return {"url": url, "title": None, "text": "", "image_url": None, "form_fields": [], "error": str(exc)}

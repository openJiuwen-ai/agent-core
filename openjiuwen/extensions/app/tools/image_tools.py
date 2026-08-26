# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Image-fetching tools for the ReAct agent.

Both ``search_images``'s and ``fetch_page_image``'s return values feed
``image_url`` into ``show_card``/``show_info_list`` (see ``tools.py``) -- see
that module's docstring for how a tool's return value reaches the WebSocket
layer.

``search_images`` (a real image search API, keyed by a natural-language
query) is the higher-priority tool -- prefer it whenever you don't already
have one specific page in mind. ``fetch_page_image`` (which scrapes a known
page's og:image via ``_fetch_page_once``) is the fallback for when you do.
"""

import asyncio
import json
from typing import Any, Optional
from urllib.parse import urlencode, urljoin

from openjiuwen.core.foundation.tool import tool
from openjiuwen.harness.tools.web import _http
from openjiuwen.harness.tools.web._common import _REQUEST_HEADERS, _parse_html
from openjiuwen.harness.tools.web._decode import _decode_response_text

from .. import config

_SERPAPI_ENDPOINT = "https://serpapi.com/search"

_ASPECT_RATIO_CODES = {"square": "s", "tall": "t", "wide": "w", "panoramic": "xw"}
_SIZE_CODES = {"large": "l", "medium": "m", "icon": "i"}


@tool(
    description=(
        "Search for real images matching a query, via SerpApi's Google Images "
        "Light API (a real image search API, not scraping) -- prefer this over "
        "`fetch_page_image` whenever you don't already have one specific page "
        "in mind (e.g. 'a nice photo of the Bund in Shanghai' rather than "
        "'the image on this exact Wikipedia article'). Returns up to "
        "`max_results` images, each with `image_url`, `title`, and `source` "
        "(the site the image came from). Never invent an image URL yourself; "
        "only use what this returns -- pick the first relevant result unless "
        "its `title`/`source` looks off-topic. Optional filters narrow the "
        "search: `image_type` ('photo', 'clipart', 'lineart', 'animated', or "
        "'face'), `image_color` (e.g. 'bw', 'red', 'blue', 'black', 'white', "
        "'trans' for transparent background), `aspect_ratio` ('square', "
        "'tall', 'wide', or 'panoramic'), and `size` ('large', 'medium', "
        "'icon', or a megapixel floor like '2mp'/'4mp'/'8mp'/'10mp'/'12mp'/"
        "'15mp'/'20mp'/'40mp'/'70mp'). Leave any filter unset unless the "
        "user's request specifically calls for it. An `error` in the response "
        "(e.g. the API key isn't configured) means don't fabricate an image "
        "for this request -- fall back to `fetch_page_image` on a page you "
        "know covers the topic instead."
    )
)
async def search_images(
    query: str,
    max_results: int = 5,
    image_type: Optional[str] = None,
    image_color: Optional[str] = None,
    aspect_ratio: Optional[str] = None,
    size: Optional[str] = None,
) -> dict[str, Any]:
    api_key = config.get("SERPAPI_API_KEY")
    if not api_key:
        return {"query": query, "images": [], "error": "SERPAPI_API_KEY is not configured on the server."}
    max_results = max(1, min(max_results, 10))

    params: dict[str, str] = {
        "engine": config.get("SERPAPI_ENGINE", "google_images_light"),
        "q": query,
        "api_key": api_key,
    }
    licenses = config.get("SERPAPI_LICENSES")
    if licenses:
        params["licenses"] = licenses
    gl = config.get("SERPAPI_GL")
    if gl:
        params["gl"] = gl
    hl = config.get("SERPAPI_HL")
    if hl:
        params["hl"] = hl

    # Per-call filters the agent can choose based on what the user asked for --
    # translated from friendly names into SerpApi/Google's actual short codes.
    if image_type:
        params["image_type"] = image_type
    if image_color:
        params["image_color"] = image_color
    if aspect_ratio:
        params["imgar"] = _ASPECT_RATIO_CODES.get(aspect_ratio, aspect_ratio)
    if size:
        params["imgsz"] = _SIZE_CODES.get(size, size)

    url = _SERPAPI_ENDPOINT + "?" + urlencode(params)
    try:
        async with _http.new_session() as session:
            status, headers, body, _final_url, _truncated = await _http.request(
                session, "GET", url, headers=_REQUEST_HEADERS, timeout_seconds=15, max_bytes=2_000_000
            )
    except Exception as exc:  # noqa: BLE001 -- report the failure, don't crash the tool call
        return {"query": query, "images": [], "error": str(exc)}

    text = _decode_response_text(body, content_type=headers.get("Content-Type", ""))
    if status >= 400:
        return {"query": query, "images": [], "error": f"SerpApi returned HTTP {status}: {text[:300]}"}
    try:
        data = json.loads(text)
    except Exception as exc:  # noqa: BLE001
        return {"query": query, "images": [], "error": f"Could not parse SerpApi response: {exc}"}

    images: list[dict[str, Any]] = []
    for item in data.get("images_results", []):
        if len(images) >= max_results:
            break
        # Prefer the (small, fast, reliably-hotlinkable) Google-served thumbnail
        # over `original` -- `original` points straight at the source site's own
        # full-resolution file (e.g. a multi-megabyte Wikimedia Commons image),
        # which is often slow to load and sometimes blocks hotlinked requests
        # outright, leaving the app showing a blank image area.
        image_url = item.get("thumbnail") or item.get("original")
        if not image_url:
            continue
        images.append(
            {
                "image_url": image_url,
                "title": item.get("title", ""),
                "source": item.get("source", ""),
            }
        )
    return {"query": query, "images": images}


MAX_IMAGE_FETCH_ATTEMPTS = 3

_BAD_IMAGE_SRC_HINTS = ("logo", "icon", "sprite", "avatar", "pixel", "spacer", "1x1", "blank.gif")


class _RetryableFetchError(Exception):
    """A page fetch that may succeed on a fresh attempt (network hiccup, 5xx, timeout)."""


async def _fetch_page_once(url: str) -> Optional[str]:
    """One fetch+parse attempt. Raises ``_RetryableFetchError`` for failures worth
    retrying; returns ``None`` (not an error) when the page loaded fine but simply
    has no usable image -- retrying an identical successful fetch would not change
    that, so callers should not retry in that case.
    """
    try:
        async with _http.new_session() as session:
            status, headers, body, final_url, _truncated = await _http.request(
                session, "GET", url, headers=_REQUEST_HEADERS, timeout_seconds=15, max_bytes=3_000_000
            )
    except Exception as exc:  # noqa: BLE001 -- network/timeout errors are retryable
        raise _RetryableFetchError(str(exc)) from exc
    if status >= 500:
        raise _RetryableFetchError(f"HTTP {status}")
    if status >= 400:
        return None

    html = _decode_response_text(body, content_type=headers.get("Content-Type", ""))
    soup = _parse_html(html)

    for selector in ('meta[property="og:image"]', 'meta[property="og:image:url"]', 'meta[name="twitter:image"]'):
        tag = soup.select_one(selector)
        content = tag.get("content") if tag else None
        if content:
            return urljoin(final_url, content.strip())

    for img in soup.select("img"):
        src = img.get("src") or img.get("data-src")
        if not src or src.startswith("data:"):
            continue
        if any(hint in src.lower() for hint in _BAD_IMAGE_SRC_HINTS):
            continue
        return urljoin(final_url, src)
    return None


async def _extract_page_image(url: str) -> Optional[str]:
    """Fetch ``url`` and pull out its Open Graph image, or failing that the
    first plausible content <img> -- reuses the same HTTP transport and HTML
    parser as ``fetch_webpage`` rather than duplicating that logic, but keeps
    the raw markup (fetch_webpage strips it entirely for text extraction, so
    it can never recover a real image URL).

    Retries transient failures (network errors, timeouts, 5xx) up to
    ``MAX_IMAGE_FETCH_ATTEMPTS`` times with a short backoff between attempts,
    so one flaky request doesn't cost the user an image they could have had.
    """
    last_error: Optional[_RetryableFetchError] = None
    for attempt in range(1, MAX_IMAGE_FETCH_ATTEMPTS + 1):
        try:
            return await _fetch_page_once(url)
        except _RetryableFetchError as exc:
            last_error = exc
            if attempt < MAX_IMAGE_FETCH_ATTEMPTS:
                await asyncio.sleep(0.5 * attempt)
    raise last_error  # exhausted all attempts


@tool(
    description=(
        "Fetch a webpage and return the URL of its main/representative image "
        "(its Open Graph image, or the first substantial <img> on the page) -- "
        "use this instead of guessing an image URL from memory when you want "
        "to show a real image in `show_card` or a `show_info_list` item. Pass "
        "a page you already know is relevant (e.g. the Wikipedia article or a "
        "recipe page for that specific dish/topic). Returns `image_url: null` "
        "if no usable image was found on the page -- in that case, don't "
        "invent one, just leave the image out."
    )
)
async def fetch_page_image(url: str) -> dict[str, Any]:
    try:
        image_url = await _extract_page_image(url)
    except Exception as exc:  # noqa: BLE001 -- report the failure, don't crash the tool call
        return {"url": url, "image_url": None, "error": str(exc)}
    return {"url": url, "image_url": image_url}

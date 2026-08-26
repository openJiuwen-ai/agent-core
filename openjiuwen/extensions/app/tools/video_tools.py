# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Video-clip tools for the ReAct agent: finding real videos and rendering
them as a playable A2UI surface.

``genui.video()`` (the client's native player) can only open a real, direct
video file/stream URL -- it has no HTML/JS engine, so it can never open a
YouTube/Vimeo *page*. Those sites don't expose a direct file URL through
normal means; getting one requires extracting their signed, expiring
internal stream URLs, which is fragile and against their Terms of Service.
Instead, YouTube/Vimeo links resolve to that site's own ``/embed/<id>`` URL
and render through ``genui.web()`` (a WebView) -- playback goes through
their own embedded player, exactly as if you'd pasted the embed URL into a
browser tab. That's the only path used for those sites; nothing here ever
scrapes or reverse-engineers a raw stream URL.
"""

import asyncio
import json
import re
from typing import Any, Optional
from urllib.parse import urlencode, urljoin

from pydantic import BaseModel, Field

from openjiuwen.core.foundation.tool import tool
from openjiuwen.harness.tools.web import _http
from openjiuwen.harness.tools.web._common import _REQUEST_HEADERS, _parse_html
from openjiuwen.harness.tools.web._decode import _decode_response_text

from .. import config, genui

MAX_VIDEO_FETCH_ATTEMPTS = 3


class _RetryableFetchError(Exception):
    """A page fetch that may succeed on a fresh attempt (network hiccup, 5xx, timeout)."""


@tool(
    description=(
        "Search YouTube for real videos matching a query, via the YouTube Data "
        "API (a real search API, not scraping a JS-rendered results page) -- "
        "this is the primary way to find clips for `show_video_clips` when the "
        "user wants a video/videos about something. Returns up to `max_results` "
        "real videos, each with `video_id`, `title`, `description`, and a "
        "ready-to-use `embed_url`. Pass that `embed_url` straight into "
        "`show_video_clips` as a clip's `embed_url` with `kind='youtube'` -- "
        "there's no need to call `fetch_video_source` on these results, they're "
        "already resolved. Never invent a video ID or title yourself; only use "
        "what this returns. An `error` in the response (e.g. the API key isn't "
        "configured) means don't fabricate a video for this request."
    )
)
async def search_youtube_videos(query: str, max_results: int = 5) -> dict[str, Any]:
    api_key = config.get("YOUTUBE_API_KEY")
    if not api_key:
        return {"query": query, "videos": [], "error": "YOUTUBE_API_KEY is not configured on the server."}
    max_results = max(1, min(max_results, 10))
    params = {
        "part": "snippet",
        "type": "video",
        "q": query,
        "maxResults": str(max_results),
        "safeSearch": "moderate",
        "key": api_key,
    }
    url = "https://www.googleapis.com/youtube/v3/search?" + urlencode(params)
    try:
        async with _http.new_session() as session:
            status, headers, body, _final_url, _truncated = await _http.request(
                session, "GET", url, headers=_REQUEST_HEADERS, timeout_seconds=15, max_bytes=1_000_000
            )
    except Exception as exc:  # noqa: BLE001 -- report the failure, don't crash the tool call
        return {"query": query, "videos": [], "error": str(exc)}

    text = _decode_response_text(body, content_type=headers.get("Content-Type", ""))
    if status >= 400:
        return {"query": query, "videos": [], "error": f"YouTube API returned HTTP {status}: {text[:300]}"}
    try:
        data = json.loads(text)
    except Exception as exc:  # noqa: BLE001
        return {"query": query, "videos": [], "error": f"Could not parse YouTube API response: {exc}"}

    videos: list[dict[str, Any]] = []
    for item in data.get("items", []):
        video_id = item.get("id", {}).get("videoId")
        if not video_id:
            continue
        snippet = item.get("snippet", {})
        videos.append(
            {
                "video_id": video_id,
                "title": snippet.get("title", ""),
                "description": snippet.get("description", ""),
                "embed_url": _youtube_embed_url(video_id),
            }
        )
    return {"query": query, "videos": videos}


_YOUTUBE_ID_RE = re.compile(r"(?:youtube(?:-nocookie)?\.com/(?:watch\?(?:.*&)?v=|embed/|shorts/)|youtu\.be/)([A-Za-z0-9_-]{11})")

_VIDEO_FILE_EXTENSIONS = (".mp4", ".webm", ".mov", ".m3u8", ".ogv")


def _extract_youtube_id(url: str) -> Optional[str]:
    match = _YOUTUBE_ID_RE.search(url)
    return match.group(1) if match else None


def _youtube_embed_url(video_id: str) -> str:
    # youtube-nocookie.com (YouTube's own privacy-enhanced embed domain) has a
    # more lenient anonymous-embed policy than youtube.com/embed -- the client
    # WebView navigates straight to this URL as a top-level page (there's no
    # real hosting page/Referer for YouTube's player to validate against
    # here), which youtube.com/embed's stricter check rejects with
    # "Error 153: Video player configuration error" for most real videos.
    # rel=0 keeps end-of-video suggestions restricted to the same channel.
    return f"https://www.youtube-nocookie.com/embed/{video_id}?rel=0"


async def _fetch_page_video_once(url: str) -> Optional[str]:
    """One fetch+parse attempt for a direct video source. Mirrors
    ``image_tools._fetch_page_once``'s retry/error split: raises for
    failures worth retrying, returns ``None`` when the page loaded fine but
    has no direct <video> (true of most pages -- they embed YouTube/Vimeo
    instead, which this never returns a URL for; see ``_extract_youtube_id``
    for that path).
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

    for selector in ('meta[property="og:video:secure_url"]', 'meta[property="og:video:url"]', 'meta[property="og:video"]'):
        tag = soup.select_one(selector)
        content = tag.get("content") if tag else None
        if content:
            resolved = urljoin(final_url, content.strip())
            if resolved.lower().split("?")[0].endswith(_VIDEO_FILE_EXTENSIONS):
                return resolved

    for video_tag in soup.select("video"):
        src = video_tag.get("src")
        if src and not src.startswith("data:"):
            return urljoin(final_url, src)
        for source_tag in video_tag.select("source"):
            src = source_tag.get("src")
            if src and not src.startswith("data:"):
                return urljoin(final_url, src)
    return None


async def _extract_page_video(url: str) -> Optional[str]:
    """Fetch ``url`` and pull out a real, direct playable video URL from an
    HTML5 ``<video>``/``<source>`` element or an ``og:video`` meta tag (a
    Wikimedia Commons file page or similar self-hosted video). Retries
    transient failures like ``image_tools._extract_page_image`` does.
    """
    last_error: Optional[_RetryableFetchError] = None
    for attempt in range(1, MAX_VIDEO_FETCH_ATTEMPTS + 1):
        try:
            return await _fetch_page_video_once(url)
        except _RetryableFetchError as exc:
            last_error = exc
            if attempt < MAX_VIDEO_FETCH_ATTEMPTS:
                await asyncio.sleep(0.5 * attempt)
    raise last_error  # exhausted all attempts


@tool(
    description=(
        "Resolve a playable video source for `show_video_clips` from a URL you "
        "found via `free_search` -- never invent a video ID or file URL yourself. "
        "If the URL is a YouTube (or youtu.be) link, returns `kind: \"youtube\"` "
        "and an `embed_url` that plays through YouTube's own embedded player. "
        "Otherwise fetches the page and looks for a real, direct playable video "
        "file (an HTML5 <video> source -- e.g. a Wikimedia Commons file page or "
        "other self-hosted video) and returns `kind: \"direct\"` with a `video_url`. "
        "Returns `kind: null` if neither was found -- in that case don't invent a "
        "video for this URL, try a different search result instead."
    )
)
async def fetch_video_source(url: str) -> dict[str, Any]:
    youtube_id = _extract_youtube_id(url)
    if youtube_id:
        return {"url": url, "kind": "youtube", "embed_url": _youtube_embed_url(youtube_id)}
    try:
        video_url = await _extract_page_video(url)
    except Exception as exc:  # noqa: BLE001 -- report the failure, don't crash the tool call
        return {"url": url, "kind": None, "video_url": None, "error": str(exc)}
    if video_url is None:
        return {"url": url, "kind": None, "video_url": None}
    return {"url": url, "kind": "direct", "video_url": video_url}


class VideoClip(BaseModel):
    caption: str = Field(description="Short caption/title shown below this clip.")
    kind: str = Field(description="Exactly the `kind` value `fetch_video_source` returned for this clip: 'youtube' or 'direct'.")
    embed_url: Optional[str] = Field(
        default=None, description="Required when kind='youtube': the `embed_url` `fetch_video_source` returned."
    )
    video_url: Optional[str] = Field(
        default=None, description="Required when kind='direct': the `video_url` `fetch_video_source` returned."
    )


@tool(
    description=(
        "Render one or more playable video clips as an A2UI surface, each in its "
        "own card with a caption. Every clip's `embed_url`/`video_url` must come "
        "from a prior `fetch_video_source` call on a real URL found via "
        "`free_search` -- never invent a video ID or file URL. Resolve every clip "
        "you want to show first, then call this once with all of them; don't call "
        "it once per clip."
    )
)
def show_video_clips(title: str, clips: list[VideoClip]) -> dict[str, Any]:
    surface_id = genui.new_surface_id("video")
    parsed_clips = [c if isinstance(c, VideoClip) else VideoClip(**c) for c in clips]
    items: list[tuple[str, str, str]] = []
    for c in parsed_clips:
        if c.kind == "youtube" and c.embed_url:
            items.append(("youtube", c.embed_url, c.caption))
        elif c.kind == "direct" and c.video_url:
            items.append(("direct", c.video_url, c.caption))
    if not items:
        return {"text": "I couldn't find a playable video for that.", "genui": []}
    summary = "\n".join(f"- {c.caption}" for c in parsed_clips)
    return {
        "text": f"{title}\n{summary}",
        "genui": genui.video_gallery_card(surface_id, title, items),
    }

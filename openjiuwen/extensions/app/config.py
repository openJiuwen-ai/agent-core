# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Environment-driven configuration for the A2UI ReAct agent server."""

import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

# Auto-load a local .env next to this file (never committed -- see .gitignore)
# so `python server.py` works without requiring `uv run --env-file`. Real
# environment variables already set take precedence (override=False default).
load_dotenv(Path(__file__).resolve().parent / ".env")

# openjiuwen.harness.tools.web.WebFreeSearchTool reads this env var directly at
# call time (it's a harness-wide flag, not one of this app's own _DEFAULTS).
# DuckDuckGo scraping needs no API key, so default it on for this extension
# unless the environment already says otherwise.
os.environ.setdefault("FREE_SEARCH_DDG_ENABLED", "true")

_CERTS_DIR = Path(__file__).resolve().parent / "certs"

_DEFAULTS: dict[str, Any] = {
    "API_KEY": os.getenv("API_KEY", ""),
    "API_BASE": os.getenv("API_BASE", "https://api.deepseek.com/v1"),
    "MODEL_NAME": os.getenv("MODEL_NAME", "deepseek-v4-flash"),
    "MODEL_PROVIDER": os.getenv("MODEL_PROVIDER", "DeepSeek"),
    "LLM_TEMPERATURE": float(os.getenv("LLM_TEMPERATURE", "1.0")),
    "LLM_SEED": int(os.getenv("LLM_SEED", "42")),
    # Left unset (None) by default and only forwarded to ModelRequestConfig
    # when actually configured -- not every provider/model accepts this
    # (DeepSeek's chat models don't expose it), so it must never be sent
    # unless the deployment explicitly opted in.
    "LLM_REASONING_EFFORT": os.getenv("LLM_REASONING_EFFORT") or None,
    "LLM_SSL_VERIFY": os.getenv("LLM_SSL_VERIFY", "false").lower() == "true",
    "HOST": os.getenv("A2UI_AGENT_HOST", "0.0.0.0"),
    "PORT": int(os.getenv("A2UI_AGENT_PORT", "8090")),
    "CATALOG_ID": os.getenv("A2UI_CATALOG_ID", "https://a2ui.org/specification/v0_9/basic_catalog.json"),
    # YouTube Data API v3 key for `tools.search_youtube_videos` -- a real
    # search API instead of scraping YouTube's JS-rendered results page (which
    # `free_search`/`fetch_page_image` can't parse) or routing video discovery
    # through DuckDuckGo scraping (rate-limited/CAPTCHA'd under load). Empty
    # means `search_youtube_videos` reports an error instead of silently
    # returning nothing.
    "YOUTUBE_API_KEY": os.getenv("YOUTUBE_API_KEY", ""),
    # SerpApi key, shared by every SerpApi-backed tool: `image_tools.
    # search_images` (Google Images Light engine, a real image search API
    # keyed by a natural-language query, instead of relying on already
    # knowing a specific page to scrape an og:image from -- see
    # `fetch_page_image`) and `hotel_tools.search_hotels` (Google Hotels
    # engine, real hotel availability/pricing). Empty API key means those
    # tools report an error instead of silently returning nothing.
    "SERPAPI_API_KEY": os.getenv("SERPAPI_API_KEY", ""),
    "SERPAPI_ENGINE": os.getenv("SERPAPI_ENGINE", "google_images_light"),
    "SERPAPI_LICENSES": os.getenv("SERPAPI_LICENSES", ""),
    # Optional locale defaults applied to every `search_images`/
    # `search_hotels` call (country/language bias for results) -- left empty
    # (omitted from the request, SerpApi/Google falls back to its own
    # default) unless set.
    "SERPAPI_GL": os.getenv("SERPAPI_GL", ""),
    "SERPAPI_HL": os.getenv("SERPAPI_HL", ""),
    # Google Maps Platform key for `map_tools.geocode_place`/`show_map`
    # (Places API (New) + Maps JavaScript API must both be enabled on the
    # project this key belongs to). Empty means those tools report an error
    # instead of silently returning nothing.
    "GOOGLE_MAPS_API_KEY": os.getenv("GOOGLE_MAPS_API_KEY", ""),
    # Public https:// base URL the client's WebView can reach directly --
    # used to build fully-qualified embed URLs like `/map-embed`
    # (map_tools.show_map). Unlike `/ws`, which the client already has
    # hardcoded separately in its own Config.ets, embed URLs are built
    # server-side and handed to the client as ordinary component data, so
    # this needs to be configured to match wherever this server is actually
    # reachable from the device.
    "PUBLIC_BASE_URL": os.getenv("A2UI_PUBLIC_BASE_URL", ""),
    # TLS for the client<->agent WebSocket, so traffic (including the API key
    # never touching this hop, but chat content and tool output) isn't sent
    # in cleartext. The private key stays on the server (certs/server.key,
    # gitignored); only the certificate (public key) is ever distributed to
    # clients, which pin it instead of relying on a CA -- see the Flutter
    # app's WsService for the client side of this. Regenerate both with
    # certs/generate.sh. Falls back to plain ws:// if either file is absent
    # so a fresh checkout without generated certs still runs for local dev.
    "SSL_CERTFILE": os.getenv("A2UI_SSL_CERTFILE", str(_CERTS_DIR / "server.crt")),
    "SSL_KEYFILE": os.getenv("A2UI_SSL_KEYFILE", str(_CERTS_DIR / "server.key")),
}

_values: dict[str, Any] = dict(_DEFAULTS)


def get(key: str, default: Any = None) -> Any:
    return _values.get(key, default)


def set_value(key: str, value: Any) -> None:
    _values[key] = value

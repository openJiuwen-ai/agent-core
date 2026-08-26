# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Finance search tools for the ReAct agent: real stock/index/currency/crypto
data via SerpApi's Google Finance engine, rendered as cards with a real,
interactive price chart -- AGenUI's native ``Chart`` catalog component
(via ``genui.chart()``), fed the actual price-history points SerpApi
returned (see ``_build_chart_points``).

The query (a company/ticker/index/currency/crypto) and desired time window
are never guessed -- the system prompt in ``agent.py`` has the agent collect
them from the user first via `ask_preferences_form`, then pass them straight
into `search_finance`.

If `search_finance`/`show_finance_results` are unavailable (no API key
configured) or return no data, the system prompt falls back to
`free_search`/`browser_inspect_page` to find real finance data instead --
see ``agent.py``.
"""

import json
from typing import Any, Optional
from urllib.parse import urlencode

from pydantic import BaseModel, Field

from openjiuwen.core.foundation.tool import tool
from openjiuwen.harness.tools.web import _http
from openjiuwen.harness.tools.web._common import _REQUEST_HEADERS
from openjiuwen.harness.tools.web._decode import _decode_response_text

from .. import config, genui

_FINANCE_SEARCH_ENDPOINT = "https://serpapi.com/search.json"
_FINANCE_ENGINE = "google_finance"
_VALID_WINDOWS = {"1D", "5D", "1M", "6M", "YTD", "1Y", "5Y", "MAX"}
_INTRADAY_WINDOWS = {"1D", "5D"}
# Caps how many of SerpApi's real graph points get fed into the chart, so it
# stays readable rather than an illegibly dense line -- the chart itself
# renders natively client-side (genui.chart()), no image/URL length limit
# involved, this is purely for chart legibility.
MAX_CHART_POINTS = 30


def _short_label(date_str: Optional[str], window: str) -> str:
    """SerpApi's graph point dates look like "Nov 19 2025, 09:30 AM
    UTC-05:00" -- too long for a chart axis tick, so pull out just the time
    (intraday windows) or the month/day (longer windows).
    """
    if not date_str:
        return ""
    date_part, _, time_part = date_str.partition(",")
    if window in _INTRADAY_WINDOWS and time_part:
        return " ".join(time_part.strip().split(" ")[:2])
    tokens = date_part.strip().split(" ")
    return " ".join(tokens[:2]) if len(tokens) >= 2 else date_part.strip()


def _build_chart_points(graph: list[dict[str, Any]], window: str) -> tuple[list[str], list[float]]:
    """Downsample SerpApi's real price-history graph points to (x-axis
    labels, values) for a native line chart -- always keeps the latest real
    price point even after sampling.
    """
    points = [p for p in graph if isinstance(p.get("price"), (int, float))]
    if not points:
        return [], []
    if len(points) > MAX_CHART_POINTS:
        step = len(points) / MAX_CHART_POINTS
        sampled = [points[min(int(i * step), len(points) - 1)] for i in range(MAX_CHART_POINTS)]
        sampled[-1] = points[-1]  # always keep the latest real price point
        points = sampled
    return [_short_label(p.get("date"), window) for p in points], [p["price"] for p in points]


@tool(
    description=(
        "Search for real, current stock/index/mutual-fund/currency/crypto "
        "data via SerpApi's Google Finance engine (not guessed from memory) "
        "-- use this whenever the user wants price/market info for a "
        "specific security. `query` MUST be formatted exactly the way "
        "Google Finance itself expects, never a bare company name or ticker "
        "(those return no results): for a stock/index/mutual fund, "
        "'TICKER:EXCHANGE' (e.g. 'AAPL:NASDAQ', 'TSLA:NASDAQ', 'JPM:NYSE', "
        "'.DJI:INDEXDJX') -- resolve the company name and its listing "
        "exchange yourself from your own knowledge, the same way you'd "
        "resolve a city to its airport code for flights; for currency or "
        "crypto, 'BASE-QUOTE' (e.g. 'EUR-USD', 'BTC-USD'), no exchange "
        "suffix. If the user only gave a company name, silently resolve it "
        "to its ticker:exchange before calling this -- don't ask them for "
        "the exchange. Collect *which* security(ies) and the time window "
        "from the user first via `ask_preferences_form` rather than "
        "guessing which one they mean. `window` controls the price "
        "history range: one of '1D' (default), '5D', '1M', '6M', 'YTD', "
        "'1Y', '5Y', 'MAX'. Returns real `title`, `stock` (ticker), "
        "`exchange`, `price`, `currency`, `change_text` (e.g. '+2.34 "
        "(+1.58%) today'), `movement` ('Up'/'Down'/'Flat'), `as_of`, "
        "`description` (a short real company/asset blurb, when available), "
        "`chart_x_axis`/`chart_values` (paired lists built from the actual "
        "price-history SerpApi returned, for a real interactive chart), and "
        "`link` (the real Google Finance page) -- any "
        "field besides `title` can come back missing, which is normal; pass "
        "only what's present into `show_finance_results`. Call this once "
        "per security if the user asks about more than one (like "
        "`search_images`), then call `show_finance_results` once with all "
        "of them. An `error` (no API key configured, or no data found) "
        "means don't fabricate a price or chart -- fall back to "
        "`free_search`/`browser_inspect_page` to find real finance data "
        "instead."
    )
)
async def search_finance(query: str, window: str = "1D") -> dict[str, Any]:
    api_key = config.get("SERPAPI_API_KEY")
    if not api_key:
        return {"query": query, "error": "SERPAPI_API_KEY is not configured on the server."}

    window = window if window in _VALID_WINDOWS else "1D"
    params: dict[str, Any] = {"engine": _FINANCE_ENGINE, "q": query, "window": window, "api_key": api_key}
    hl = config.get("SERPAPI_HL")
    if hl:
        params["hl"] = hl

    url = _FINANCE_SEARCH_ENDPOINT + "?" + urlencode(params)
    try:
        async with _http.new_session() as session:
            status, headers, body, _final_url, _truncated = await _http.request(
                session, "GET", url, headers=_REQUEST_HEADERS, timeout_seconds=20, max_bytes=3_000_000
            )
    except Exception as exc:  # noqa: BLE001 -- report the failure, don't crash the tool call
        return {"query": query, "error": str(exc)}

    text = _decode_response_text(body, content_type=headers.get("Content-Type", ""))
    if status >= 400:
        return {"query": query, "error": f"SerpApi returned HTTP {status}: {text[:300]}"}
    try:
        data = json.loads(text)
    except Exception as exc:  # noqa: BLE001
        return {"query": query, "error": f"Could not parse SerpApi response: {exc}"}

    if data.get("search_metadata", {}).get("status") == "Error":
        error_message = data.get("error") or data.get("search_metadata", {}).get("error") or "unknown error"
        return {"query": query, "error": f"SerpApi Google Finance search failed: {error_message}"}

    summary = data.get("summary") or {}
    title = summary.get("title")
    if not title:
        return {"query": query, "error": "No finance data found for this query."}

    price_movement = summary.get("price_movement") or {}
    movement = price_movement.get("movement")
    change_text = None
    if price_movement.get("value") is not None:
        sign = "+" if movement == "Up" else "-" if movement == "Down" else ""
        percent = price_movement.get("percentage")
        change_text = f"{sign}{abs(price_movement['value'])}"
        if percent is not None:
            change_text += f" ({sign}{abs(percent)}%) today"

    description = None
    knowledge_graph = data.get("knowledge_graph") or {}
    about = knowledge_graph.get("about") or {}
    snippets = about.get("snippets") or []
    if snippets:
        description = snippets[0]
    elif about.get("description"):
        description = about["description"]

    stock = summary.get("stock")
    exchange = summary.get("exchange")
    link = f"https://www.google.com/finance/quote/{stock}:{exchange}" if stock and exchange else None

    chart_x_axis, chart_values = _build_chart_points(data.get("graph") or [], window)

    return {
        "query": query,
        "title": title,
        "stock": stock,
        "exchange": exchange,
        "price": summary.get("price"),
        "currency": summary.get("currency"),
        "change_text": change_text,
        "movement": movement,
        "as_of": summary.get("date"),
        "window": window,
        "description": description,
        "chart_x_axis": chart_x_axis or None,
        "chart_values": chart_values or None,
        "link": link,
    }


class FinanceResult(BaseModel):
    title: str = Field(description="The security's real display name, from a prior `search_finance` call.")
    stock: Optional[str] = Field(default=None, description="Real ticker symbol from `search_finance`.")
    exchange: Optional[str] = Field(default=None, description="Real exchange code from `search_finance`.")
    price: Optional[str] = Field(default=None, description="Real formatted current price from `search_finance`.")
    currency: Optional[str] = Field(default=None, description="Real currency code from `search_finance`.")
    change_text: Optional[str] = Field(
        default=None, description="Real formatted price change (e.g. '+2.34 (+1.58%) today') from `search_finance`."
    )
    movement: Optional[str] = Field(
        default=None, description="Real 'Up'/'Down'/'Flat' from `search_finance` -- colors the change text."
    )
    as_of: Optional[str] = Field(default=None, description="Real last-updated timestamp from `search_finance`.")
    window: Optional[str] = Field(default=None, description="The time range the chart covers, from `search_finance`.")
    description: Optional[str] = Field(
        default=None, description="Real short company/asset blurb from `search_finance`, when available."
    )
    chart_x_axis: Optional[list[str]] = Field(
        default=None, description="Real chart x-axis labels from `search_finance`, paired with `chart_values`."
    )
    chart_values: Optional[list[float]] = Field(
        default=None, description="Real price-history values from `search_finance`, paired with `chart_x_axis`."
    )
    link: Optional[str] = Field(
        default=None, description="Real Google Finance page URL from `search_finance` -- the button target."
    )


@tool(
    description=(
        "Render one or more real finance results as an A2UI surface, each "
        "in its own card with the security's name/ticker, current price and "
        "change (colored for up/down), a real interactive price chart, and a "
        "'View on Google Finance' button. Every item must come from a prior "
        "`search_finance` call -- never invent a price, change, or chart. "
        "Pass through exactly what `search_finance` returned, including "
        "missing/null fields (just leave those out, don't invent a "
        "replacement value). Call this once with every security the user "
        "asked about, not once per security."
    )
)
def show_finance_results(title: str, items: list[FinanceResult]) -> dict[str, Any]:
    parsed_items = [i if isinstance(i, FinanceResult) else FinanceResult(**i) for i in items]
    if not parsed_items:
        return {"text": "I couldn't find any finance data for that.", "genui": []}

    surface_id = genui.new_surface_id("finance")
    summary_lines = []
    for item in parsed_items:
        line = f"- {item.title}"
        if item.price:
            line += f" ({item.price}"
            line += f", {item.change_text})" if item.change_text else ")"
        summary_lines.append(line)
    summary = "\n".join(summary_lines)
    item_dicts = [i.model_dump(exclude_none=True) for i in parsed_items]
    return {
        "text": f"{title}\n{summary}",
        "genui": genui.finance_gallery_card(surface_id, title, item_dicts),
    }

# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Shopping search tools for the ReAct agent: real Amazon product search via
SerpApi's Amazon Search engine, rendered as a gallery of product cards.

Mirrors hotel_tools.py's shape -- see that module's own docstring for the
general pattern (search tool returns structured results with an `error`
field on failure; show tool renders a paginated gallery via `more_count`/
`show_more_*`, same as hotels/flights).
"""

import json
from typing import Any, Optional
from urllib.parse import urlencode

from pydantic import BaseModel, Field

from openjiuwen.core.foundation.tool import tool
from openjiuwen.harness.tools.web import _http
from openjiuwen.harness.tools.web._common import _REQUEST_HEADERS
from openjiuwen.harness.tools.web._decode import _decode_response_text

from ..core import config, genui

_SHOPPING_SEARCH_ENDPOINT = "https://serpapi.com/search.json"
_SHOPPING_ENGINE = "amazon"
MAX_PRODUCT_RESULTS = 10


@tool(
    description=(
        "Search for real, currently-listed products on Amazon via SerpApi's "
        "Amazon Search engine (not guessed from memory) -- use this whenever "
        "the user wants to shop for, compare, or buy a product. `query` "
        "should be a specific product search term (e.g. 'wireless noise "
        "cancelling headphones', not just 'something for my commute'). "
        "Defaults to the Singapore marketplace (`amazon_domain='amazon.sg'`, "
        "prices in SGD) -- only pass a different `amazon_domain` (e.g. "
        "'amazon.com') if the user specifically asks for another country's "
        "store. Returns up to 10 real products, each with `title`, `price`, "
        "`rating`, `reviews`, `image_url`, `link` (the product's real Amazon "
        "page), and `is_prime` -- any of these besides `title` can be "
        "missing for a given product, which is normal; only pass fields "
        "that are actually present into `show_shopping_results`, never "
        "invent a replacement. An `error` (no API key configured, or no "
        "products found) means don't fabricate a product -- tell the user "
        "the search failed instead."
    )
)
async def search_products(query: str, amazon_domain: str = "amazon.sg") -> dict[str, Any]:
    api_key = config.get("SERPAPI_API_KEY")
    if not api_key:
        return {"query": query, "products": [], "error": "SERPAPI_API_KEY is not configured on the server."}

    params: dict[str, Any] = {
        "engine": _SHOPPING_ENGINE,
        "k": query,
        "amazon_domain": amazon_domain,
        "api_key": api_key,
    }

    url = _SHOPPING_SEARCH_ENDPOINT + "?" + urlencode(params)
    try:
        async with _http.new_session() as session:
            status, headers, body, _final_url, _truncated = await _http.request(
                session, "GET", url, headers=_REQUEST_HEADERS, timeout_seconds=20, max_bytes=3_000_000
            )
    except Exception as exc:  # noqa: BLE001 -- report the failure, don't crash the tool call
        return {"query": query, "products": [], "error": str(exc)}

    text = _decode_response_text(body, content_type=headers.get("Content-Type", ""))
    if status >= 400:
        return {"query": query, "products": [], "error": f"SerpApi returned HTTP {status}: {text[:300]}"}
    try:
        data = json.loads(text)
    except Exception as exc:  # noqa: BLE001
        return {"query": query, "products": [], "error": f"Could not parse SerpApi response: {exc}"}

    if data.get("search_metadata", {}).get("status") == "Error":
        error_message = data.get("error") or data.get("search_metadata", {}).get("error") or "unknown error"
        return {"query": query, "products": [], "error": f"SerpApi Amazon search failed: {error_message}"}

    results = data.get("organic_results") or []
    if not results:
        return {"query": query, "products": [], "error": "No products found for this search."}

    products: list[dict[str, Any]] = []
    for item in results[:MAX_PRODUCT_RESULTS]:
        title = item.get("title")
        if not title:
            continue
        products.append(
            {
                "title": title,
                # `price` is already a formatted string (e.g. "$29.99") --
                # prefer it over `extracted_price` (a bare float) so the
                # tool's output needs no reformatting downstream.
                "price": item.get("price"),
                "rating": item.get("rating"),
                "reviews": item.get("reviews"),
                "image_url": item.get("thumbnail"),
                # `link_clean` (when present) strips SerpApi/Amazon tracking
                # params off the real product URL -- prefer it, fall back to
                # `link` for results that don't have it.
                "link": item.get("link_clean") or item.get("link"),
                "is_prime": item.get("is_prime"),
            }
        )

    return {"query": query, "products": products}


class ProductResult(BaseModel):
    title: str = Field(description="The product's real title, from a prior `search_products` call.")
    price: Optional[str] = Field(
        default=None, description="Real formatted price (e.g. '$29.99') from `search_products`. Omit if null."
    )
    rating: Optional[float] = Field(
        default=None, description="Real rating (e.g. 4.5) from `search_products`. Omit if it returned null."
    )
    reviews: Optional[int] = Field(
        default=None, description="Real review count from `search_products`, shown alongside `rating` if given."
    )
    image_url: Optional[str] = Field(
        default=None, description="Real product photo URL from `search_products`. Omit if it returned null."
    )
    link: Optional[str] = Field(
        default=None,
        description="Real URL to the product's Amazon page, from `search_products` -- the 'Buy Now' button target.",
    )
    is_prime: Optional[bool] = Field(
        default=None, description="Whether the product is Prime-eligible, from `search_products`."
    )


@tool(
    description=(
        "Render a gallery of real Amazon product results as an A2UI "
        "surface, each in its own card with a photo, price/rating, and a "
        "'Buy Now' button that opens the product's real Amazon page "
        "externally for the user to complete the purchase there themselves "
        "-- this agent never completes a purchase on the user's behalf. "
        "Every product must come from a prior `search_products` call -- "
        "never invent a product, price, rating, or link. Pass through "
        "exactly what `search_products` returned, including missing/null "
        "fields (just leave those out of the product, don't invent a "
        "replacement value). To keep each response fast, show products 3 "
        "at a time: pass only the next 3 products from a `search_products` "
        "result and set `more_count` to how many are left after this batch "
        "(e.g. showing products 1-3 of 10 -> `more_count=7`) -- this "
        "renders a 'Show more' link. Each time the user taps it, you'll "
        "see a `show_more_products` UI action -- respond by calling this "
        "again with just the *next* 3 products from that same earlier "
        "`search_products` result (don't search again, and don't dump all "
        "the remaining products at once), updating `more_count` to "
        "whatever is left after that batch. Repeat this one-batch-per-tap "
        "pattern until every product has been shown, at which point the "
        "final batch's `more_count` is 0 and no button renders. If there "
        "were 3 or fewer products to begin with, just show all of them "
        "with `more_count=0`."
    )
)
def show_shopping_results(title: str, products: list[ProductResult], more_count: int = 0) -> dict[str, Any]:
    parsed_products = [p if isinstance(p, ProductResult) else ProductResult(**p) for p in products]
    if not parsed_products:
        return {"text": "I couldn't find any products for that search.", "genui": []}

    surface_id = genui.new_surface_id("shopping")
    summary_lines = []
    for p in parsed_products:
        line = f"- {p.title}"
        if p.price:
            line += f" ({p.price})"
        summary_lines.append(line)
    if more_count > 0:
        summary_lines.append(f"...and {more_count} more")
    summary = "\n".join(summary_lines)
    product_dicts = [p.model_dump(exclude_none=True) for p in parsed_products]
    return {
        "text": f"{title}\n{summary}",
        "genui": genui.shopping_gallery_card(surface_id, title, product_dicts, more_count=more_count),
    }

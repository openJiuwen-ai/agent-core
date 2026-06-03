#!/usr/bin/env python
# coding: utf-8
# pylint: disable=protected-access

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

from openjiuwen.core.foundation.tool import McpServerConfig
from openjiuwen.harness.tools.browser_move.playwright_runtime.config import BrowserRunGuardrails
from openjiuwen.harness.tools.browser_move.playwright_runtime.probes import (
    build_card_probe_js,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime import (
    BrowserAgentRuntime,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime_tools import (
    BrowserProbeCardsTool,
)


def _run(coro):
    return asyncio.run(coro)


def _make_runtime() -> BrowserAgentRuntime:
    mcp_cfg = McpServerConfig(
        server_id="test-playwright-runtime",
        server_name="test-playwright-runtime",
        server_path="stdio://playwright",
        client_type="stdio",
        params={"cwd": str(Path.cwd())},
    )

    return BrowserAgentRuntime(
        provider="openai",
        api_key="test-key",
        api_base="https://example.invalid/v1",
        model_name="test-model",
        mcp_cfg=mcp_cfg,
        guardrails=BrowserRunGuardrails(
            max_steps=3,
            max_failures=1,
            timeout_s=30,
            retry_once=False,
        ),
    )


def test_build_card_probe_js_contains_card_extraction_terms() -> None:
    js = build_card_probe_js(max_cards=10, viewport_only=True)

    assert "max_cards" in js
    assert "viewport_only" in js
    assert "recurring_signatures" in js
    assert "price" in js
    assert "rating" in js
    assert "primary_link" in js
    assert "buttons" in js


def test_build_card_probe_js_clamps_max_cards() -> None:
    js = build_card_probe_js(max_cards=999, viewport_only=True)

    assert '"max_cards": 50' in js


def test_browser_probe_cards_tool_invokes_runtime_api() -> None:
    runtime = _make_runtime()
    runtime.probe_cards = AsyncMock(
        return_value={
            "ok": True,
            "cards": [
                {
                    "id": "card_1",
                    "title": "A Light in the Attic",
                    "price": "£51.77",
                    "selector_hint": "article.product_pod",
                }
            ],
            "error": None,
        }
    )

    tool = BrowserProbeCardsTool(runtime, language="en")

    result = _run(
        tool.invoke(
            {
                "max_cards": 200,
                "viewport_only": "false",
                "include_buttons": "true",
                "query": "attic",
            }
        )
    )

    runtime.probe_cards.assert_called_once_with(
        max_cards=50,
        viewport_only=False,
        include_buttons=True,
        query="attic",
    )
    assert result.success is True
    assert result.data["cards"][0]["title"] == "A Light in the Attic"


def test_browser_probe_cards_tool_reports_runtime_error() -> None:
    runtime = _make_runtime()
    runtime.probe_cards = AsyncMock(
        return_value={
            "ok": False,
            "error": "browser_code_executor_not_ready",
            "cards": [],
        }
    )

    tool = BrowserProbeCardsTool(runtime, language="en")

    result = _run(tool.invoke({}))

    assert result.success is False
    assert result.error == "browser_code_executor_not_ready"
    assert result.data["cards"] == []


def test_runtime_probe_cards_uses_code_executor_and_parses_json() -> None:
    runtime = _make_runtime()
    runtime.ensure_runtime_ready = AsyncMock()
    runtime._code_executor = AsyncMock(
        return_value={
            "content": [
                {
                    "type": "text",
                    "text": '{"ok": true, "url": "https://books.toscrape.com/", "cards": [{"id": "card_1", "title": "Book", "price": "£10.00"}]}',
                }
            ]
        }
    )

    result = _run(
        runtime.probe_cards(
            max_cards=10,
            viewport_only=True,
            include_buttons=True,
            query="book",
        )
    )

    runtime.ensure_runtime_ready.assert_called_once()
    runtime._code_executor.assert_called_once()
    assert result["ok"] is True
    assert result["url"] == "https://books.toscrape.com/"
    assert result["cards"][0]["title"] == "Book"


def test_runtime_probe_cards_handles_missing_code_executor() -> None:
    runtime = _make_runtime()
    runtime.ensure_runtime_ready = AsyncMock()
    runtime._code_executor = None

    result = _run(runtime.probe_cards())

    assert result["ok"] is False
    assert result["error"] == "browser_code_executor_not_ready"
    assert result["cards"] == []


def test_build_card_probe_js_keeps_normalization_simple() -> None:
    js = build_card_probe_js(max_cards=10, viewport_only=True)

    assert "const normalize = (value, limit = 180)" in js
    assert "String(value || '')" in js
    assert "repairCommonEncoding" not in js
    assert "repairCommandEncoding" not in js
    assert "normalizePriceValue" in js


def test_build_card_probe_js_extracts_star_rating_classes() -> None:
    js = build_card_probe_js(max_cards=10, viewport_only=True)

    assert "ratingClassValue" in js
    assert "Five stars" in js
    assert "Three stars" in js
    assert "One star" in js


def test_build_card_probe_js_builds_deeper_selector_hints() -> None:
    js = build_card_probe_js(max_cards=10, viewport_only=True)

    assert "depth < 8" in js
    assert "ol" in js
    assert "ul" in js
    assert "nth-of-type" in js


def test_build_card_probe_js_prefers_better_nested_card_candidate() -> None:
    js = build_card_probe_js(max_cards=10, viewport_only=True)

    assert "conflictsWithExisting" in js
    assert "itemBetter" in js
    assert "selected.splice" in js


def test_build_card_probe_js_does_not_return_repeated_simple_selector_early() -> None:
    js = build_card_probe_js(max_cards=10, viewport_only=True)

    assert "document.querySelectorAll(simple).length === 1" in js
    assert "sameSimple.length === 1" not in js


def test_build_card_probe_js_button_extraction_ignores_viewport_for_card_children() -> None:
    js = build_card_probe_js(max_cards=10, viewport_only=True)

    assert "rect.width < 2 || rect.height < 2" in js
    assert "style.visibility === 'hidden'" in js
    assert "Number(style.opacity) === 0" in js

def test_build_card_probe_js_accepts_site_profiles_and_selector_cache_records() -> None:
    js = build_card_probe_js(
        max_cards=10,
        viewport_only=True,
        site_profiles=[
            {
                "id": "test_shop",
                "domains": ["example.com"],
                "route_patterns": ["^/search"],
                "card_container_selectors": [".product-card"],
                "title_selectors": [".product-title"],
                "price_selectors": [".product-price"],
                "button_selectors": [".add-to-cart"],
            }
        ],
        selector_cache_records=[
            {
                "domain": "example.com",
                "route_signature": "/search",
                "kind": "card_probe",
                "selectors": {
                    "card_container_selectors": [".cached-card"],
                    "title_selectors": [".cached-title"],
                },
            }
        ],
    )

    assert "site_profiles" in js
    assert "selector_cache_records" in js
    assert ".product-card" in js
    assert ".cached-card" in js
    assert "profile_ids" in js
    assert "cache_records_used" in js

def test_runtime_probe_cards_unwraps_result_field_and_records_cache(tmp_path, monkeypatch) -> None:
    from openjiuwen.harness.tools.browser_move.playwright_runtime.site_profiles import (
        BrowserSelectorCache,
    )
    import openjiuwen.harness.tools.browser_move.playwright_runtime.runtime as runtime_module

    runtime = _make_runtime()
    runtime.ensure_runtime_ready = AsyncMock()
    runtime._code_executor = AsyncMock(
        return_value={
            "result": (
                '### Result\n'
                '{"ok": true, '
                '"url": "https://books.toscrape.com/", '
                '"profile_ids": ["books_to_scrape"], '
                '"cache_records_used": 0, '
                '"cards": ['
                '{"id": "card_1", '
                '"title": "A Light in the Attic", '
                '"price": "51.77", '
                '"rating": "Three stars", '
                '"availability": "In stock", '
                '"has_image": true, '
                '"primary_link": "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html", '
                '"text_preview": "A Light in the Attic Three stars 51.77 In stock Add to basket", '
                '"selector_hint": "ol:nth-of-type(1) > li:nth-of-type(1) > article.product_pod:nth-of-type(1)", '
                '"title_selector_hint": "article.product_pod:nth-of-type(1) > h3:nth-of-type(1) > a:nth-of-type(1)", '
                '"price_selector_hint": "article.product_pod:nth-of-type(1) > div.product_price:nth-of-type(2) > p.price_color:nth-of-type(1)", '
                '"rating_selector_hint": "article.product_pod:nth-of-type(1) > p.star-rating:nth-of-type(1)", '
                '"primary_link_selector_hint": "article.product_pod:nth-of-type(1) > h3:nth-of-type(1) > a:nth-of-type(1)", '
                '"buttons": [{"selector_hint": "article.product_pod:nth-of-type(1) > div.product_price:nth-of-type(2) > form:nth-of-type(1) > button.btn.btn-primary:nth-of-type(1)"}]'
                '},'
                '{"id": "card_2", '
                '"title": "Tipping the Velvet", '
                '"price": "53.74", '
                '"rating": "One star", '
                '"availability": "In stock", '
                '"has_image": true, '
                '"primary_link": "https://books.toscrape.com/catalogue/tipping-the-velvet_999/index.html", '
                '"text_preview": "Tipping the Velvet One star 53.74 In stock Add to basket", '
                '"selector_hint": "ol:nth-of-type(1) > li:nth-of-type(2) > article.product_pod:nth-of-type(1)", '
                '"title_selector_hint": "article.product_pod:nth-of-type(1) > h3:nth-of-type(1) > a:nth-of-type(1)", '
                '"price_selector_hint": "article.product_pod:nth-of-type(1) > div.product_price:nth-of-type(2) > p.price_color:nth-of-type(1)", '
                '"rating_selector_hint": "article.product_pod:nth-of-type(1) > p.star-rating:nth-of-type(1)", '
                '"primary_link_selector_hint": "article.product_pod:nth-of-type(1) > h3:nth-of-type(1) > a:nth-of-type(1)", '
                '"buttons": [{"selector_hint": "article.product_pod:nth-of-type(1) > div.product_price:nth-of-type(2) > form:nth-of-type(1) > button.btn.btn-primary:nth-of-type(1)"}]'
                '}]}'
            )
        }
    )

    cache = BrowserSelectorCache(tmp_path / "selector_cache.json")
    monkeypatch.setattr(runtime_module, "get_selector_cache", lambda: cache)

    result = _run(runtime.probe_cards())

    assert result["ok"] is True
    assert result["cards"][0]["title"] == "A Light in the Attic"

    exported = cache.export_for_probe()
    assert len(exported) == 1
    assert exported[0]["domain"] == "books.toscrape.com"
    assert exported[0]["success_count"] == 1
    assert exported[0]["quality_score"] > 0

def test_runtime_unwrap_mcp_result_field() -> None:
    runtime = _make_runtime()

    raw = {
        "result": '### Result\n{"ok": true, "cards": []}'
    }

    assert runtime._unwrap_mcp_text_result(raw) == '### Result\n{"ok": true, "cards": []}'

def test_build_card_probe_js_has_cache_first_diagnostics() -> None:
    js = build_card_probe_js(
        max_cards=10,
        viewport_only=True,
        selector_cache_records=[
            {
                "domain": "example.com",
                "route_signature": "/search",
                "kind": "card_probe",
                "selectors": {
                    "card_container_selectors": [".cached-card"],
                    "title_selectors": [".cached-title"],
                },
                "quality_score": 0.9,
            }
        ],
    )

    assert "cacheSelectors" in js
    assert "siteProfileSelectors" in js
    assert "cachedCandidates" in js
    assert "hasEnoughGoodCards(cachedCandidates)" in js
    assert "selectorSource = 'cache'" in js
    assert "cache_accepted" in js
    assert "selector_source" in js
    assert "cached_container_selectors" in js


def test_build_card_probe_js_filters_page_chrome_candidates() -> None:
    js = build_card_probe_js(max_cards=10, viewport_only=True)

    assert "looksLikePageChrome" in js
    assert "fresh & fast" in js.lower()
    assert "breadcrumb" in js
    assert "navbar" in js


def test_build_card_probe_js_uses_selectable_not_raw_scored() -> None:
    js = build_card_probe_js(max_cards=10, viewport_only=True)

    assert "const selectable = scored.filter" in js
    assert "for (const item of selectable)" in js

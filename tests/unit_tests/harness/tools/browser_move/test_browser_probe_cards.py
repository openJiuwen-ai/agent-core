#!/usr/bin/env python
# coding: utf-8

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from openjiuwen.core.foundation.tool import McpServerConfig
from openjiuwen.harness.tools.browser_move.playwright_runtime.config import BrowserRunGuardrails
from openjiuwen.harness.tools.browser_move.playwright_runtime.page_structure_index import (
    build_page_index_configuration,
    build_page_index_configure_js,
    build_page_index_install_js,
)
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


def test_build_card_probe_js_contains_group_first_extraction_terms() -> None:
    install_js = build_page_index_install_js()
    query_js = build_card_probe_js(max_cards=10, viewport_only=True)

    assert "discoverRepeatedGroups" in install_js
    assert "inferGroupSchema" in install_js
    assert "chooseRepresentativeIds" in install_js
    assert "relativePath" in install_js
    assert "resolveRelativePath" in install_js
    assert "recurring_signatures" in install_js
    assert "representatives_parsed" in install_js
    assert "schema_reused" in install_js
    assert "primary_link" in install_js
    assert "buttons" in install_js
    assert "page_index_runtime_missing" in query_js
    assert "discoverRepeatedGroups" not in query_js


def test_build_card_probe_js_clamps_max_cards() -> None:
    js = build_card_probe_js(max_cards=999, viewport_only=True)

    assert '"max_cards":50' in js


def test_build_card_probe_js_defaults_to_compact_diagnostics() -> None:
    js = build_card_probe_js()

    assert '"diagnostics_level":"compact"' in js
    assert len(js) < 1500


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
        diagnostics_level="compact",
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
    runtime._page_index_runtime_installed = True
    runtime._ensure_page_index_configuration = AsyncMock()
    runtime._code_executor = AsyncMock(
        return_value={
            "content": [
                {
                    "type": "text",
                    "text": (
                        '{"ok": true, "url": "https://books.toscrape.com/", '  # noqa: E501
                        '"cards": [{"id": "card_1", "title": "Book", '  # noqa: E501
                        '"price": "£10.00"}]}'
                    ),
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


def test_runtime_probe_cards_uses_internal_cache_samples_but_hides_them(monkeypatch) -> None:
    import openjiuwen.harness.tools.browser_move.playwright_runtime.runtime as runtime_module

    runtime = _make_runtime()
    runtime.ensure_runtime_ready = AsyncMock()
    runtime._page_index_runtime_installed = True
    runtime._ensure_page_index_configuration = AsyncMock()
    runtime._code_executor = AsyncMock(
        return_value={
            "ok": True,
            "url": "https://example.com/",
            "cards": [{"id": "compact", "title": "Compact"}],
            "_cache_cards": [
                {
                    "id": "cache",
                    "title": "Cache sample title",
                    "primary_link": "https://example.com/item",
                    "text_preview": "Cache sample title with enough repeated item context",
                    "selector_hint": "article.card",
                    "title_selector_hint": "article.card h3 a",
                }
            ],
        }
    )
    cache = MagicMock()
    cache.export_for_probe.return_value = []
    monkeypatch.setattr(runtime_module, "get_selector_cache", lambda: cache)

    result = _run(runtime.probe_cards())

    assert result["cards"][0]["id"] == "compact"
    assert "_cache_cards" not in result
    recorded = cache.record_card_probe_result.call_args.args[0]
    assert recorded["cards"][0]["id"] == "cache"


def test_runtime_probe_cards_handles_missing_code_executor() -> None:
    runtime = _make_runtime()
    runtime.ensure_runtime_ready = AsyncMock()
    runtime._code_executor = None

    result = _run(runtime.probe_cards())

    assert result["ok"] is False
    assert result["error"] == "browser_code_executor_not_ready"
    assert result["cards"] == []


def test_build_card_probe_js_uses_bottom_up_text_aggregation() -> None:
    js = build_page_index_install_js()

    assert "for (let index = nodes.length - 1; index >= 0; index -= 1)" in js
    assert "node.aggregateText = normalize(textParts.join(' '), MAX_TEXT)" in js
    assert "normalizePriceValue" in js
    assert "repairCommonEncoding" not in js
    assert "repairCommandEncoding" not in js


def test_build_card_probe_js_extracts_star_rating_classes() -> None:
    js = build_page_index_install_js()

    assert "ratingClassValue" in js
    assert "Five stars" in js
    assert "Three stars" in js
    assert "One star" in js


def test_build_card_probe_js_builds_stable_selector_hints() -> None:
    js = build_page_index_install_js()

    assert "depth < 8" in js
    assert "data-testid" in js
    assert "nth-of-type" in js


def test_build_card_probe_js_suppresses_overlapping_repeated_groups() -> None:
    js = build_page_index_install_js()

    assert "groupsOverlap" in js
    assert "ratio >= 0.65" in js
    assert "conflicting" in js


def test_build_card_probe_js_does_not_use_broad_div_candidate_scan() -> None:
    js = build_page_index_install_js()

    assert "document.querySelectorAll('div')" not in js
    assert "selectors.join(',')" not in js
    assert "document.createTreeWalker" in js


def test_build_card_probe_js_extracts_buttons_from_compiled_paths() -> None:
    js = build_page_index_install_js()

    assert "schema.buttonPaths" in js
    assert "resolveRelativePath(index, rootId, path)" in js
    assert "if (!node || !node.interactive) continue" in js


def test_card_probe_configuration_contains_profiles_and_cache_records() -> None:
    configuration = build_page_index_configuration(
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
    configure_js = build_page_index_configure_js(configuration)
    query_js = build_card_probe_js(
        max_cards=10,
        viewport_only=True,
        configuration_revision=configuration["revision"],
    )

    assert '"site_profiles"' in configure_js
    assert '"selector_cache_records"' in configure_js
    assert ".product-card" in configure_js
    assert ".cached-card" in configure_js
    assert ".product-card" not in query_js
    assert ".cached-card" not in query_js
    assert "matchingProfiles" in build_page_index_install_js()
    assert "matchingCacheRecords" in build_page_index_install_js()



def test_runtime_probe_cards_unwraps_result_field_and_records_cache(tmp_path, monkeypatch) -> None:
    from openjiuwen.harness.tools.browser_move.playwright_runtime.site_profiles import (
        BrowserSelectorCache,
    )
    import openjiuwen.harness.tools.browser_move.playwright_runtime.runtime as runtime_module

    runtime = _make_runtime()
    runtime.ensure_runtime_ready = AsyncMock()
    runtime._page_index_runtime_installed = True
    runtime._ensure_page_index_configuration = AsyncMock()
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
                '"price_selector_hint": "article.product_pod:nth-of-type(1) > '
                'div.product_price:nth-of-type(2) > p.price_color:nth-of-type(1)", '
                '"rating_selector_hint": "article.product_pod:nth-of-type(1) > p.star-rating:nth-of-type(1)", '
                '"primary_link_selector_hint": "article.product_pod:nth-of-type(1) > '
                'h3:nth-of-type(1) > a:nth-of-type(1)", '
                '"buttons": [{"selector_hint": "article.product_pod:nth-of-type(1) > '
                'div.product_price:nth-of-type(2) > form:nth-of-type(1) > '
                'button.btn.btn-primary:nth-of-type(1)"}]'
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
                '"price_selector_hint": "article.product_pod:nth-of-type(1) > '
                'div.product_price:nth-of-type(2) > p.price_color:nth-of-type(1)", '
                '"rating_selector_hint": "article.product_pod:nth-of-type(1) > p.star-rating:nth-of-type(1)", '
                '"primary_link_selector_hint": "article.product_pod:nth-of-type(1) > '
                'h3:nth-of-type(1) > a:nth-of-type(1)", '
                '"buttons": [{"selector_hint": "article.product_pod:nth-of-type(1) > '
                'div.product_price:nth-of-type(2) > form:nth-of-type(1) > '
                'button.btn.btn-primary:nth-of-type(1)"}]'
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
    install_js = build_page_index_install_js()
    configuration = build_page_index_configuration(
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
        ]
    )
    js = build_card_probe_js(
        max_cards=10,
        viewport_only=True,
        configuration_revision=configuration["revision"],
    )

    assert "cacheSelectors" in install_js
    assert "cachedCandidates" in install_js
    assert "hasEnoughGoodCards" in install_js
    cache_add = install_js.index("if (cacheAccepted) addCandidateGroup(cachedGroup)")
    profile_add = install_js.index("addCandidateGroup(profileGroup)")
    repeated_add = install_js.index("for (const item of repeatedGroups)")
    assert cache_add < profile_add < repeated_add
    assert "selectorSource: selected ? selected.group.source : candidateGroups[0].source" in install_js
    assert "cache_accepted" in install_js
    assert "cache_rejection_reason" in install_js
    assert "cache_candidate_count" in install_js
    assert "cache_good_candidate_count" in install_js
    assert "selector_source" in install_js
    assert "cached_container_selectors" in install_js
    assert "record.kind || 'card_probe'" in install_js


def test_build_card_probe_js_filters_page_chrome_groups() -> None:
    js = build_page_index_install_js()

    assert "looksLikePageChrome" in js
    assert "CHROME_ROLES" in js
    assert "breadcrumb" in js
    assert "navbar" in js


def test_build_card_probe_js_ranks_groups_before_parsing_members() -> None:
    js = build_page_index_install_js()

    assert "candidates.sort((left, right) => right.score - left.score)" in js
    query_cards_start = js.index("const queryCards")
    group_ranking = js.index("const repeatedGroups = index.groups", query_cards_start)
    bounded_loop = js.index("candidateGroups.slice(0, MAX_GROUP_ATTEMPTS)", query_cards_start)
    schema_inference = js.index("schema = inferGroupSchema", query_cards_start)
    assert group_ranking < bounded_loop < schema_inference
    assert "queryMatches: groupQueryMatchCount" in js


def test_build_card_probe_js_groups_all_repeated_sibling_shapes_without_tag_lists() -> None:
    js = build_page_index_install_js()

    assert "childrenByParent" in js
    assert "exactBuckets" in js
    assert "shapeBuckets" in js
    assert "node.structuralSignature" in js
    assert "node.shapeSignature" in js
    assert "tbody > tr" not in js
    assert "[class*=\"row\" i]" not in js


def test_build_card_probe_js_extracts_metadata_through_inferred_schema() -> None:
    js = build_page_index_install_js()

    assert "fieldScore" in js
    assert "bestFieldNode" in js
    assert "schema.fields[field]" in js
    assert "author_selector_hint" in js
    assert "source_selector_hint" in js
    assert "summary_selector_hint" in js
    assert "isAuthorProfile" in js
    assert "isArticleHref" in js


def test_runtime_probe_cards_records_rejected_cache_attempt(tmp_path, monkeypatch) -> None:
    import json
    from openjiuwen.harness.tools.browser_move.playwright_runtime.site_profiles import (
        BrowserSelectorCache,
    )
    import openjiuwen.harness.tools.browser_move.playwright_runtime.runtime as runtime_module

    cache_path = tmp_path / "selector_cache.json"
    cache_path.write_text(
        json.dumps(
            {
                "version": 1,
                "records": [
                    {
                        "domain": "example.com",
                        "route_signature": "/search",
                        "kind": "card_probe",
                        "selectors": {"card_container_selectors": [".old-card"]},
                        "success_count": 2,
                        "failure_count": 0,
                        "quality_score": 0.8,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    cache = BrowserSelectorCache(cache_path)
    monkeypatch.setattr(runtime_module, "get_selector_cache", lambda: cache)

    runtime = _make_runtime()
    runtime.ensure_runtime_ready = AsyncMock()
    runtime._page_index_runtime_installed = True
    runtime._ensure_page_index_configuration = AsyncMock()
    runtime._code_executor = AsyncMock(
        return_value={
            "content": [
                {
                    "type": "text",
                    "text": (
                        '{"ok": true, '
                        '"url": "https://example.com/search?q=mouse", '
                        '"cache_records_used": 1, '
                        '"cache_accepted": false, '
                        '"cache_rejection_reason": "cache_validation_failed", '
                        '"selector_source": "generic", '
                        '"cards": []}'
                    ),
                }
            ]
        }
    )

    result = _run(runtime.probe_cards())

    assert result["ok"] is True
    exported = cache.export_for_probe()
    assert exported[0]["failure_count"] == 1
    assert exported[0]["last_failure_reason"] == "cache_validation_failed"


def test_browser_probe_cards_prioritizes_article_links_over_author_links() -> None:
    js = build_page_index_install_js()

    assert "isAuthorProfile" in js
    assert "isArticleHref" in js
    assert "if (isAuthorProfile(node)) score -= 100" in js
    assert "if (node.isHeading || isArticleHref(node.href)) score += 45" in js

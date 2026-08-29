#!/usr/bin/env python
# coding: utf-8
"""Regression tests for the PageState-to-Batch target contract."""
# pylint: disable=protected-access

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openjiuwen.harness.tools.browser_move.playwright_runtime.page_state import (
    BrowserPageState,
)
from openjiuwen.harness.tools.browser_move.playwright_runtime.runtime import (
    BrowserAgentRuntime,
)


def _run(coro):
    return asyncio.run(coro)


def _make_bare_runtime() -> BrowserAgentRuntime:
    runtime = BrowserAgentRuntime.__new__(BrowserAgentRuntime)
    runtime._page_generation = 0
    runtime._reference_generations = {}
    runtime._selector_primary_links = {}
    runtime._last_observed_url = ""
    return runtime


def _interactive(selector: str, text: str) -> dict:
    return {
        "role": "button",
        "text": text,
        "selector_hint": selector,
        "selector_hint_validated": True,
        "match_count": 1,
        "visible": True,
        "enabled": True,
        "actionable": True,
        "clickable": True,
    }


def test_probe_targets_and_compact_page_state_share_one_contract() -> None:
    state = BrowserPageState(page_id="page-test")
    payload = {
        "url": "https://example.test/search",
        "title": "Search",
        "elements": [
            _interactive("#query", "Search"),
            {
                "role": "tab",
                "text": "Sales",
                "selector_hint": "text=Sales",
                "selector_hint_validated": False,
                "match_count": 2,
                "visible": True,
                "enabled": True,
                "actionable": False,
                "clickable": False,
            },
        ],
    }

    state.register_interactives(payload)

    safe_target_id = payload["elements"][0]["target_id"]
    assert "target_id" not in payload["elements"][1]
    assert payload["elements"][0]["generation_id"] == "g0"
    assert state.export() == {
        "page_id": "page-test",
        "generation_id": "g0",
        "url": "https://example.test/search",
        "title": "Search",
        "interactives": [
            {
                "target_id": safe_target_id,
                "generation_id": "g0",
                "role": "button",
                "text": "Search",
                "match_count": 1,
                "visible": True,
                "enabled": True,
                "actionable": True,
                "clickable": True,
            }
        ],
        "cards": [],
        "field_coverage": [],
        "blockers": [],
    }
    resolved = state.resolve_target(generation_id="g0", target_id=safe_target_id)
    assert resolved is not None
    assert resolved.locator == {"selector": "#query"}


def test_cards_add_primary_link_and_field_coverage_to_page_state() -> None:
    state = BrowserPageState(page_id="page-results", generation=4)
    payload = {
        "url": "https://shop.example.test/results",
        "title": "Headphones",
        "cards": [
            {
                "title": "Bluetooth Headphones",
                "price": "$99",
                "source": "Example Shop",
                "summary": "Low-latency wireless headphones",
                "primary_link": "https://shop.example.test/item/1",
                "selector_hint": ".card:nth-of-type(1)",
                "selector_hint_validated": True,
                "match_count": 1,
                "visible": True,
                "enabled": True,
                "actionable": False,
                "clickable": False,
            }
        ],
    }

    state.register_cards(payload)

    card = payload["cards"][0]
    exported = state.export()
    assert card["generation_id"] == "g4"
    assert exported["cards"][0]["target_id"] == card["target_id"]
    assert exported["cards"][0]["href"] == "https://shop.example.test/item/1"
    assert exported["field_coverage"] == ["price", "primary_link", "source", "summary", "title"]


def test_card_detail_fields_register_generation_scoped_read_only_targets() -> None:
    state = BrowserPageState(page_id="page-detail", generation=2)
    payload = {
        "cards": [
            {
                "title": "Article",
                "author": "Alice",
                "likes": None,
                "field_status": {"author": "present", "likes": "missing"},
                "field_provenance": {
                    "author": {
                        "selector": ".article-author",
                        "raw_text": "Alice",
                        "generation_id": "g2",
                        "source": "browser_probe_cards",
                    }
                },
                "selector_metadata": {
                    "author": {
                        "selector_hint": ".article-author",
                        "match_count": 1,
                        "visible": True,
                        "enabled": True,
                        "generation_id": "g2",
                    }
                },
            }
        ]
    }

    state.register_cards(payload)

    exported_card = state.export()["cards"][0]
    author_target = exported_card["field_targets"]["author"]
    assert author_target["generation_id"] == "g2"
    assert author_target["field"] == "author"
    assert author_target["extractable"] is True
    assert author_target["read_only"] is True
    assert author_target["actionable"] is False
    resolved = state.resolve_target(generation_id="g2", target_id=author_target["target_id"])
    assert resolved is not None
    assert resolved.locator == {"selector": ".article-author"}
    assert exported_card["field_status"]["likes"] == "missing"


def test_page_state_preserves_probe_region_kind_and_dynamic_target_semantics() -> None:
    state = BrowserPageState(page_id="page-semantic")
    dynamic = _interactive("[data-date='2026-08-09']", "9")
    dynamic.update({"region": "main", "kind": "calendar_date"})
    state.register_interactives({"elements": [dynamic]})
    state.register_cards(
        {
            "cards": [
                {
                    "region": "main_result",
                    "kind": "product",
                    "result_index": 1,
                    "is_ad": False,
                    "title": "Bluetooth Headphones",
                    "primary_link": "https://item.taobao.com/item.htm?id=1",
                }
            ]
        }
    )

    exported = state.export()
    assert exported["interactives"][0]["kind"] == "calendar_date"
    assert exported["interactives"][0]["region"] == "main"
    assert exported["cards"][0]["region"] == "main_result"
    assert exported["cards"][0]["kind"] == "product"
    assert exported["cards"][0]["result_index"] == 1
    assert exported["cards"][0]["is_ad"] is False


def test_page_state_blockers_do_not_treat_a_login_link_as_login_required() -> None:
    state = BrowserPageState(page_id="page-login")
    state.register_interactives(
        {
            "title": "Public results",
            "elements": [_interactive("#login", "Login")],
        }
    )
    assert state.export()["blockers"] == []

    state.register_interactives(
        {
            "title": "Please sign in to continue",
            "elements": [],
        }
    )
    assert state.export()["blockers"] == ["login_required"]


def test_navigation_hard_invalidates_ax_refs_targets_and_selectors() -> None:
    state = BrowserPageState(page_id="page-ax")
    registered = state.register_ax_snapshot('- tab "Sales" [ref=f1e174]')
    state.register_interactives({"elements": [_interactive("#query", "Search")]})
    target_id = state.export()["interactives"][-1]["target_id"]

    assert registered == ("f1e174",)
    assert state.resolve_target(generation_id="g0", ref="f1e174") is not None
    assert state.resolve_target(generation_id="g0", selector="#query") is not None

    state.advance(url="https://example.test/results")

    with pytest.raises(ValueError, match="Stale PageState target|Stale target_id"):
        state.resolve_target(generation_id="g1", target_id=target_id)
    with pytest.raises(ValueError, match="Stale AX ref"):
        state.resolve_target(generation_id="g1", ref="f1e174")
    with pytest.raises(ValueError, match="Stale selector"):
        state.resolve_target(generation_id="g1", selector="#query")


def test_ax_ref_reused_after_navigation_gets_a_new_current_target() -> None:
    state = BrowserPageState(page_id="page-ref-reuse")
    state.register_ax_snapshot('- button "Old action" [ref=e1]')
    old_target_id = state.export()["interactives"][0]["target_id"]

    state.advance(url="https://example.test/new-page")
    state.register_ax_snapshot('- button "New action" [ref=e1]')

    new_target = state.resolve_target(generation_id="g1", ref="e1")
    assert new_target is not None
    assert new_target.generation_id == "g1"
    assert new_target.target_id != old_target_id


def test_stale_probe_target_refreshes_only_to_unique_current_equivalent() -> None:
    state = BrowserPageState(page_id="page-refresh")
    first = _interactive("[data-date='2026-08-26']", "26")
    first.update({"kind": "calendar_date", "region": "hotel_search"})
    state.register_interactives({"elements": [first]})
    stale_target_id = first["target_id"]

    state.advance(url="https://hotels.ctrip.com/list")
    current = _interactive("[data-date='2026-08-26']", "26")
    current.update({"kind": "calendar_date", "region": "hotel_search"})
    state.register_interactives({"elements": [current]})

    assert state.refresh_target_id(stale_target_id) == current["target_id"]


def test_stale_ax_target_and_ambiguous_probe_target_cannot_refresh() -> None:
    state = BrowserPageState(page_id="page-refresh-reject")
    state.register_ax_snapshot('- button "Continue" [ref=e1]')
    stale_ax_target = state.export()["interactives"][0]["target_id"]
    old = _interactive(".rating", "5 stars")
    old.update({"kind": "rating_filter", "region": "main"})
    state.register_interactives({"elements": [old]})
    stale_probe_target = old["target_id"]

    state.advance(url="https://example.test/results")
    first = _interactive("#rating-a", "5 stars")
    second = _interactive("#rating-b", "5 stars")
    for target in (first, second):
        target.update({"kind": "rating_filter", "region": "main"})
    state.register_interactives({"elements": [first, second]})

    with pytest.raises(ValueError, match="Stale AX target_id"):
        state.refresh_target_id(stale_ax_target)
    with pytest.raises(ValueError, match="ambiguous"):
        state.refresh_target_id(stale_probe_target)


def test_runtime_refreshes_stale_batch_target_id_without_accepting_stale_ref() -> None:
    runtime = _make_bare_runtime()
    old = _interactive("#sort-price", "Price")
    old.update({"kind": "sort_tab", "region": "main"})
    runtime._ensure_page_state().register_interactives({"elements": [old]})

    runtime._ensure_page_state().advance(url="https://example.test/results?sort=price")
    current = _interactive("#sort-price", "Price")
    current.update({"kind": "sort_tab", "region": "main"})
    runtime._ensure_page_state().register_interactives({"elements": [current]})

    refreshed, recovered_from = _run(
        runtime._refresh_stale_batch_targets(
            [{"op": "click", "target_id": old["target_id"]}],
            generation_id="g0",
        )
    )
    assert refreshed[0]["target_id"] == current["target_id"]
    assert recovered_from == "g0"

    with pytest.raises(ValueError, match="AX refs cannot be refreshed"):
        _run(
            runtime._refresh_stale_batch_targets(
                [{"op": "click", "ref": "e1"}],
                generation_id="g0",
            )
        )


def test_stale_generation_condition_waits_refresh_without_reusing_action_selector() -> None:
    runtime = _make_bare_runtime()
    runtime._ensure_page_state().advance(url="https://example.test/results")

    refreshed, recovered_from = _run(
        runtime._refresh_stale_batch_targets(
            [
                {"op": "wait_for_url", "url_contains": "/results"},
                {"op": "wait_for_selector", "selector": ".result-card"},
            ],
            generation_id="g0",
        )
    )
    assert len(refreshed) == 2
    assert recovered_from == "g0"

    with pytest.raises(ValueError, match="Model-authored selectors cannot refresh"):
        _run(
            runtime._refresh_stale_batch_targets(
                [{"op": "extract_text", "selector": ".result-card", "field": "title"}],
                generation_id="g0",
            )
        )


def test_runtime_rebinds_stale_probe_target_when_dom_identity_is_still_unique() -> None:
    runtime = _make_bare_runtime()
    old = _interactive("#sort-price", "Price")
    runtime._ensure_page_state().register_interactives({"elements": [old]})
    runtime._ensure_page_state().advance(url="https://example.test/results")
    runtime.ensure_runtime_ready = AsyncMock()
    runtime._call_playwright_run_code_unsafe = AsyncMock(
        return_value=('{"ok":true,"match_count":1,"visible":true,"enabled":true,"actionable":true}')
    )

    refreshed, recovered_from = _run(
        runtime._refresh_stale_batch_targets(
            [{"op": "click", "target_id": old["target_id"]}],
            generation_id="g0",
        )
    )

    refreshed_id = refreshed[0]["target_id"]
    assert recovered_from == "g0"
    assert refreshed_id != old["target_id"]
    rebound = runtime._ensure_page_state().resolve_target(
        generation_id="g1",
        target_id=refreshed_id,
    )
    assert rebound is not None
    assert rebound.selector == "#sort-price"


def test_compact_page_state_never_exposes_probe_local_id_or_ax_ref() -> None:
    state = BrowserPageState(page_id="page-compact")
    payload = {"elements": [_interactive("#query", "Search")]}
    payload["elements"][0]["id"] = "local-probe-id"
    state.register_interactives(payload)
    state.register_ax_snapshot('textbox "Search" [ref=f1e2]')

    serialized = str(state.export())

    assert "local-probe-id" not in serialized
    assert "f1e2" not in serialized
    assert "target_id" in serialized


def test_failed_stale_target_refresh_returns_current_generation_and_candidates() -> None:
    runtime = _make_bare_runtime()
    old = _interactive("#sort-price", "Price")
    runtime._ensure_page_state().register_interactives({"elements": [old]})
    runtime._ensure_page_state().advance(url="https://example.test/results")
    runtime.ensure_runtime_ready = AsyncMock()
    runtime._call_playwright_run_code_unsafe = AsyncMock(
        return_value=('{"ok":true,"match_count":2,"visible":true,"enabled":true,"actionable":true}')
    )

    result = _run(
        runtime.batch_interact(
            steps=[{"op": "click", "target_id": old["target_id"]}],
            generation_id="g0",
        )
    )

    assert result["ok"] is False
    assert result["current_generation"] == "g1"
    assert result["generation_id"] == "g1"
    assert result["candidate_fresh_targets"] == []


def test_replacing_ax_snapshot_preserves_present_refs_and_removes_missing_refs() -> None:
    state = BrowserPageState(page_id="page-ref-replacement")
    state.replace_ax_snapshot(
        "\n".join(
            [
                '- textbox "Email" [ref=e1]',
                '- button "Continue" [ref=e2]',
            ]
        )
    )
    email_target = state.resolve_target(generation_id="g0", ref="e1")
    assert email_target is not None

    state.replace_ax_snapshot(
        "\n".join(
            [
                '- textbox "Email" [ref=e1]',
                '- button "Submit" [ref=e3]',
            ]
        )
    )

    assert state.resolve_target(generation_id="g0", ref="e1") is email_target
    assert state.resolve_target(generation_id="g0", ref="e3") is not None
    with pytest.raises(ValueError, match="Unknown AX ref: e2"):
        state.resolve_target(generation_id="g0", ref="e2")


def test_runtime_resolves_probe_target_without_model_generated_css() -> None:
    runtime = _make_bare_runtime()
    payload = {
        "elements": [
            _interactive("#from", "From"),
            _interactive("[role=option]:nth-of-type(2)", "Shanghai (SHA)"),
        ]
    }
    runtime._ensure_page_state().register_interactives(payload)

    resolved = _run(
        runtime._resolve_batch_steps(
            [
                {
                    "op": "autocomplete",
                    "target_id": payload["elements"][0]["target_id"],
                    "value": "Shanghai",
                    "option_target_id": payload["elements"][1]["target_id"],
                },
                {"op": "press", "key": "Enter"},
            ],
            generation_id="g0",
        )
    )

    assert resolved[0]["selector"] == "#from"
    assert resolved[0]["option_selector"] == "[role=option]:nth-of-type(2)"
    assert "target_id" not in resolved[0]
    assert "option_target_id" not in resolved[0]


def test_runtime_rejects_guessed_action_selector_before_executor() -> None:
    runtime = _make_bare_runtime()

    with pytest.raises(ValueError, match="Unregistered action selector"):
        _run(
            runtime._resolve_batch_steps(
                [
                    {"op": "click", "selector": "ul:nth-of-type(1) > li:nth-of-type(2)"},
                    {"op": "press", "key": "Enter"},
                ],
                generation_id="g0",
            )
        )


def test_runtime_rejects_invalid_batch_before_starting_browser() -> None:
    runtime = _make_bare_runtime()
    runtime.ensure_runtime_ready = AsyncMock()

    result = _run(
        runtime.batch_interact(
            steps=[{"selector": "#missing-op"}],
            generation_id="g0",
        )
    )

    assert result["ok"] is False
    assert result["status"] == "failed"
    assert "unsupported" in result["error"]
    runtime.ensure_runtime_ready.assert_not_awaited()


def test_runtime_rewrites_single_target_click_to_official_primitive() -> None:
    runtime = _make_bare_runtime()
    runtime.ensure_runtime_ready = AsyncMock()
    payload = {
        "url": "https://example.test/search",
        "title": "Search",
        "elements": [_interactive("#search", "Search")],
    }
    runtime._ensure_page_state().register_interactives(payload)
    click_tool = SimpleNamespace(
        invoke=AsyncMock(
            return_value=SimpleNamespace(
                success=True,
                error=None,
                data={"page": {"url": payload["url"], "title": payload["title"]}},
            )
        )
    )
    runtime._get_playwright_mcp_tool = AsyncMock(return_value=click_tool)

    result = _run(
        runtime.batch_interact(
            steps=[{"op": "click", "target_id": payload["elements"][0]["target_id"]}],
            generation_id="g0",
        )
    )

    assert result["ok"] is True
    assert result["execution_mode"] == "primitive"
    assert result["metrics"]["tool_name"] == "browser_click"
    assert result["steps"] == [
        {
            "index": 0,
            "op": "click",
            "ok": True,
            "status": "completed",
            "elapsed_ms": result["steps"][0]["elapsed_ms"],
        }
    ]
    assert "_runtime_page" not in result
    runtime._get_playwright_mcp_tool.assert_awaited_once_with("browser_click")
    assert click_tool.invoke.await_args.args[0]["target"] == "#search"


def test_runtime_rewrites_single_card_click_to_direct_navigation() -> None:
    runtime = _make_bare_runtime()
    runtime.ensure_runtime_ready = AsyncMock()
    payload = {
        "cards": [
            {
                "title": "Result",
                "primary_link": "https://example.test/result/1",
                "selector_hint": ".card",
                "selector_hint_validated": True,
                "match_count": 1,
                "visible": True,
                "enabled": True,
                "actionable": True,
                "clickable": True,
            }
        ]
    }
    runtime._ensure_page_state().register_cards(payload)
    navigate_tool = SimpleNamespace(invoke=AsyncMock(return_value=SimpleNamespace(success=True, error=None, data={})))
    runtime._get_playwright_mcp_tool = AsyncMock(return_value=navigate_tool)

    result = _run(
        runtime.batch_interact(
            steps=[{"op": "click", "target_id": payload["cards"][0]["target_id"]}],
            generation_id="g0",
        )
    )

    assert result["ok"] is True
    assert result["metrics"]["tool_name"] == "browser_navigate"
    assert result["generation_id"] == "g1"
    assert result["page_state"]["url"] == "https://example.test/result/1"
    navigate_tool.invoke.assert_awaited_once_with({"url": "https://example.test/result/1"})


def test_single_step_uses_compact_rpc_fallback_when_primitive_is_unavailable() -> None:
    runtime = _make_bare_runtime()
    runtime._get_playwright_mcp_tool = AsyncMock(side_effect=RuntimeError("not registered"))

    result = _run(runtime._run_single_batch_primitive({"op": "click", "selector": "#search"}))

    assert result is None


def test_runtime_materializes_native_ax_ref_inside_runtime() -> None:
    runtime = _make_bare_runtime()
    runtime._ensure_page_state().register_ax_snapshot('- tab "Sales" [ref=f1e174]')
    evaluate_tool = SimpleNamespace(invoke=AsyncMock(return_value=SimpleNamespace(success=True, error=None)))
    runtime._get_playwright_mcp_tool = AsyncMock(return_value=evaluate_tool)

    resolved = _run(
        runtime._resolve_batch_steps(
            [
                {"op": "click", "ref": "f1e174"},
                {"op": "press", "key": "Enter"},
            ],
            generation_id="g0",
        )
    )

    assert resolved[0]["selector"].startswith('[data-openjiuwen-target-id="t_g0_')
    assert "ref" not in resolved[0]
    runtime._get_playwright_mcp_tool.assert_awaited_once_with("browser_evaluate")
    assert evaluate_tool.invoke.await_args.args[0]["target"] == "f1e174"


def test_card_primary_link_target_requires_direct_navigation() -> None:
    runtime = _make_bare_runtime()
    payload = {
        "cards": [
            {
                "title": "Result",
                "primary_link": "https://example.test/result/1",
                "selector_hint": ".card:nth-of-type(1)",
                "selector_hint_validated": True,
                "match_count": 1,
                "visible": True,
                "enabled": True,
                "actionable": True,
                "clickable": True,
            }
        ]
    }
    runtime._ensure_page_state().register_cards(payload)

    with pytest.raises(ValueError, match="browser_navigate"):
        _run(
            runtime._resolve_batch_steps(
                [
                    {"op": "click", "target_id": payload["cards"][0]["target_id"]},
                    {"op": "press", "key": "Enter"},
                ],
                generation_id="g0",
            )
        )


def test_runtime_reset_invalidates_current_page_state() -> None:
    runtime = _make_bare_runtime()
    runtime._service = SimpleNamespace(reset=AsyncMock())
    payload = {"elements": [_interactive("#query", "Search")]}
    runtime._ensure_page_state().register_interactives(payload)
    target_id = payload["elements"][0]["target_id"]

    _run(runtime.reset())

    runtime._service.reset.assert_awaited_once()
    assert runtime.generation_id == "g1"
    with pytest.raises(ValueError, match="Stale target_id"):
        runtime._ensure_page_state().resolve_target(
            generation_id="g1",
            target_id=target_id,
        )


def test_runtime_reset_failure_still_invalidates_current_page_state() -> None:
    runtime = _make_bare_runtime()
    runtime._service = SimpleNamespace(reset=AsyncMock(side_effect=RuntimeError("reset failed")))

    with pytest.raises(RuntimeError, match="reset failed"):
        _run(runtime.reset())

    assert runtime.generation_id == "g1"

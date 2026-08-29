#!/usr/bin/env python
# coding: utf-8

from __future__ import annotations

from openjiuwen.harness.tools.browser_move.playwright_runtime.semantic_state import (
    SemanticStateTracker,
    build_semantic_state,
)


def _state(*, price: str = "", result_count: int = 10, fields: list[str] | None = None) -> dict:
    filters = [{"key": "price", "value": price}] if price else []
    return {
        "url": "https://shop.example/search?q=headphones&utm=x",
        "form_values": [{"key": "query", "value": "headphones"}],
        "selected_filters": filters,
        "result_count": result_count,
        "field_coverage": fields or [],
    }


def test_build_semantic_state_is_stable_and_ignores_non_semantic_fields() -> None:
    first = build_semantic_state(
        {
            "url": "https://EXAMPLE.test/search?b=2&a=1#results",
            "form_values": [{"name": "Query", "value": " OpenJiuwen "}],
            "selected_filters": [{"label": "Sort", "text": "Price"}],
            "result_count": "20",
            "field_coverage": ["Title", "price"],
            "selector": "#unstable-g8",
            "generation_id": "g8",
        }
    )

    assert first == {
        "url": "https://example.test/search?a=1&b=2",
        "form_values": [{"key": "query", "value": "OpenJiuwen"}],
        "selected_filters": [{"key": "sort", "value": "Price"}],
        "result_count": 20,
        "field_coverage": ["price", "title"],
    }


def test_tracker_forces_replan_after_three_semantic_no_progress_states() -> None:
    tracker = SemanticStateTracker()
    tracker.observe(_state())

    first = tracker.observe(_state())
    second = tracker.observe(_state())
    third = tracker.observe(_state())

    assert first["consecutive_no_progress"] == 1
    assert second["consecutive_no_progress"] == 2
    assert third["consecutive_no_progress"] == 3
    assert third["replan_required"] is True
    assert third["replan_reason"] == ["three_consecutive_no_progress_states"]


def test_tracker_requires_three_state_revisits_before_replan() -> None:
    tracker = SemanticStateTracker()

    tracker.observe(_state(price="0-100"))
    tracker.observe(_state(price="100-200"))
    first_revisit = tracker.observe(_state(price="0-100"))
    second_revisit = tracker.observe(_state(price="100-200"))
    third_revisit = tracker.observe(_state(price="0-100"))

    assert first_revisit["aba_loop"] is True
    assert first_revisit["state_revisit_count"] == 1
    assert first_revisit["replan_required"] is False
    assert second_revisit["aba_loop"] is True
    assert second_revisit["state_revisit_count"] == 2
    assert second_revisit["repeated_filter_state"] is True
    assert second_revisit["replan_required"] is False
    assert third_revisit["aba_loop"] is True
    assert third_revisit["state_revisit_count"] == 3
    assert third_revisit["repeated_filter_state"] is True
    assert third_revisit["replan_required"] is True
    assert "three_semantic_state_revisits" in third_revisit["replan_reason"]


def test_new_field_evidence_counts_as_progress() -> None:
    tracker = SemanticStateTracker()
    tracker.observe(_state(fields=[]))
    progress = tracker.observe(_state(fields=["title", "price"]))

    assert progress["progress"] == "progress"
    assert progress["observable_progress"] is True
    assert progress["semantic_state"]["field_coverage"] == ["price", "title"]

    after_navigation = tracker.observe(
        {
            **_state(fields=[]),
            "url": "https://shop.example/item/1",
        }
    )
    assert after_navigation["semantic_state"]["field_coverage"] == ["price", "title"]


def test_tracker_observes_each_model_action_group_once() -> None:
    tracker = SemanticStateTracker()
    first = tracker.observe(_state(), action_group_id="group-1")
    duplicate = tracker.observe(_state(result_count=99), action_group_id="group-1")
    second = tracker.observe(_state(result_count=99), action_group_id="group-2")

    assert duplicate == first
    assert second["revision"] == first["revision"] + 1
    assert second["action_group_id"] == "group-2"

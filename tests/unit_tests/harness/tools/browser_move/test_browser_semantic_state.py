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


def test_generation_change_does_not_reset_semantic_no_progress() -> None:
    tracker = SemanticStateTracker()
    first_state = {**_state(), "generation_id": "g1"}
    second_state = {**_state(), "generation_id": "g2"}

    tracker.observe(first_state)
    repeated = tracker.observe(second_state)

    assert repeated["progress"] == "no_progress"
    assert repeated["changed_fields"] == []


def test_tracker_reports_semantic_fields_that_actually_changed() -> None:
    tracker = SemanticStateTracker()
    tracker.observe(_state(price="0-100", result_count=10))

    progress = tracker.observe(_state(price="100-200", result_count=7))

    assert progress["changed_fields"] == ["result_count", "selected_filters"]


def test_content_changes_count_as_progress_with_unchanged_selected_metadata() -> None:
    tracker = SemanticStateTracker()
    metadata = {**_state(), "first_result_text": "Pinned tender"}
    tracker.observe({**metadata, "page_content_hash": "a" * 64})

    progress = tracker.observe({**metadata, "page_content_hash": "b" * 64})

    assert progress["progress"] == "progress"
    assert progress["changed_fields"] == ["page_content_hash"]
    assert progress["semantic_state"]["first_result_text"] == "Pinned tender"
    assert progress["semantic_state"]["page_content_hash"] == "b" * 64


def test_content_hash_survives_nested_semantic_state_normalization() -> None:
    state = {"semantic_state": {**_state(), "page_content_hash": "a" * 64}}

    normalized = build_semantic_state(state)

    assert normalized["page_content_hash"] == "a" * 64
    assert build_semantic_state(normalized) == normalized


def test_dropdown_toggling_revisits_complete_content_states() -> None:
    tracker = SemanticStateTracker()
    closed = {**_state(), "page_content_hash": "a" * 64}
    opened = {**_state(), "page_content_hash": "b" * 64}
    tracker.observe(closed)
    assert tracker.observe(opened)["progress"] == "progress"

    for index, state in enumerate((closed, opened, closed), start=1):
        progress = tracker.observe(state)
        assert progress["progress"] == "state_revisit"
        assert progress["aba_loop"] is True
        assert progress["consecutive_no_progress"] == index
        assert progress["replan_required"] is (index == 3)


def test_old_filter_with_new_content_is_progress_and_filter_repetition_is_diagnostic() -> None:
    tracker = SemanticStateTracker()
    tracker.observe({**_state(price="0-100"), "page_content_hash": "a" * 64})
    tracker.observe({**_state(price="100-200"), "page_content_hash": "b" * 64})

    progress = tracker.observe({**_state(price="0-100"), "page_content_hash": "c" * 64})

    assert progress["repeated_filter_state"] is True
    assert progress["state_revisit"] is False
    assert progress["progress"] == "progress"
    assert progress["consecutive_no_progress"] == 0


def test_metadata_only_filter_revisits_remain_compatible() -> None:
    tracker = SemanticStateTracker()
    tracker.observe(_state(price="0-100"))
    tracker.observe(_state(price="100-200"))

    progress = tracker.observe(_state(price="0-100", result_count=11))

    assert progress["state_revisit"] is False
    assert progress["repeated_filter_state"] is True
    assert progress["progress"] == "state_revisit"


def test_reads_preserve_the_interaction_failure_budget() -> None:
    tracker = SemanticStateTracker()
    tracker.observe(_state())
    tracker.observe(_state())
    tracker.observe(_state())

    for index in range(20):
        progress = tracker.observe(_state(), action_group_id=f"read-{index}", observation_only=True)
        assert progress["progress"] == "inspection"
        assert progress["observation_only"] is True
        assert progress["observable_progress"] is False
        assert progress["consecutive_no_progress"] == 2
        assert progress["replan_required"] is False

    failed_action = tracker.observe(_state(), action_group_id="click")
    assert failed_action["observation_only"] is False
    assert failed_action["consecutive_no_progress"] == 3
    assert failed_action["replan_required"] is True


def test_reads_preserve_mutation_loop_history_and_revisit_counts() -> None:
    tracker = SemanticStateTracker(history_size=4)
    first = _state(price="0-100")
    second = _state(price="100-200")
    tracker.observe(first)
    tracker.observe(second)
    tracker.observe(first)

    for _ in range(20):
        progress = tracker.observe(first, observation_only=True)
        assert progress["consecutive_no_progress"] == 1
        assert progress["state_revisit_count"] == 1

    revisit = tracker.observe(second)
    assert revisit["aba_loop"] is True
    assert revisit["state_revisit_count"] == 2
    assert revisit["consecutive_no_progress"] == 2
    assert tracker.observe(first)["replan_required"] is True


def test_read_of_revisited_state_is_not_a_failed_interaction() -> None:
    tracker = SemanticStateTracker()
    tracker.observe(_state(price="0-100"))
    tracker.observe(_state(price="100-200"))

    read = tracker.observe(_state(price="0-100"), observation_only=True)

    assert read["progress"] == "inspection"
    assert read["consecutive_no_progress"] == 0
    assert read["state_revisit_count"] == 0
    assert read["replan_required"] is False


def test_read_with_new_evidence_advances_progress_and_updates_baseline() -> None:
    tracker = SemanticStateTracker()
    tracker.observe(_state())
    tracker.observe(_state())
    evidence = _state(fields=["title", "price"])

    progress = tracker.observe(evidence, observation_only=True)
    repeated = tracker.observe(evidence, observation_only=True)

    assert progress["progress"] == "progress"
    assert progress["observable_progress"] is True
    assert progress["consecutive_no_progress"] == 0
    assert progress["changed_fields"] == ["field_coverage"]
    assert repeated["progress"] == "inspection"
    assert tracker.observe(evidence)["consecutive_no_progress"] == 1


def test_read_deduplication_and_reset_keep_initial_observation_semantics() -> None:
    tracker = SemanticStateTracker()
    initial = tracker.observe(_state(), action_group_id="initial-read", observation_only=True)
    assert initial["progress"] == "initial"
    tracker.observe(_state())
    tracker.observe(_state())
    tracker.observe(_state())
    read = tracker.observe(_state(), action_group_id="read", observation_only=True)

    assert read["replan_required"] is True
    assert read["consecutive_no_progress"] == 3
    assert tracker.observe(_state(fields=["title"]), action_group_id="read", observation_only=True) == read

    tracker.reset()
    reset = tracker.observe(_state(), action_group_id="read", observation_only=True)
    assert reset["progress"] == "initial"
    assert reset["consecutive_no_progress"] == 0
    assert reset["state_revisit_count"] == 0
    assert reset["replan_required"] is False

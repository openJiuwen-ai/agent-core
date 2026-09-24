"""Characterization tests for candidate filtering and node selection.

filter.py, selector.py and adaptive_selector.py are slated to merge into one
module; these pin the full policy table and both selection functions first.
"""

import pytest

from video_memory.retrieval.adaptive_selector import (
    SelectionPolicy,
    adaptive_select_nodes,
    selection_policy,
)
from video_memory.retrieval.filter import filter_nodes
from video_memory.retrieval.selector import select_nodes
from video_memory.schemas import MemoryNode, QAItem, QAParseResult

RAW_TYPES = {
    "detail": ["Factual Detail Questions"],
    "privacy": ["Privacy-Sensitive Questions"],
    "multi": ["Multi-Evidence Reasoning Questions"],
    "summary": ["Content Summarization Questions"],
    "preference": ["User Preference Questions"],
}


def _qa(raw_type: list[str]) -> QAItem:
    return QAItem(
        qa_id="q",
        question="Q?",
        answer="A",
        qa_time_key=None,
        qa_time_id=None,
        reference_sets=[["f"]],
        raw_type=raw_type,
    )


def _node(node_id: str, node_type: str, time_ids: list[int]) -> MemoryNode:
    return MemoryNode(node_id, node_type, f"text of {node_id}", time_ids)


# --------------------------------------------------------------------------
# filter_nodes
# --------------------------------------------------------------------------

NODES = [
    _node("d_early", "detail", [1, 2]),
    _node("d_late", "detail", [90]),
    _node("s_early", "summary", [1, 2]),
    _node("p_notime", "preference", []),
]


def test_filter_keeps_only_the_parsed_type() -> None:
    parsed = QAParseResult(qa_types=["detail"], entities=[], time_range=None)
    assert [n.node_id for n in filter_nodes(NODES, parsed)] == ["d_early", "d_late"]


def test_empty_qa_types_disables_the_type_filter() -> None:
    parsed = QAParseResult(qa_types=[], entities=[], time_range=None)
    assert len(filter_nodes(NODES, parsed)) == len(NODES)


def test_time_range_needs_one_overlapping_time_id() -> None:
    parsed = QAParseResult(qa_types=[], entities=[], time_range=(0, 10))
    assert [n.node_id for n in filter_nodes(NODES, parsed)] == ["d_early", "s_early"]


def test_nodes_without_time_ids_are_dropped_by_any_time_range() -> None:
    parsed = QAParseResult(qa_types=["preference"], entities=[], time_range=(0, 1000))
    assert filter_nodes(NODES, parsed) == []


# --------------------------------------------------------------------------
# select_nodes (fixed thresholds, used by pipelines/run_qa.py)
# --------------------------------------------------------------------------

SCORES = {"a": 0.9, "b": 0.6, "c": 0.4, "d": 0.1}
NODE_TO_FRAMES = {"a": ["f1"], "b": ["f2", "f1"], "c": ["f3"], "d": ["f4"]}


def test_select_nodes_takes_everything_at_or_above_the_threshold() -> None:
    context = select_nodes(SCORES, NODE_TO_FRAMES, final_node_threshold=0.5, min_k=1, max_k=10)

    assert context.selected_node_ids == ["a", "b"]
    assert context.retrieved_frame_keys == ["f1", "f2"]  # deduplicated and sorted
    assert context.node_scores == {"a": 0.9, "b": 0.6}


def test_select_nodes_falls_back_to_min_k_when_nothing_clears_the_threshold() -> None:
    """The floor is a retrieval strategy, not a failure path: returning zero
    frames would leave the answer model with no evidence at all."""
    context = select_nodes(SCORES, NODE_TO_FRAMES, final_node_threshold=0.99, min_k=2, max_k=10)
    assert context.selected_node_ids == ["a", "b"]


def test_select_nodes_truncates_at_max_k() -> None:
    context = select_nodes(SCORES, NODE_TO_FRAMES, final_node_threshold=0.0, min_k=1, max_k=2)
    assert context.selected_node_ids == ["a", "b"]


def test_select_nodes_treats_max_k_of_zero_as_unbounded() -> None:
    context = select_nodes(SCORES, NODE_TO_FRAMES, final_node_threshold=0.0, min_k=0, max_k=0)
    assert context.selected_node_ids == ["a", "b", "c", "d"]


def test_select_nodes_ignores_nodes_with_no_frame_edges() -> None:
    context = select_nodes({"orphan": 1.0}, {}, final_node_threshold=0.0, min_k=1, max_k=5)
    assert context.selected_node_ids == ["orphan"]
    assert context.retrieved_frame_keys == []


# --------------------------------------------------------------------------
# selection_policy: the full category x rank_method table
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("category", "expected_k"),
    [("detail", (1, 3)), ("privacy", (1, 3)), ("multi", (2, 6)), ("summary", (4, 10)), ("preference", (6, 15))],
)
def test_selection_policy_min_max_per_category(category: str, expected_k: tuple[int, int]) -> None:
    policy = selection_policy(_qa(RAW_TYPES[category]), "field", [])
    assert (policy.category, policy.min_k, policy.max_k) == (category, *expected_k)


@pytest.mark.parametrize(
    ("rank_method", "expected"),
    [
        ("field", {"detail": 0.35, "privacy": 0.35, "multi": 0.30, "summary": 0.28, "preference": 0.25}),
        ("embedding", {"detail": 0.45, "privacy": 0.45, "multi": 0.40, "summary": 0.35, "preference": 0.32}),
        ("qwen_embedding", {"detail": 0.45, "privacy": 0.45, "multi": 0.40, "summary": 0.35, "preference": 0.32}),
        ("api", {"detail": 0.65, "privacy": 0.65, "multi": 0.58, "summary": 0.55, "preference": 0.50}),
    ],
)
def test_selection_policy_thresholds_per_rank_method(rank_method: str, expected: dict[str, float]) -> None:
    for category, threshold in expected.items():
        policy = selection_policy(_qa(RAW_TYPES[category]), rank_method, [])
        assert policy.threshold == pytest.approx(threshold), f"{rank_method}/{category}"


def test_unknown_rank_method_falls_back_to_the_field_thresholds() -> None:
    policy = selection_policy(_qa(RAW_TYPES["detail"]), "not_a_method", [])
    assert policy.threshold == pytest.approx(0.35)


@pytest.mark.parametrize(
    ("parsed_types", "expected"),
    [(["summary"], "summary"), (["preference"], "preference"), (["detail"], "detail"), ([], "detail")],
)
def test_category_falls_back_to_the_parsed_type_when_raw_type_is_empty(
    parsed_types: list[str], expected: str
) -> None:
    assert selection_policy(_qa([]), "field", parsed_types).category == expected


def test_raw_type_wins_over_the_parsed_type() -> None:
    assert selection_policy(_qa(RAW_TYPES["privacy"]), "field", ["summary"]).category == "privacy"


# --------------------------------------------------------------------------
# adaptive_select_nodes
# --------------------------------------------------------------------------


def test_adaptive_select_applies_the_policy_threshold_then_min_k_then_max_k() -> None:
    policy = SelectionPolicy(threshold=0.5, min_k=1, max_k=10, category="detail")
    assert adaptive_select_nodes(SCORES, NODE_TO_FRAMES, policy).selected_node_ids == ["a", "b"]

    floor = SelectionPolicy(threshold=0.99, min_k=3, max_k=10, category="summary")
    assert adaptive_select_nodes(SCORES, NODE_TO_FRAMES, floor).selected_node_ids == ["a", "b", "c"]

    cap = SelectionPolicy(threshold=0.0, min_k=1, max_k=2, category="detail")
    assert adaptive_select_nodes(SCORES, NODE_TO_FRAMES, cap).selected_node_ids == ["a", "b"]


def test_adaptive_select_returns_sorted_deduplicated_frames() -> None:
    policy = SelectionPolicy(threshold=0.0, min_k=1, max_k=10, category="detail")
    context = adaptive_select_nodes(SCORES, NODE_TO_FRAMES, policy)
    assert context.retrieved_frame_keys == ["f1", "f2", "f3", "f4"]


def test_adaptive_select_on_empty_scores() -> None:
    policy = SelectionPolicy(threshold=0.5, min_k=3, max_k=10, category="detail")
    context = adaptive_select_nodes({}, {}, policy)
    assert (context.selected_node_ids, context.retrieved_frame_keys) == ([], [])

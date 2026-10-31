"""Characterization tests for the node rankers.

These pin the current scoring arithmetic so that merging ranker.py,
field_ranker.py and embedding_ranker.py — which today cross-import each
other's private helpers — cannot change any score unnoticed.
"""

import pytest

from video_memory.retrieval.field_ranker import (
    FieldAwareRanker,
    field_bonus_score,
    split_multiple_choice,
)
from video_memory.retrieval.ranker import RuleBasedNodeRanker, _jaccard, _tokens
from video_memory.schemas import MemoryNode, QAItem, QAParseResult


def _node(index: int, node_type: str, description: str) -> MemoryNode:
    return MemoryNode(f"n{index}", node_type, description, [index])


PRICE_NODE = _node(0, "detail", "The price is $299.00")
NOISE_NODE = _node(1, "detail", "Unrelated navigation bar")
SUMMARY_NODE = _node(2, "summary", "Window summary: viewed a product")
NODES = [PRICE_NODE, NOISE_NODE, SUMMARY_NODE]


def _qa(question: str, raw_type: list[str] | None = None) -> QAItem:
    return QAItem(
        qa_id="q",
        question=question,
        answer="A",
        qa_time_key=None,
        qa_time_id=None,
        reference_sets=[["f"]],
        raw_type=raw_type or ["Factual Detail Questions"],
    )


def _parsed(qa_types: list[str], entities: list[str]) -> QAParseResult:
    return QAParseResult(qa_types=qa_types, entities=entities, time_range=(0, 10), intent="find price")


# --------------------------------------------------------------------------
# shared helpers
# --------------------------------------------------------------------------


def test_tokens_lowercases_and_drops_single_characters() -> None:
    assert _tokens("The price is $299.00 a") == {"the", "price", "is", "299", "00"}


def test_jaccard_is_zero_when_either_side_is_empty() -> None:
    assert _jaccard(set(), {"a"}) == 0.0
    assert _jaccard({"a"}, set()) == 0.0
    assert _jaccard({"a", "b"}, {"b", "c"}) == pytest.approx(1 / 3)


def test_split_multiple_choice() -> None:
    assert split_multiple_choice("Q?\nA. one\nB. two") == ("Q?", "A. one\nB. two")
    assert split_multiple_choice("Q with no options") == ("Q with no options", "")


# --------------------------------------------------------------------------
# RuleBasedNodeRanker: 0.2 + lexical*0.6 + entity_overlap*0.3
# --------------------------------------------------------------------------


def test_rule_based_ranker_scores_and_orders() -> None:
    ranked = RuleBasedNodeRanker().rank("What is the price?", ["price"], NODES)

    assert [item.node_id for item in ranked] == ["n0", "n1", "n2"]
    assert ranked[0].score == pytest.approx(0.56)
    assert all(item.reason == "rule_based_overlap" for item in ranked)


def test_rule_based_ranker_floor_is_the_constant_term() -> None:
    ranked = RuleBasedNodeRanker().rank("completely disjoint wording", [], [NOISE_NODE])
    assert ranked[0].score == pytest.approx(0.2)


# --------------------------------------------------------------------------
# FieldAwareRanker: 0.15 + lexical*0.45 + entity*0.25 + option*0.1
#                   + field_bonus + type_bonus
# --------------------------------------------------------------------------


def test_field_aware_ranker_scores_and_orders() -> None:
    ranked = FieldAwareRanker().rank(
        _qa("What is the price?\nA. 299\nB. 399"),
        _parsed(["detail"], ["price"]),
        NODES,
    )

    assert [item.node_id for item in ranked] == ["n0", "n1", "n2"]
    assert ranked[0].score == pytest.approx(0.7616666666666667)
    assert ranked[0].reason == "field_rule bonus=0.28"
    assert ranked[2].score == pytest.approx(0.15)


def test_field_aware_ranker_type_bonus_prefers_the_matching_node_type() -> None:
    preference_node = _node(3, "preference", "The user may be interested in news")
    detail_node = _node(4, "detail", "The user may be interested in news")

    ranked = FieldAwareRanker().rank(
        _qa("Which source does the user prefer?", raw_type=["User Preference Questions"]),
        _parsed([], []),
        [preference_node, detail_node],
    )
    by_id = {item.node_id: item.score for item in ranked}

    # Identical text, so the whole gap is the preference type bonus.
    assert by_id["n3"] - by_id["n4"] == pytest.approx(0.12)


def test_field_aware_ranker_caps_at_one() -> None:
    node = _node(5, "detail", "Account page; iris@gmail.com; Last Updated: October 9; $299.00")
    ranked = FieldAwareRanker().rank(
        _qa("What is the email address and last updated time and price?"),
        _parsed(["detail"], ["email", "address", "last", "updated", "price"]),
        [node],
    )
    assert ranked[0].score <= 1.0


# --------------------------------------------------------------------------
# field_bonus_score: seven rules, capped at 0.45
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "node_text", "expected"),
    [
        ("What was the last updated time?", "Article; Last Updated: October 9", 0.35),
        ("What is the price?", "Shopping page; $299.00", 0.28),
        ("What is the email address?", "Account page; iris@gmail.com", 0.35),
        ("What is the account number?", "Bank account 018274639521", 0.32),
        ("What did the user finally select?", "The user selected the alarm", 0.18),
        ("Which does the user prefer most often?", "The user may be interested in news", 0.15),
        ("Which is the most accurate summary?", "The user viewed the weather", 0.12),
        ("Nothing relevant here", "Nothing relevant either", 0.0),
    ],
)
def test_field_bonus_score_rules(question: str, node_text: str, expected: float) -> None:
    assert field_bonus_score(question, node_text) == pytest.approx(expected)


def test_field_bonus_score_is_capped() -> None:
    node_text = "Account page; iris@gmail.com; Last Updated: October 9; $299.00; account 018274639521"
    question = "What is the email address, account number, price and last updated time?"
    assert field_bonus_score(question, node_text) == pytest.approx(0.45)


def test_field_bonus_needs_both_sides_to_match() -> None:
    assert field_bonus_score("What is the price?", "No numbers on this screen") == 0.0
    assert field_bonus_score("Who is shown?", "Shopping page; $299.00") == 0.0

# coding: utf-8
"""Prompts built from a precomputed summary tree, and its startup validation."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from openjiuwen.agent_evolving.attribution.trajectory_summary.attribution_context import (
    AttributionContextLimits,
    AttributionSummaryError,
    render_attribution_summary,
    require_attribution_summary,
)
from openjiuwen.agent_evolving.attribution.trajectory_summary.models import (
    AgentSummary,
    ProblemSummary,
    Segment,
    SummaryTree,
    Turn,
)


def _tree() -> SummaryTree:
    segment_a = Segment(
        segment_id="A:segment:1",
        turn_id="A:turn:1",
        agent_id="A",
        start_node_id="A:0",
        end_node_id="A:1",
        start_ts_ns=0,
        end_ts_ns=10,
        narrative="A segment",
        status="completed",
    )
    segment_b = Segment(
        segment_id="B:segment:1",
        turn_id="B:turn:1",
        agent_id="B",
        start_node_id="B:0",
        end_node_id="B:1",
        start_ts_ns=20,
        end_ts_ns=30,
        narrative="B segment",
        status="completed",
    )
    return SummaryTree(
        trajectory_summary="root summary text",
        display_summary="DISPLAY_SENTINEL",
        agent_summaries={
            "A": AgentSummary(
                agent_id="A",
                display_name="Alpha",
                kind="main_agent",
                summary="alpha summary",
                start_node_id="A:0",
                end_node_id="A:1",
                start_ts_ns=0,
                end_ts_ns=10,
                status="completed",
            ),
            "B": AgentSummary(
                agent_id="B",
                display_name="Beta",
                kind="team_member",
                summary="beta summary",
                start_node_id="B:0",
                end_node_id="B:1",
                start_ts_ns=20,
                end_ts_ns=30,
                status="completed",
            ),
        },
        turns={
            "A": [Turn("A:turn:1", 1, "A", "A:0", "A:1", 0, 10, segments=[segment_a], status="completed")],
            "B": [Turn("B:turn:1", 1, "B", "B:0", "B:1", 20, 30, segments=[segment_b], status="completed")],
        },
        problems=[ProblemSummary(problem_id="PROBLEM_SENTINEL", kind="tool_error", summary="hidden", locations=[])],
        status="completed",
    )


def test_require_attribution_summary_accepts_valid_tree() -> None:
    tree = _tree()
    assert require_attribution_summary(SimpleNamespace(summary_tree=tree)) is tree


def test_require_attribution_summary_derives_single_agent_without_agent_summaries() -> None:
    tree = _tree()
    tree.agent_summaries = {}
    assert require_attribution_summary(SimpleNamespace(summary_tree=tree)) is tree


def test_require_attribution_summary_accepts_degraded_status() -> None:
    tree = _tree()
    tree.status = "degraded"
    assert require_attribution_summary(SimpleNamespace(summary_tree=tree)) is tree


def test_require_attribution_summary_rejects_missing_tree() -> None:
    with pytest.raises(AttributionSummaryError, match="required"):
        require_attribution_summary(SimpleNamespace(summary_tree=None))


@pytest.mark.parametrize(
    "mutate,message",
    [
        (lambda tree: setattr(tree, "status", "failed"), "failed"),
        (lambda tree: setattr(tree, "trajectory_summary", ""), "trajectory_summary"),
        (lambda tree: setattr(tree, "turns", {}), "no turns"),
        (lambda tree: setattr(tree.turns["A"][0], "segments", []), "no segments"),
    ],
)
def test_require_attribution_summary_rejects_invalid_input(mutate, message: str) -> None:
    tree = _tree()
    mutate(tree)
    with pytest.raises(AttributionSummaryError, match=message):
        require_attribution_summary(SimpleNamespace(summary_tree=tree))


def test_render_includes_navigation_and_excludes_hidden_fields() -> None:
    text = render_attribution_summary(_tree(), "zh")
    assert "root summary text" in text
    assert "A:0 -> A:1" in text and "A:segment:1 -> A:segment:1" in text
    assert "B:0 -> B:1" in text and "B:segment:1" in text
    assert "A:turn:1" in text and "B:turn:1" in text
    assert "DISPLAY_SENTINEL" not in text
    assert "PROBLEM_SENTINEL" not in text
    assert "problems" not in text and "problem_id" not in text


def test_render_english_labels() -> None:
    text = render_attribution_summary(_tree(), "en")
    assert "Hierarchical Trajectory Summary" in text
    assert "Subagent-level Trajectory Summary Index" in text
    assert "Subagent trajectory summary: alpha summary" in text
    assert "Subagent trajectory summary：alpha summary" not in text
    assert "Progressive Disclosure" in text


def test_render_truncates_root_and_agent_excerpts() -> None:
    tree = _tree()
    tree.trajectory_summary = "root " * 5000
    tree.agent_summaries["A"].summary = "alpha " * 5000
    text = render_attribution_summary(
        tree,
        "zh",
        AttributionContextLimits(total_chars=20_000, root_chars=1_000, agent_excerpt_chars=500),
    )
    assert "[summary truncated]" in text
    assert len(text) <= 20_000
    assert "A:0 -> A:1" in text
    assert "B:segment:1" in text

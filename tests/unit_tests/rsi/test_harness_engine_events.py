# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for the shared event projection of single-Harness candidates."""

from openjiuwen.rsi.harness_rsi.single_harness.events_translate import (
    epoch_node_event,
    node_event,
    parent_node_id,
    progress_event,
)


def test_candidate_event_projects_public_node_without_control_plane_terms() -> None:
    candidate = {
        "candidate_id": "candidate-2",
        "status": "rejected",
        "accepted": False,
        "candidate_score": 0.5,
        "before_harness_refs_path": "candidate-1.yaml",
        "candidate_harness_refs_path": "candidate-2.yaml",
        "reason": "epoch_full_checkpoint_regressed",
        "capabilities": [
            {
                "action_group": "prompt",
                "operation": "modify",
                "target_path": "prompt_sections/verification.md",
                "expected_effect": "Run a final verification before delivery.",
            }
        ],
    }
    previous = {
        "candidate_id": "candidate-1",
        "candidate_harness_refs_path": "candidate-1.yaml",
    }

    event = node_event(
        candidate,
        iteration=2,
        parent_id=parent_node_id(candidate, [previous]),
    )

    assert event.node.node_id == "candidate-2"
    assert event.node.parent_id == "candidate-1"
    assert event.node.type == "REJECTED"
    assert event.node.changes[0].group == "PROMPT"
    assert event.node.changes[0].target == "prompt_sections/verification.md"
    assert "epoch" not in event.node.reason.lower()
    assert "checkpoint" not in event.node.reason.lower()


def test_progress_event_counts_epochs_instead_of_candidates() -> None:
    event = progress_event(
        {
            "candidate_gates": [{"candidate_id": "one"}, {"candidate_id": "two"}],
            "epoch_checkpoints": [{"epoch": 1}],
            "best_score": 0.8,
            "baseline_score": 0.4,
        },
        total_iterations=6,
    )

    assert event.iteration == 1
    assert event.total_iterations == 6
    assert event.score == 0.8
    assert event.baseline == 0.4


def test_epoch_node_aggregates_changes_and_follows_selected_parent() -> None:
    checkpoints = [
        {"epoch": 1, "selected_harness_refs_path": "h1.yaml"},
        {"epoch": 2, "selected_harness_refs_path": "h1.yaml"},
    ]
    current = {
        "epoch": 3,
        "before_harness_refs_path": "h1.yaml",
        "selected_harness_refs_path": "h3.yaml",
        "harness_refs_path": "h3.yaml",
        "score": 0.9,
        "promotion_applied": True,
    }
    event = epoch_node_event(
        {
            "epoch_checkpoints": checkpoints + [current],
            "candidate_gates": [
                {"epoch": 3, "status": "accepted", "capabilities": [{"action_group": "skill"}]},
                {"epoch": 3, "status": "accepted", "capabilities": [{"action_group": "tool"}]},
                {"epoch": 3, "status": "rejected", "capabilities": [{"action_group": "prompt"}]},
            ],
        },
        current,
    )
    assert event.node.node_id == "epoch-003"
    assert event.node.parent_id == "epoch-002"
    assert event.node.score == 0.9
    assert [change.group for change in event.node.changes] == ["SKILL", "TOOL"]


def test_epoch_node_does_not_reuse_score_for_a_different_harness() -> None:
    event = epoch_node_event(
        {},
        {
            "epoch": 1,
            "selected_harness_refs_path": "filtered.yaml",
            "harness_refs_path": "replayed.yaml",
            "score": 1.0,
            "promotion_applied": True,
        },
    )
    assert event.node.score is None
    assert event.node.extra["artifact_path"] == "filtered.yaml"

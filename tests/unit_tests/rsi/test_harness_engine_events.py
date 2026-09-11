# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for the shared event projection of single-Harness candidates."""

import pytest

from openjiuwen.rsi.harness_rsi.single_harness.events_translate import (
    active_epoch_node_event,
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
        {"epoch": 1, "selected_harness_refs_path": "h1.yaml", "promotion_applied": True},
        {"epoch": 2, "selected_harness_refs_path": "h1.yaml", "promotion_applied": False},
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
    assert event.node.parent_id == "epoch-001"
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


def test_rejected_and_unchanged_epochs_do_not_become_h0_parents() -> None:
    state = {"source_harness_refs_path": "h0.yaml", "baseline_score": 0.6, "epoch_checkpoints": []}
    for epoch, status in enumerate(("rejected", "verified", "rejected"), 1):
        checkpoint = {
            "epoch": epoch,
            "status": status,
            "promotion_applied": False,
            "before_harness_refs_path": "h0.yaml",
            "selected_harness_refs_path": "h0.yaml",
            "harness_refs_path": "h0.yaml",
            "score": 0.6,
        }
        state["epoch_checkpoints"].append(checkpoint)
        event = epoch_node_event(state, checkpoint)
        assert event.node.parent_id == "h0"
        assert event.node.score == 0.6
        assert not event.node.adopted
    state.update(active_epoch=4, active_epoch_before_harness_refs_path="h0.yaml")
    active = active_epoch_node_event(state).node
    assert active.parent_id == "h0"
    assert active.reason is None
    assert active.score is None
    assert set(active.extra) == {"artifact_path", "iteration_unit", "source_evidence"}


@pytest.mark.parametrize("score", [0.6, 0.0])
def test_existing_score_and_adoption_fields_are_preserved(score) -> None:
    event = epoch_node_event(
        {"source_harness_refs_path": "h0.yaml", "baseline_score": 0.0},
        {
            "epoch": 1,
            "status": "rejected",
            "promotion_applied": False,
            "before_harness_refs_path": "h0.yaml",
            "selected_harness_refs_path": "h0.yaml",
            "harness_refs_path": "h0.yaml",
            "score": score,
        },
    )
    assert event.node.parent_id == "h0"
    assert event.node.score == score
    assert not event.node.adopted
    assert "acceptance checks" in event.node.reason
    assert "score" not in event.node.reason.lower()


@pytest.mark.parametrize("parent_score", [0.8, None])
def test_parent_is_actual_promoted_version_not_latest_observation(parent_score) -> None:
    parent = {
        "epoch": 1,
        "promotion_applied": True,
        "before_harness_refs_path": "h0.yaml",
        "selected_harness_refs_path": "h1.yaml",
        "harness_refs_path": "h1.yaml",
        "score": parent_score,
    }
    rejected = {**parent, "epoch": 2, "promotion_applied": False, "score": 0.1}
    current = {
        "epoch": 3,
        "before_harness_refs_path": "h1.yaml",
        "promotion_applied": False,
        "selected_harness_refs_path": "h1.yaml",
        "harness_refs_path": "h1.yaml",
        "score": 0.6,
    }
    event = epoch_node_event(
        {
            "source_harness_refs_path": "h0.yaml",
            "baseline_score": 0.0,
            "epoch_checkpoints": [parent, rejected, current],
        },
        current,
    )
    assert event.node.parent_id == "epoch-001"
    assert event.node.score == 0.6


def test_parent_follows_selected_filtered_version_not_replayed_version() -> None:
    parent = {
        "epoch": 1,
        "promotion_applied": True,
        "selected_harness_refs_path": "filtered.yaml",
        "harness_refs_path": "replayed.yaml",
        "score": 1.0,
    }
    current = {
        "epoch": 2,
        "before_harness_refs_path": "filtered.yaml",
        "selected_harness_refs_path": "filtered.yaml",
        "harness_refs_path": "filtered.yaml",
        "score": 0.6,
    }
    event = epoch_node_event({"epoch_checkpoints": [parent]}, current)
    assert event.node.parent_id == "epoch-001"
    assert event.node.score == 0.6

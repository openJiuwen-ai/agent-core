# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for the shared event projection of single-Harness candidates."""

from openjiuwen.rsi.harness_rsi.single_harness.events_translate import (
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


def test_progress_event_uses_persisted_candidate_count_and_scores() -> None:
    event = progress_event(
        {
            "candidate_gates": [{"candidate_id": "one"}, {"candidate_id": "two"}],
            "best_score": 0.8,
            "baseline_score": 0.4,
        },
        total_iterations=6,
    )

    assert event.iteration == 2
    assert event.total_iterations == 6
    assert event.score == 0.8
    assert event.baseline == 0.4
    assert event.usage is None

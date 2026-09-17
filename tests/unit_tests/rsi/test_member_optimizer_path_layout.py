# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for short runtime paths used by member optimization."""

from __future__ import annotations

from pathlib import Path

from openjiuwen.rsi.harness_rsi.member_optimizer.path_layout import (
    MemberOptimizerPathLayout,
)


def test_member_optimizer_runtime_paths_do_not_nest_under_audit_dir(tmp_path: Path) -> None:
    workspace_root = tmp_path / ".local" / "rsi" / "webpage_single_harness" / "workspace"
    team_root = workspace_root / "energy-storage-investor-landing-page-swarm"
    output_root = team_root / "member_optimizations"
    (team_root / "evaluations").mkdir(parents=True)
    output_root.mkdir(parents=True)

    layout = MemberOptimizerPathLayout.from_output_root(output_root)
    role = "investor-readiness-reviewer"
    optimization_id = "member_optimization_002"
    skill_rel = Path("skills") / "cross_artifact_class_consistency" / "SKILL.md"

    old_publish_path = output_root / optimization_id / ".publish_tmp" / role / skill_rel
    new_publish_path = layout.publish_tmp_dir(optimization_id, role) / skill_rel
    current_path = layout.current_harness_dir(role) / skill_rel

    assert layout.runtime_root == workspace_root / "mh"
    assert output_root not in new_publish_path.parents
    assert team_root not in new_publish_path.parents
    assert output_root not in current_path.parents
    assert role not in str(new_publish_path)
    assert len(str(new_publish_path)) + 40 < len(str(old_publish_path))
    assert len(str(current_path)) < len(str(old_publish_path))


def test_member_optimizer_runtime_paths_are_short_for_nested_sibling_candidate(
    tmp_path: Path,
) -> None:
    run_root = tmp_path / ".office_runs" / "runs" / "office_jws_example"
    optimization_root = run_root / "single_harness_optimization"
    (optimization_root / "evaluations").mkdir(parents=True)
    candidate_root = optimization_root / "member_optimizations" / "sibling_cohorts" / "e001_b001_r001_example" / "c001"

    layout = MemberOptimizerPathLayout.from_output_root(candidate_root)

    assert layout.runtime_root == run_root / "mh"
    assert candidate_root not in layout.worktrees_dir("member_optimization_001").parents


def test_member_optimizer_role_mapping_is_stable(tmp_path: Path) -> None:
    layout = MemberOptimizerPathLayout.from_output_root(tmp_path / "workspace" / "demo-team" / "member_optimizations")

    first = layout.role_key("frontend-architect")
    second = layout.role_key("frontend-architect")
    other = layout.role_key("content-curator")

    assert first == second
    assert first != other
    assert first.startswith("r")
    assert len(first) == 9

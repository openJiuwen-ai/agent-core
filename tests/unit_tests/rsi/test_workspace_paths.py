# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for orchestrator workspace path allocation."""

from __future__ import annotations

from pathlib import Path

from openjiuwen.rsi.orchestrator.workspace_paths import (
    OrchestratorWorkspacePaths,
)


def test_for_team_sanitizes_team_name_and_initializes_standard_structure(tmp_path: Path) -> None:
    """Team workspaces use safe names and can materialize the 001 directory contract."""
    paths = OrchestratorWorkspacePaths(str(tmp_path / "workspace")).for_team("Math Team/高级")

    initialized = paths.ensure_workspace_structure()

    assert paths.root == (tmp_path / "workspace" / "Math_Team_高级").resolve()
    assert initialized == [
        paths.root / "datasets",
        paths.root / "evaluations",
        paths.root / "team_skills",
        paths.root / "member_optimizations",
        paths.root / "checkpoints",
        paths.base_root / "optimization_experiences",
    ]
    assert all(path.is_dir() for path in initialized)
    assert not (paths.root / "analysis").exists()
    assert not (paths.root / "optimization_experiences").exists()


def test_batch_and_epoch_stage_paths_are_sortable_under_team_evaluations(tmp_path: Path) -> None:
    """001 path allocation keeps epoch, batch, and stage dimensions sortable."""
    paths = OrchestratorWorkspacePaths(str(tmp_path / "workspace")).for_team("default_team")

    batch_stage = paths.batch_stage_dir(3, 12, "member_optimization")
    epoch_stage = paths.epoch_evaluation_dir(3)

    assert batch_stage == (tmp_path / "workspace" / "default_team" / "evaluations" / "e003" / "b012" / "mh").resolve()
    assert epoch_stage == (tmp_path / "workspace" / "default_team" / "evaluations" / "e003" / "full").resolve()

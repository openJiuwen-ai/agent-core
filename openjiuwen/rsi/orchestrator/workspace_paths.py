# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Workspace paths for orchestrator-owned artifacts."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

_DEFAULT_TEAM_NAME = "default_team"


@dataclass(frozen=True, slots=True)
class OrchestratorWorkspacePaths:
    """Centralize directory allocation for an optimization workspace."""

    workspace_dir: str
    team_name: str = _DEFAULT_TEAM_NAME

    @property
    def base_root(self) -> Path:
        return Path(self.workspace_dir).expanduser().resolve()

    @property
    def root(self) -> Path:
        return self.base_root / _safe_team_dir_name(self.team_name)

    def for_team(self, team_name: str) -> "OrchestratorWorkspacePaths":
        """Return a path allocator scoped to one Team workspace."""
        return OrchestratorWorkspacePaths(
            workspace_dir=self.workspace_dir,
            team_name=team_name or _DEFAULT_TEAM_NAME,
        )

    def ensure_workspace_structure(self) -> list[Path]:
        """Materialize the standard workspace directories from feat 001."""
        return [
            self._ensure_dir(self.root / "datasets"),
            self._ensure_dir(self.root / "evaluations"),
            self._ensure_dir(self.root / "team_skills"),
            self._ensure_dir(self.root / "member_optimizations"),
            self._ensure_dir(self.root / "checkpoints"),
            self._ensure_dir(self.base_root / "optimization_experiences"),
        ]

    @property
    def context_path(self) -> Path:
        return self.root / "orchestrator_context.yaml"

    @property
    def checkpoint_dir(self) -> Path:
        return self.root / "checkpoints"

    def allocate_dataset_dir(self) -> Path:
        """Allocate the next generated dataset directory."""
        datasets_root = self._ensure_dir(self.root / "datasets")
        index = 1
        while True:
            candidate = datasets_root / f"dataset_{index:03d}"
            if not candidate.exists():
                return candidate
            index += 1

    def dataset_curation_dir(self, epoch: int) -> Path:
        """Return the output root for replay datasets mined from one epoch."""
        return self._ensure_dir(self.root / "datasets" / "curated_replay" / f"e{epoch:03d}")

    def targeted_dataset_dir(self, epoch: int) -> Path:
        """Return the output root for synthetic data generated from curated failures."""
        return self._ensure_dir(self.root / "datasets" / "targeted" / f"e{epoch:03d}")

    def batch_stage_dir(self, epoch: int, batch_index: int, optimization_stage: str) -> Path:
        """Return the output root for one epoch/batch optimization stage."""
        return self._ensure_dir(
            self.evaluation_dir() / f"e{epoch:03d}" / f"b{batch_index:03d}" / _stage_dir_name(optimization_stage)
        )

    def epoch_evaluation_dir(self, epoch: int) -> Path:
        """Return the output root for the full-dataset epoch evaluation."""
        return self._ensure_dir(self.evaluation_dir() / f"e{epoch:03d}" / "full")

    def team_skill_dir(self) -> Path:
        """Return the root directory for Team Skill optimization metadata."""
        return self._ensure_dir(self.root / "team_skills")

    def member_optimization_dir(self) -> Path:
        """Return the root directory for member optimization outputs."""
        return self._ensure_dir(self.root / "member_optimizations")

    def initial_harness_dir(self) -> Path:
        """Return the short root for Team Skill bootstrapped harnesses."""
        return self._ensure_dir(self.base_root / "ih" / _short_team_key(self.team_name))

    def analysis_dir(self) -> Path:
        """Return the root directory for fallback analysis handoff artifacts."""
        return self._ensure_dir(self.root / "analysis")

    def optimization_experience_dir(self) -> Path:
        """Return the root directory for reusable optimization experiences."""
        return self._ensure_dir(self.base_root / "optimization_experiences")

    def evaluation_dir(self) -> Path:
        """Return the root directory for evaluation outputs."""
        return self._ensure_dir(self.root / "evaluations")

    @staticmethod
    def _ensure_dir(path: Path) -> Path:
        path.mkdir(parents=True, exist_ok=True)
        return path


__all__ = [
    "OrchestratorWorkspacePaths",
]


def _safe_team_dir_name(team_name: str) -> str:
    """Convert Team names into stable workspace directory names."""
    normalized = "".join(char if char.isalnum() or char in {"_", ".", "-"} else "_" for char in team_name.strip())
    while "__" in normalized:
        normalized = normalized.replace("__", "_")
    normalized = normalized.strip("._")
    return normalized or _DEFAULT_TEAM_NAME


def _short_team_key(team_name: str) -> str:
    safe = _safe_team_dir_name(team_name)
    digest = hashlib.sha1(safe.encode("utf-8")).hexdigest()[:10]
    return f"t_{digest}"


def _stage_dir_name(stage: str) -> str:
    """Return compact stage names while preserving stable ordering context."""
    stage_names = {
        "team_skill_optimization": "ts",
        "member_optimization": "mh",
        "candidate_gate": "cg",
    }
    return stage_names.get(stage, _safe_team_dir_name(stage)[:24] or "stage")

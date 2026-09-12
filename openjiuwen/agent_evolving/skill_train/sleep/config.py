# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Configuration for skill_train sleep mode."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional


@dataclass
class SleepConfig:
    """Dataclass config for one offline sleep cycle.

    When the held-out gate accepts a candidate (and the run is not dry-run),
    the cycle persists a new skill version via EvolutionStore
    (archive + write + MINOR SemVer bump) for each detected skill only.

    ``skill_name`` / ``skill_init`` optionally bootstrap baseline content for
    one named skill when EvolutionStore has no copy yet. They are not a
    fallback group for tasks without a skill hint.

    ``trajectory_store_dir`` points at a JiuwenSwarm observation dir (or a
    single file) holding ``traces-*.jsonl``; sessions are harvested by
    ``session.id``.
    """

    trajectory_store_dir: str = ""
    session_id: Optional[str] = None
    max_trajectories: int = 40
    max_tasks_per_night: int = 40
    # off: heuristic rubric built from follow-up turns only.
    # llm: additionally ask the optimizer model to synthesize a checklist rubric.
    rubric_synthesis: str = "off"
    project: str = "invoked"
    state_dir: str = ""
    staging_root: str = ""
    # Optional: when consolidating this named skill and store has no content yet,
    # load baseline from skill_init. Not used as a fallback group for hint-less tasks.
    skill_name: str = ""
    skill_init: str = ""
    skills_base_dir: str = ""
    memory_init: str = ""
    backend: str = "model"  # mock | model
    edit_budget: int = 4
    gate_mode: str = "on"
    gate_metric: str = "mixed"
    gate_mixed_weight: float = 0.5
    gate_no_regression: bool = False
    val_fraction: float = 0.34
    test_fraction: float = 0.0
    seed: int = 42
    evolve_skill: bool = True
    evolve_memory: bool = False
    preferences: str = ""
    progress: bool = False

    def resolved_state_dir(self) -> Path:
        if self.state_dir:
            return Path(self.state_dir).expanduser()
        return Path.home() / ".openjiuwen" / "skill-sleep"

    def resolved_staging_root(self) -> Path:
        if self.staging_root:
            return Path(self.staging_root).expanduser()
        return self.resolved_state_dir() / "staging"

    def resolved_trajectory_dir(self) -> Path:
        if not self.trajectory_store_dir:
            raise ValueError("SleepConfig.trajectory_store_dir is required")
        return Path(self.trajectory_store_dir).expanduser()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trajectory_store_dir": self.trajectory_store_dir,
            "session_id": self.session_id,
            "max_trajectories": self.max_trajectories,
            "max_tasks_per_night": self.max_tasks_per_night,
            "rubric_synthesis": self.rubric_synthesis,
            "project": self.project or os.getcwd(),
            "state_dir": str(self.resolved_state_dir()),
            "staging_root": str(self.resolved_staging_root()),
            "skill_name": self.skill_name,
            "skill_init": self.skill_init,
            "skills_base_dir": self.skills_base_dir,
            "memory_init": self.memory_init,
            "backend": self.backend,
            "edit_budget": self.edit_budget,
            "gate_mode": self.gate_mode,
            "gate_metric": self.gate_metric,
            "gate_mixed_weight": self.gate_mixed_weight,
            "gate_no_regression": self.gate_no_regression,
            "val_fraction": self.val_fraction,
            "test_fraction": self.test_fraction,
            "seed": self.seed,
            "evolve_skill": self.evolve_skill,
            "evolve_memory": self.evolve_memory,
            "preferences": self.preferences,
            "progress": self.progress,
        }

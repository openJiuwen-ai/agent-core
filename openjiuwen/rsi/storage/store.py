# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Artifact store facade for the optimization workspace."""

from __future__ import annotations


class ArtifactStore:
    """Manage versioned artifact directories and reference files."""

    def __init__(self, workspace_dir: str) -> None:
        self.workspace_dir = workspace_dir

    def allocate_dataset_dir(self) -> str:
        """TODO: allocate ``datasets/<dataset_id>/`` without creating dataset refs."""
        raise NotImplementedError("TODO: allocate dataset artifact directory")

    def allocate_evaluation_dir(self) -> str:
        """TODO: allocate ``evaluations/<eval_id>/`` for evaluator outputs."""
        raise NotImplementedError("TODO: allocate evaluation artifact directory")

    def allocate_team_skill_version_dir(self) -> str:
        """TODO: allocate a new version directory under ``team_skills/``."""
        raise NotImplementedError("TODO: allocate Team Skill version directory")

    def allocate_harness_version_dir(self, member_name: str) -> str:
        """TODO: allocate a new member harness version directory."""
        raise NotImplementedError("TODO: allocate member harness version directory")

    def write_result_ref(self, output_path: str) -> str:
        """TODO: write final ``result_ref.yaml`` and return its path."""
        raise NotImplementedError("TODO: write optimization result reference")


__all__ = [
    "ArtifactStore",
]

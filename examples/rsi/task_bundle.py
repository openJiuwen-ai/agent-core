# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Resolve the portable task directory used by RSI dataset adapters."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class TaskBundle:
    """Validated paths supplied by one ``task/<id>`` directory."""

    root: Path
    harness_refs: Path
    evaluation_model: Path
    analysis_model: Path
    member_optimization_model: Path
    judge_model: Path | None = None


def load_task_bundle(task_dir: str | Path) -> TaskBundle:
    """Load the files shared by benchmark-specific RSI adapters.

    Dataset contents deliberately remain outside this object. A benchmark
    adapter owns their schema, while this loader owns the common Harness and
    model configuration layout.
    """

    root = Path(task_dir).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"RSI task directory not found: {root}")

    harness_refs = _required_file(root / "harness" / "harness_refs.yaml")
    models_dir = root / "models"
    evaluation_model = _required_file(models_dir / "evaluation.yaml")
    analysis_model = _required_file(models_dir / "analysis.yaml")
    member_optimization_model = _required_file(models_dir / "member_optimization.yaml")
    judge_path = models_dir / "judge.yaml"

    return TaskBundle(
        root=root,
        harness_refs=harness_refs,
        evaluation_model=evaluation_model,
        analysis_model=analysis_model,
        member_optimization_model=member_optimization_model,
        judge_model=judge_path if judge_path.is_file() else None,
    )


def _required_file(path: Path) -> Path:
    if not path.is_file():
        raise FileNotFoundError(f"required RSI task file not found: {path}")
    return path.resolve()


__all__ = ["TaskBundle", "load_task_bundle"]

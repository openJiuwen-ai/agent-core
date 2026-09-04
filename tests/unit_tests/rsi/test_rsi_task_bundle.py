# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for the portable RSI task directory contract."""

from __future__ import annotations

from pathlib import Path

import pytest

from examples.rsi.task_bundle import load_task_bundle


def test_load_task_bundle_resolves_required_files(tmp_path: Path) -> None:
    task_dir = tmp_path / "task" / "sample"
    files = [
        task_dir / "harness" / "harness_refs.yaml",
        task_dir / "models" / "evaluation.yaml",
        task_dir / "models" / "analysis.yaml",
        task_dir / "models" / "member_optimization.yaml",
        task_dir / "models" / "judge.yaml",
    ]
    for path in files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n", encoding="utf-8")

    bundle = load_task_bundle(task_dir)

    assert bundle.root == task_dir.resolve()
    assert bundle.harness_refs == files[0].resolve()
    assert bundle.evaluation_model == files[1].resolve()
    assert bundle.analysis_model == files[2].resolve()
    assert bundle.member_optimization_model == files[3].resolve()
    assert bundle.judge_model == files[4].resolve()


def test_load_task_bundle_rejects_incomplete_layout(tmp_path: Path) -> None:
    task_dir = tmp_path / "task" / "incomplete"
    task_dir.mkdir(parents=True)

    with pytest.raises(FileNotFoundError, match="harness_refs.yaml"):
        load_task_bundle(task_dir)

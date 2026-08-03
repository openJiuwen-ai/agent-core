# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for optimization before/after comparison helpers."""

from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

import yaml

from openjiuwen.rsi.comparison import (
    build_query_case,
    build_report,
    resolve_comparison_refs,
    summarize_eval_ref,
)


def test_resolve_comparison_refs_prefers_initial_and_best_refs() -> None:
    workspace = _test_root("refs") / "workspace" / "web_team"
    workspace.mkdir(parents=True)
    context_path = workspace / "orchestrator_context.yaml"
    context_path.write_text(
        yaml.safe_dump(
            {
                "task_id": "task_001",
                "task": "build a web page",
                "context_path": str(context_path),
                "checkpoint_dir": str(workspace / "checkpoints"),
                "current": {
                    "team_skill_ref_path": "team_skills/current_team_skill",
                    "harness_refs_path": "member_optimizations/current_harness_refs.yaml",
                },
                "best": {
                    "team_skill_ref_path": "checkpoints/epoch_002/team_skill",
                    "harness_refs_path": "checkpoints/epoch_002/harness_refs.yaml",
                    "score": 0.87,
                },
                "history": {
                    "team_skill_optimizations": [
                        {
                            "before_team_skill_ref_path": "initial_team_skills/web_team",
                            "after_team_skill_ref_path": "team_skills/current_team_skill",
                            "eval_ref_path": "evaluations/e001/eval_ref.yaml",
                        }
                    ],
                    "member_optimizations": [
                        {
                            "before_harness_refs_path": "member_optimizations/before.yaml",
                            "after_harness_refs_path": "member_optimizations/after.yaml",
                            "eval_ref_path": "evaluations/e001/eval_ref.yaml",
                        }
                    ],
                },
                "metadata": {"initial_harness_refs_path": "member_optimizations/initial_harnesses/harness_refs.yaml"},
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    refs = resolve_comparison_refs(context_path)

    assert refs.baseline_team_skill_ref_path == str((workspace / "initial_team_skills/web_team").resolve())
    assert refs.baseline_harness_refs_path == str(
        (workspace / "member_optimizations/initial_harnesses/harness_refs.yaml").resolve()
    )
    assert refs.optimized_team_skill_ref_path == str((workspace / "checkpoints/epoch_002/team_skill").resolve())
    assert refs.optimized_harness_refs_path == str((workspace / "checkpoints/epoch_002/harness_refs.yaml").resolve())


def test_build_query_case_declares_reference_contract() -> None:
    case = build_query_case(
        case_id="compare_001",
        query="创建一个新能源官网首页",
        required_files=["index.html", "styles.css"],
        pass_threshold=0.8,
    )

    assert case["case_id"] == "compare_001"
    assert case["input"]["user_message"] == "创建一个新能源官网首页"
    assert case["reference"]["expected_artifacts"]["required_files"] == [
        "index.html",
        "styles.css",
    ]
    assert case["reference"]["judge_rubric"]["pass_threshold"] == 0.8
    assert {item["id"] for item in case["reference"]["required_behaviors"]} >= {
        "deliverable_completeness",
        "requirement_coverage",
        "inspectable_quality",
    }


def test_build_report_summarizes_score_delta() -> None:
    root = _test_root("report")
    baseline_eval = _write_eval_artifacts(
        root / "baseline",
        average_score=0.4,
        passed_cases=1,
        total_cases=2,
    )
    optimized_eval = _write_eval_artifacts(
        root / "optimized",
        average_score=0.9,
        passed_cases=2,
        total_cases=2,
    )

    report = build_report(
        workspace_root=root / "workspace",
        case_path=root / "case.json",
        baseline_refs={
            "team_skill_ref_path": "before_skill",
            "harness_refs_path": "before_harness",
        },
        optimized_refs={
            "team_skill_ref_path": "after_skill",
            "harness_refs_path": "after_harness",
        },
        baseline=summarize_eval_ref(baseline_eval),
        optimized=summarize_eval_ref(optimized_eval),
    )

    assert report["score_delta"] == 0.5
    assert report["passed_cases_delta"] == 1
    assert report["baseline"]["average_score"] == 0.4
    assert report["optimized"]["average_score"] == 0.9


def _test_root(name: str) -> Path:
    root = Path.cwd() / ".local" / "test_before_after_compare" / f"{name}_{uuid.uuid4().hex}"
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    return root


def _write_eval_artifacts(
    eval_dir: Path,
    *,
    average_score: float,
    passed_cases: int,
    total_cases: int,
) -> Path:
    eval_dir.mkdir(parents=True)
    summary_path = eval_dir / "summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "average_score": average_score,
                "passed_cases": passed_cases,
                "total_cases": total_cases,
            }
        ),
        encoding="utf-8",
    )
    eval_ref_path = eval_dir / "eval_ref.yaml"
    eval_ref_path.write_text(
        yaml.safe_dump(
            {
                "eval_id": eval_dir.name,
                "eval_dir": str(eval_dir),
                "summary_path": str(summary_path),
                "cases": [],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return eval_ref_path

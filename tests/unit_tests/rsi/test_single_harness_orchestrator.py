# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for the standalone single-harness optimization facade."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml

from openjiuwen.rsi.schema import DatasetArtifact
from openjiuwen.rsi.single_harness import (
    SingleHarnessOptimizationOrchestrator,
    SingleHarnessOptimizationRequest,
)


class _FakeEvaluator:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def evaluate_batch(
        self,
        *,
        cases: list[dict[str, Any]],
        team_skill_ref_path: str,
        harness_refs_path: str,
        output_dir: str,
        context_path: str | None = None,
        dataset: DatasetArtifact | None = None,
    ) -> str:
        self.calls.append(
            {
                "cases": cases,
                "team_skill_ref_path": team_skill_ref_path,
                "harness_refs_path": harness_refs_path,
                "output_dir": output_dir,
                "context_path": context_path,
                "dataset": dataset,
            }
        )
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        eval_ref = out / "eval_ref.yaml"
        yaml.safe_dump(
            {
                "eval_id": out.name,
                "team_name": "",
                "team_skill_ref_path": team_skill_ref_path,
                "harness_refs_path": harness_refs_path,
                "summary_path": str(out / "summary.json"),
                "cases": [
                    {
                        "case_id": case["case_id"],
                        "status": "failed",
                        "score": 0.0,
                    }
                    for case in cases
                ],
            },
            eval_ref.open("w", encoding="utf-8"),
            allow_unicode=True,
            sort_keys=False,
        )
        (out / "summary.json").write_text(
            json.dumps({"average_score": 0.0}, ensure_ascii=False),
            encoding="utf-8",
        )
        return str(eval_ref)


class _FakeAnalyzer:
    def __init__(self) -> None:
        self.calls: list[Any] = []

    async def analyze(self, invocation: Any) -> str:
        self.calls.append(invocation)
        out = Path(invocation.output_dir)
        out.mkdir(parents=True, exist_ok=True)
        analysis_ref = out / "analysis_ref.yaml"
        issues_path = out / "issues.yaml"
        yaml.safe_dump(
            {"issues": [{"issue_id": "i1", "severity": "high"}]},
            issues_path.open("w", encoding="utf-8"),
            allow_unicode=True,
            sort_keys=False,
        )
        yaml.safe_dump(
            {
                "analysis_id": "analysis_001",
                "eval_ref_path": invocation.eval_ref_path,
                "issues_path": str(issues_path),
            },
            analysis_ref.open("w", encoding="utf-8"),
            allow_unicode=True,
            sort_keys=False,
        )
        return str(analysis_ref)


class _FakeMemberOptimizer:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    async def optimize(
        self,
        eval_ref_path: str,
        analysis_result_path: str,
        harness_refs_path: str,
        output_dir: str,
    ) -> str:
        self.calls.append(
            {
                "eval_ref_path": eval_ref_path,
                "analysis_result_path": analysis_result_path,
                "harness_refs_path": harness_refs_path,
                "output_dir": output_dir,
            }
        )
        out = Path(output_dir)
        run_dir = out / "member_optimization_001"
        run_dir.mkdir(parents=True, exist_ok=True)
        current_refs = out / "current_harness_refs.yaml"
        yaml.safe_dump(
            {
                "version": 1,
                "harness_refs": {"solver": "optimized-solver"},
                "published_roles": ["solver"],
            },
            current_refs.open("w", encoding="utf-8"),
            sort_keys=False,
        )
        ref = run_dir / "member_optimization_ref.yaml"
        yaml.safe_dump(
            {
                "status": "success",
                "optimized_harness_refs_path": str(current_refs),
                "published_roles": ["solver"],
            },
            ref.open("w", encoding="utf-8"),
            sort_keys=False,
        )
        return str(ref)


class _ForbiddenCollaborator:
    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"single harness must not call team collaborator: {name}")


def test_single_harness_uses_existing_dataset_and_member_optimizer_only(tmp_path: Path) -> None:
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(
        json.dumps({"cases": [{"case_id": "tb_001", "input": {"user_message": "fix it"}}]}),
        encoding="utf-8",
    )
    harness_refs_path = tmp_path / "harness_refs.yaml"
    yaml.safe_dump(
        {"harness_refs": {"solver": str(tmp_path / "solver")}},
        harness_refs_path.open("w", encoding="utf-8"),
        sort_keys=False,
    )
    evaluator = _FakeEvaluator()
    analyzer = _FakeAnalyzer()
    optimizer = _FakeMemberOptimizer()
    orchestrator = SingleHarnessOptimizationOrchestrator(
        evaluator=evaluator,
        analyzer=analyzer,
        member_optimizer=optimizer,
        dataset_generator=_ForbiddenCollaborator(),
        team_skill_generator=_ForbiddenCollaborator(),
        team_skill_optimizer=_ForbiddenCollaborator(),
    )

    result = orchestrator.run_sync(
        SingleHarnessOptimizationRequest(
            dataset_files=[str(cases_path)],
            harness_refs_path=str(harness_refs_path),
            output_dir=str(tmp_path / "run"),
        )
    )

    assert result.dataset.dataset_files == [str(cases_path)]
    assert Path(result.seed_eval_ref_path).parts[-2:] == ("seed", "eval_ref.yaml")
    assert Path(result.analysis_ref_path).parts[-2:] == ("analysis", "analysis_ref.yaml")
    assert Path(result.member_optimization_ref_path).parts[-2:] == (
        "member_optimization_001",
        "member_optimization_ref.yaml",
    )
    assert result.optimized_harness_refs_path.endswith("current_harness_refs.yaml")
    assert evaluator.calls[0]["team_skill_ref_path"] == ""
    assert evaluator.calls[0]["harness_refs_path"] == str(harness_refs_path)
    assert optimizer.calls[0]["harness_refs_path"] == str(harness_refs_path)


def test_single_harness_can_run_seed_only_without_optimization(tmp_path: Path) -> None:
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(json.dumps([{"case_id": "tb_001", "input": "fix it"}]), encoding="utf-8")
    harness_refs_path = tmp_path / "harness_refs.yaml"
    yaml.safe_dump(
        {"harness_refs": {"solver": str(tmp_path / "solver")}},
        harness_refs_path.open("w", encoding="utf-8"),
        sort_keys=False,
    )
    evaluator = _FakeEvaluator()
    analyzer = _FakeAnalyzer()
    optimizer = _FakeMemberOptimizer()
    orchestrator = SingleHarnessOptimizationOrchestrator(
        evaluator=evaluator,
        analyzer=analyzer,
        member_optimizer=optimizer,
    )

    result = orchestrator.run_sync(
        SingleHarnessOptimizationRequest(
            dataset_files=[str(cases_path)],
            harness_refs_path=str(harness_refs_path),
            output_dir=str(tmp_path / "run"),
            optimize=False,
        )
    )

    assert result.analysis_ref_path == ""
    assert result.member_optimization_ref_path == ""
    assert analyzer.calls == []
    assert optimizer.calls == []
    assert evaluator.calls[0]["team_skill_ref_path"] == ""


def test_single_harness_rejects_team_backend_config() -> None:
    config = SimpleNamespace(evaluator=SimpleNamespace(backend="local"))

    try:
        SingleHarnessOptimizationOrchestrator(
            config=config,  # type: ignore[arg-type]
            evaluator=_FakeEvaluator(),
            analyzer=_FakeAnalyzer(),
            member_optimizer=_FakeMemberOptimizer(),
        )
    except ValueError as exc:
        assert "single_harness" in str(exc)
    else:
        raise AssertionError("single harness facade must reject non-single backend config")

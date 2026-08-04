# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Standalone optimization facade for one Expert Harness.

This module intentionally does not depend on Team Skill generation, Team
execution, or generated datasets.  It reuses the existing evaluator/analyzer
and MemberOptimizer contracts with a single ``solver`` harness ref.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import yaml

from openjiuwen.rsi.config import (
    AutoCoordinatingHarnessConfig,
)
from openjiuwen.rsi.evaluation_result_analyzer import (
    EvaluationResultAnalyzer,
)
from openjiuwen.rsi.evaluator import TeamEvaluator
from openjiuwen.rsi.member_optimizer import MemberOptimizer
from openjiuwen.rsi.schema import (
    DatasetArtifact,
    EvaluationResultAnalysisInvocation,
)


class _Evaluator(Protocol):
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
        """Evaluate one batch and return its artifact reference."""
        ...


class _Analyzer(Protocol):
    async def analyze(self, invocation: EvaluationResultAnalysisInvocation) -> str:
        """Analyze an evaluation invocation and return its artifact reference."""
        ...


class _MemberOptimizer(Protocol):
    async def optimize(
        self,
        eval_ref_path: str,
        analysis_result_path: str,
        harness_refs_path: str,
        output_dir: str,
    ) -> str:
        """Optimize a member and return its artifact reference."""
        ...


@dataclass(frozen=True, slots=True)
class SingleHarnessOptimizationRequest:
    """Inputs for optimizing one standalone Expert Harness."""

    dataset_files: list[str]
    harness_refs_path: str
    output_dir: str
    dataset_id: str = "single_harness_benchmark"
    optimize: bool = True


@dataclass(frozen=True, slots=True)
class SingleHarnessOptimizationResult:
    """Artifacts produced by the standalone single-harness facade."""

    dataset: DatasetArtifact
    seed_eval_ref_path: str
    analysis_ref_path: str = ""
    member_optimization_ref_path: str = ""
    optimized_harness_refs_path: str = ""


class SingleHarnessOptimizationOrchestrator:
    """Run benchmark evaluation and optional harness optimization for one harness.

    The class is deliberately small: callers supply an existing benchmark
    dataset and an existing ``harness_refs.yaml`` with exactly one role.  No
    Team Skill or synthetic dataset stage is part of this flow.
    """

    def __init__(
        self,
        *,
        config: AutoCoordinatingHarnessConfig | None = None,
        evaluator: _Evaluator | None = None,
        analyzer: _Analyzer | None = None,
        member_optimizer: _MemberOptimizer | None = None,
        dataset_generator: Any | None = None,
        team_skill_generator: Any | None = None,
        team_skill_optimizer: Any | None = None,
    ) -> None:
        missing_components = any(component is None for component in (evaluator, analyzer, member_optimizer))
        if config is None and missing_components:
            raise ValueError("config is required unless evaluator, analyzer, and member_optimizer are provided")
        if config is not None:
            backend = getattr(config.evaluator, "backend", "")
            if backend != "single_harness":
                raise ValueError(
                    "SingleHarnessOptimizationOrchestrator requires config.evaluator.backend='single_harness'"
                )
        self.config = config
        self.evaluator = evaluator or TeamEvaluator(config.evaluator)  # type: ignore[union-attr]
        self.analyzer = analyzer or EvaluationResultAnalyzer(
            config.evaluation_result_analyzer  # type: ignore[union-attr]
        )
        self.member_optimizer = member_optimizer or MemberOptimizer(
            config.member_optimizer  # type: ignore[union-attr]
        )
        self._dataset_generator = dataset_generator
        self._team_skill_generator = team_skill_generator
        self._team_skill_optimizer = team_skill_optimizer

    async def run(self, request: SingleHarnessOptimizationRequest) -> SingleHarnessOptimizationResult:
        """Run seed benchmark evaluation and optionally optimize the solver harness."""
        _ = self._dataset_generator
        _ = self._team_skill_generator
        _ = self._team_skill_optimizer
        output_dir = Path(request.output_dir).expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        dataset = _dataset_artifact(request)
        cases = _load_cases(request.dataset_files)
        _validate_single_harness_refs(request.harness_refs_path)

        seed_eval_ref_path = await self.evaluator.evaluate_batch(
            cases=cases,
            team_skill_ref_path="",
            harness_refs_path=request.harness_refs_path,
            output_dir=str(output_dir / "seed"),
            dataset=dataset,
        )
        if not request.optimize:
            return SingleHarnessOptimizationResult(
                dataset=dataset,
                seed_eval_ref_path=seed_eval_ref_path,
            )

        analysis_ref_path = await self.analyzer.analyze(
            _analysis_invocation_from_eval_ref(
                eval_ref_path=seed_eval_ref_path,
                output_dir=str(output_dir / "analysis"),
                harness_refs_path=request.harness_refs_path,
            )
        )
        member_optimization_ref_path = await self.member_optimizer.optimize(
            eval_ref_path=seed_eval_ref_path,
            analysis_result_path=analysis_ref_path,
            harness_refs_path=request.harness_refs_path,
            output_dir=str(output_dir / "member_optimizations"),
        )
        optimized_harness_refs_path = _optimized_refs_from_member_ref(member_optimization_ref_path)
        return SingleHarnessOptimizationResult(
            dataset=dataset,
            seed_eval_ref_path=seed_eval_ref_path,
            analysis_ref_path=analysis_ref_path,
            member_optimization_ref_path=member_optimization_ref_path,
            optimized_harness_refs_path=optimized_harness_refs_path,
        )

    def run_sync(self, request: SingleHarnessOptimizationRequest) -> SingleHarnessOptimizationResult:
        """Synchronous convenience wrapper for scripts and tests."""
        return asyncio.run(self.run(request))


def _dataset_artifact(request: SingleHarnessOptimizationRequest) -> DatasetArtifact:
    files = [str(Path(path).expanduser().resolve()) for path in request.dataset_files]
    dirs = {str(Path(path).expanduser().resolve().parent) for path in request.dataset_files}
    dataset_dir = next(iter(dirs)) if len(dirs) == 1 else str(Path(request.output_dir).resolve())
    return DatasetArtifact(
        dataset_id=request.dataset_id,
        dataset_dir=dataset_dir,
        dataset_files=files,
        cases=len(_load_cases(files)),
    )


def _load_cases(dataset_files: list[str]) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for dataset_file in dataset_files:
        path = Path(dataset_file).expanduser().resolve()
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
        if isinstance(data, dict) and isinstance(data.get("cases"), list):
            raw_cases = data["cases"]
        elif isinstance(data, dict):
            raw_cases = [data]
        elif isinstance(data, list):
            raw_cases = data
        else:
            raise ValueError(f"dataset json must contain case mappings: {path}")
        for index, case in enumerate(raw_cases, start=1):
            if not isinstance(case, dict):
                raise ValueError(f"dataset case must be a mapping: {path}#{index}")
            cases.append({**case, "case_path": str(path), "case_index": index})
    return cases


def _validate_single_harness_refs(harness_refs_path: str) -> None:
    refs = _read_harness_refs(harness_refs_path)
    if len(refs) != 1:
        raise ValueError(
            f"single harness optimization requires exactly one harness ref; got {len(refs)} from {harness_refs_path}"
        )


def _read_harness_refs(harness_refs_path: str) -> dict[str, str]:
    path = Path(harness_refs_path).expanduser().resolve()
    with path.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file) or {}
    if not isinstance(data, dict):
        raise ValueError(f"harness refs must be a mapping: {path}")
    refs = data.get("harness_refs")
    if isinstance(refs, dict):
        return {str(role): str(ref) for role, ref in refs.items() if str(ref).strip()}
    return {
        str(role): str(ref)
        for role, ref in data.items()
        if isinstance(ref, str) and role not in {"version", "source_harness_refs_path"}
    }


def _optimized_refs_from_member_ref(member_optimization_ref_path: str) -> str:
    path = Path(member_optimization_ref_path).expanduser().resolve()
    with path.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file) or {}
    if not isinstance(data, dict):
        return ""
    return str(data.get("optimized_harness_refs_path", "") or "")


def _analysis_invocation_from_eval_ref(
    *,
    eval_ref_path: str,
    output_dir: str,
    harness_refs_path: str,
) -> EvaluationResultAnalysisInvocation:
    path = Path(eval_ref_path).expanduser().resolve()
    with path.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file) or {}
    if not isinstance(data, dict):
        data = {}
    eval_dir = Path(str(data.get("eval_dir") or path.parent)).expanduser().resolve()
    case_results_dir = str(data.get("case_results_dir") or eval_dir / "cases")
    case_traces_dir = str(data.get("case_traces_dir") or case_results_dir)
    return EvaluationResultAnalysisInvocation(
        eval_ref_path=str(path),
        case_results_dir=case_results_dir,
        case_traces_dir=case_traces_dir,
        team_skill_ref_path="",
        harness_refs_path=str(data.get("harness_refs_path") or harness_refs_path),
        output_dir=output_dir,
        source_stage="single_harness_seed",
    )


__all__ = [
    "SingleHarnessOptimizationOrchestrator",
    "SingleHarnessOptimizationRequest",
    "SingleHarnessOptimizationResult",
]

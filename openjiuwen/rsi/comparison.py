# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Before/after evaluation comparison utilities for optimization runs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from openjiuwen.rsi.config import EvaluatorConfig
from openjiuwen.rsi.evaluator import TeamEvaluator
from openjiuwen.rsi.orchestrator.context import (
    OrchestratorContextStore,
)


@dataclass(frozen=True, slots=True)
class ComparisonRefs:
    """Artifact refs used for one before/after comparison."""

    baseline_team_skill_ref_path: str
    baseline_harness_refs_path: str
    optimized_team_skill_ref_path: str
    optimized_harness_refs_path: str


def resolve_comparison_refs(
    context_path: str | Path,
    *,
    baseline_team_skill_ref_path: str = "",
    baseline_harness_refs_path: str = "",
    optimized_team_skill_ref_path: str = "",
    optimized_harness_refs_path: str = "",
) -> ComparisonRefs:
    """Resolve baseline and optimized refs from an orchestrator context.

    Baseline refs come from the initial artifacts recorded by the run. Optimized
    refs come from ``best`` when present, because best is promoted only by full
    evaluation.
    """
    context_file = Path(context_path).expanduser().resolve()
    context = OrchestratorContextStore(str(context_file)).load()
    base_dir = context_file.parent

    first_team_history = (
        context.history.team_skill_optimizations[0] if context.history.team_skill_optimizations else None
    )
    first_member_history = context.history.member_optimizations[0] if context.history.member_optimizations else None

    baseline_team = _first_non_empty(
        baseline_team_skill_ref_path,
        (first_team_history.before_team_skill_ref_path if first_team_history is not None else ""),
        str(context.metadata.get("initial_team_skill_ref_path", "")),
        context.current.team_skill_ref_path,
    )
    baseline_harness = _first_non_empty(
        baseline_harness_refs_path,
        str(context.metadata.get("initial_harness_refs_path", "")),
        (first_member_history.before_harness_refs_path if first_member_history is not None else ""),
        context.current.harness_refs_path,
    )
    optimized_team = _first_non_empty(
        optimized_team_skill_ref_path,
        str(context.best.team_skill_ref_path or ""),
        context.current.team_skill_ref_path,
        baseline_team,
    )
    optimized_harness = _first_non_empty(
        optimized_harness_refs_path,
        str(context.best.harness_refs_path or ""),
        context.current.harness_refs_path,
        baseline_harness,
    )

    if not baseline_harness:
        raise ValueError(f"cannot resolve baseline harness refs from {context_file}")
    if not optimized_harness:
        raise ValueError(f"cannot resolve optimized harness refs from {context_file}")

    return ComparisonRefs(
        baseline_team_skill_ref_path=_absolute_ref(baseline_team, base_dir),
        baseline_harness_refs_path=_absolute_ref(baseline_harness, base_dir),
        optimized_team_skill_ref_path=_absolute_ref(optimized_team, base_dir),
        optimized_harness_refs_path=_absolute_ref(optimized_harness, base_dir),
    )


def build_query_case(
    *,
    case_id: str,
    query: str,
    required_files: list[str] | None = None,
    pass_threshold: float = 0.8,
) -> dict[str, Any]:
    """Build a generic single evaluation case from a user query."""
    deliverables = [str(item) for item in required_files or [] if str(item).strip()]
    return {
        "case_id": case_id,
        "input": {
            "user_message": query,
        },
        "reference": {
            "expected_artifacts": {
                "required_files": deliverables,
            },
            "required_behaviors": [
                {
                    "id": "deliverable_completeness",
                    "description": "All required final deliverables are produced and inspectable.",
                    "weight": 1.0,
                    "rubric": "Score low if required files are missing, empty, misplaced, or not readable.",
                },
                {
                    "id": "requirement_coverage",
                    "description": (
                        "The result covers the concrete user requirements instead of returning a generic answer."
                    ),
                    "weight": 1.0,
                    "rubric": (
                        "Score low if important entities, constraints, sections, or requested outputs are absent."
                    ),
                },
                {
                    "id": "structure_and_coherence",
                    "description": "The delivered result is organized, coherent, and usable for the requested task.",
                    "weight": 1.0,
                    "rubric": "Score low if the structure is fragmented, contradictory, or hard to consume.",
                },
                {
                    "id": "inspectable_quality",
                    "description": (
                        "The evidence and artifacts are concrete enough for downstream review and optimization."
                    ),
                    "weight": 1.0,
                    "rubric": (
                        "Score low if the output only claims completion without concrete artifacts or "
                        "verifiable content."
                    ),
                },
            ],
            "forbidden_behaviors": [
                {
                    "id": "missing_required_deliverables",
                    "description": "The agent reports success while required final deliverables are absent.",
                    "penalty": 0.4,
                }
            ],
            "judge_rubric": {
                "pass_threshold": pass_threshold,
            },
        },
        "metadata": {
            "source": "before_after_compare",
            "comparison_case": True,
        },
    }


def load_cases(case_path: str | Path) -> list[dict[str, Any]]:
    """Load comparison cases from a JSON file."""
    path = Path(case_path).expanduser().resolve()
    with open(path, "r", encoding="utf-8") as file:
        data = json.load(file)
    if isinstance(data, dict) and isinstance(data.get("cases"), list):
        raw_cases = data["cases"]
    elif isinstance(data, dict):
        raw_cases = [data]
    elif isinstance(data, list):
        raw_cases = data
    else:
        raise ValueError(f"case file must contain a mapping or list: {path}")

    cases: list[dict[str, Any]] = []
    for index, case in enumerate(raw_cases, start=1):
        if not isinstance(case, dict):
            raise ValueError(f"case must be a mapping: {path}#{index}")
        cases.append({**case, "case_path": str(path), "case_index": index})
    return cases


def summarize_eval_ref(eval_ref_path: str | Path) -> dict[str, Any]:
    """Return a compact summary for an evaluator ``eval_ref.yaml``."""
    path = Path(eval_ref_path).expanduser().resolve()
    with open(path, "r", encoding="utf-8") as file:
        eval_ref = yaml.safe_load(file) or {}
    if not isinstance(eval_ref, dict):
        raise ValueError(f"eval_ref must contain a mapping: {path}")

    summary: dict[str, Any] = {}
    summary_path = eval_ref.get("summary_path")
    if summary_path:
        summary_file = Path(str(summary_path)).expanduser().resolve()
        with open(summary_file, "r", encoding="utf-8") as file:
            loaded = json.load(file)
        if isinstance(loaded, dict):
            summary = loaded

    return {
        "eval_ref_path": str(path),
        "eval_dir": str(eval_ref.get("eval_dir", "")),
        "summary_path": str(summary_path or ""),
        "total_cases": _optional_int(summary.get("total_cases")),
        "passed_cases": _optional_int(summary.get("passed_cases")),
        "failed_cases": _optional_int(summary.get("failed_cases")),
        "average_score": _optional_float(summary.get("average_score")),
        "evaluation_method": str(summary.get("evaluation_method", "")),
        "case_results": list(summary.get("case_results", [])) if isinstance(summary.get("case_results"), list) else [],
        "cases": eval_ref.get("cases", []),
    }


def build_report(
    *,
    workspace_root: str | Path,
    case_path: str | Path,
    baseline_refs: dict[str, str],
    optimized_refs: dict[str, str],
    baseline: dict[str, Any],
    optimized: dict[str, Any],
) -> dict[str, Any]:
    """Build a serializable before/after comparison report."""
    baseline_score = baseline.get("average_score")
    optimized_score = optimized.get("average_score")
    baseline_passed = baseline.get("passed_cases")
    optimized_passed = optimized.get("passed_cases")
    return {
        "created_at": datetime.now(UTC).astimezone().isoformat(),
        "workspace_root": str(Path(workspace_root).expanduser().resolve()),
        "case_path": (str(Path(case_path).expanduser().resolve()) if str(case_path) else ""),
        "baseline_refs": baseline_refs,
        "optimized_refs": optimized_refs,
        "baseline": baseline,
        "optimized": optimized,
        "score_delta": _delta(optimized_score, baseline_score),
        "passed_cases_delta": _int_delta(optimized_passed, baseline_passed),
    }


def write_report_files(report: dict[str, Any], output_dir: str | Path) -> dict[str, str]:
    """Write YAML and Markdown report files."""
    out = Path(output_dir).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    yaml_path = out / "comparison_report.yaml"
    markdown_path = out / "comparison_report.md"
    yaml_path.write_text(
        yaml.safe_dump(report, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    markdown_path.write_text(_format_report_markdown(report), encoding="utf-8")
    return {
        "yaml": str(yaml_path),
        "markdown": str(markdown_path),
    }


async def run_before_after_comparison(
    *,
    context_path: str | Path,
    output_dir: str | Path,
    cases: list[dict[str, Any]],
    run_model_config_ref: str,
    judge_model_config_ref: str = "",
    backend: str = "local",
    evaluation_method: str = "llm_as_judge",
    success_score: float = 1.0,
    baseline_team_skill_ref_path: str = "",
    baseline_harness_refs_path: str = "",
    optimized_team_skill_ref_path: str = "",
    optimized_harness_refs_path: str = "",
) -> dict[str, Any]:
    """Evaluate the same cases with baseline and optimized refs."""
    context_file = Path(context_path).expanduser().resolve()
    workspace_root = context_file.parent
    refs = resolve_comparison_refs(
        context_file,
        baseline_team_skill_ref_path=baseline_team_skill_ref_path,
        baseline_harness_refs_path=baseline_harness_refs_path,
        optimized_team_skill_ref_path=optimized_team_skill_ref_path,
        optimized_harness_refs_path=optimized_harness_refs_path,
    )
    out = Path(output_dir).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    case_file = out / "comparison_cases.json"
    case_file.write_text(
        json.dumps({"cases": cases}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    evaluator = TeamEvaluator(
        EvaluatorConfig(
            model_config_ref=run_model_config_ref,
            judge_model_config_ref=judge_model_config_ref,
            backend=backend,
            evaluation_method=evaluation_method,
            success_score=success_score,
        )
    )
    baseline_eval_ref = await evaluator.evaluate_batch(
        cases=cases,
        team_skill_ref_path=refs.baseline_team_skill_ref_path,
        harness_refs_path=refs.baseline_harness_refs_path,
        output_dir=str(out / "baseline"),
    )
    optimized_eval_ref = await evaluator.evaluate_batch(
        cases=cases,
        team_skill_ref_path=refs.optimized_team_skill_ref_path,
        harness_refs_path=refs.optimized_harness_refs_path,
        output_dir=str(out / "optimized"),
    )
    report = build_report(
        workspace_root=workspace_root,
        case_path=case_file,
        baseline_refs={
            "team_skill_ref_path": refs.baseline_team_skill_ref_path,
            "harness_refs_path": refs.baseline_harness_refs_path,
        },
        optimized_refs={
            "team_skill_ref_path": refs.optimized_team_skill_ref_path,
            "harness_refs_path": refs.optimized_harness_refs_path,
        },
        baseline=summarize_eval_ref(baseline_eval_ref),
        optimized=summarize_eval_ref(optimized_eval_ref),
    )
    report["report_paths"] = write_report_files(report, out)
    return report


def next_comparison_dir(workspace_root: str | Path) -> Path:
    """Return a short timestamped output directory under a workspace."""
    stamp = datetime.now(UTC).astimezone().strftime("%Y%m%d_%H%M%S")
    return Path(workspace_root).expanduser().resolve() / "comparisons" / f"cmp_{stamp}"


def context_path_from_workspace(workspace_root: str | Path) -> Path:
    """Resolve ``orchestrator_context.yaml`` from a team workspace root."""
    root = Path(workspace_root).expanduser().resolve()
    context_path = root / "orchestrator_context.yaml"
    if not context_path.is_file():
        raise FileNotFoundError(f"orchestrator_context.yaml not found under {root}")
    return context_path


__all__ = [
    "ComparisonRefs",
    "build_query_case",
    "build_report",
    "context_path_from_workspace",
    "load_cases",
    "next_comparison_dir",
    "resolve_comparison_refs",
    "run_before_after_comparison",
    "summarize_eval_ref",
    "write_report_files",
]


def _first_non_empty(*values: str) -> str:
    for value in values:
        if value:
            return value
    return ""


def _absolute_ref(value: str, base_dir: Path) -> str:
    if not value:
        return ""
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return str(path.resolve())


def _optional_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float | str):
        return float(value)
    return None


def _optional_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float | str):
        return int(value)
    return None


def _delta(left: Any, right: Any) -> float | None:
    left_value = _optional_float(left)
    right_value = _optional_float(right)
    if left_value is None or right_value is None:
        return None
    return round(left_value - right_value, 6)


def _int_delta(left: Any, right: Any) -> int | None:
    left_value = _optional_int(left)
    right_value = _optional_int(right)
    if left_value is None or right_value is None:
        return None
    return left_value - right_value


def _format_report_markdown(report: dict[str, Any]) -> str:
    baseline = report.get("baseline", {})
    optimized = report.get("optimized", {})
    baseline_refs = report.get("baseline_refs", {})
    optimized_refs = report.get("optimized_refs", {})
    lines = [
        "# Optimization Before/After Comparison",
        "",
        f"- Workspace: `{report.get('workspace_root', '')}`",
        f"- Case file: `{report.get('case_path', '')}`",
        f"- Score delta: `{report.get('score_delta')}`",
        f"- Passed cases delta: `{report.get('passed_cases_delta')}`",
        "",
        "## Metrics",
        "",
        "| Side | Average score | Passed | Total | Eval ref |",
        "| --- | ---: | ---: | ---: | --- |",
        (
            f"| Baseline | {baseline.get('average_score')} | "
            f"{baseline.get('passed_cases')} | {baseline.get('total_cases')} | "
            f"`{baseline.get('eval_ref_path', '')}` |"
        ),
        (
            f"| Optimized | {optimized.get('average_score')} | "
            f"{optimized.get('passed_cases')} | {optimized.get('total_cases')} | "
            f"`{optimized.get('eval_ref_path', '')}` |"
        ),
        "",
        "## Refs",
        "",
        "| Side | Team Skill | Harness refs |",
        "| --- | --- | --- |",
        (
            f"| Baseline | `{baseline_refs.get('team_skill_ref_path', '')}` | "
            f"`{baseline_refs.get('harness_refs_path', '')}` |"
        ),
        (
            f"| Optimized | `{optimized_refs.get('team_skill_ref_path', '')}` | "
            f"`{optimized_refs.get('harness_refs_path', '')}` |"
        ),
        "",
    ]
    return "\n".join(lines)

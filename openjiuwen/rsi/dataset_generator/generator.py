# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Model-driven synthetic evaluation dataset generation."""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from openjiuwen.rsi.config import DatasetGeneratorConfig
from openjiuwen.rsi.evaluator.judge_skills import (
    JudgeSkill,
    available_judge_skills,
    resolve_judge_skills_by_name,
    resolve_judge_skills_for_task,
)
from openjiuwen.rsi.member_optimizer.agents.factory import (
    load_member_optimizer_model,
)
from openjiuwen.rsi.model_call import (
    DEFAULT_MODEL_CALL_MAX_RETRIES,
    RetryableModelOutputError,
    run_model_call_with_retries,
)
from openjiuwen.rsi.schema import DatasetArtifact
from openjiuwen.rsi.text_encoding import (
    repair_payload_mojibake,
    repair_text_mojibake,
)
from openjiuwen.rsi.usage_recorder import (
    llm_usage_scope,
    record_llm_usage,
)

DATASET_SOURCE = "llm_synthetic_evaluation_dataset"
DATASET_FILENAME = "synthetic_cases.json"
DEFAULT_MIN_CASES = 6
MAX_TASK_ANALYSIS_ATTEMPTS = 2
MAX_CASE_GENERATION_ATTEMPTS = 3
GENERIC_TRAINING_INTENT_TAXONOMY: tuple[dict[str, Any], ...] = (
    {
        "intent": "team_coordination_and_role_design",
        "description": (
            "Probe whether the generated team structure, role split, ownership, "
            "handoff, and leadership plan fit the task."
        ),
        "expected_optimization_target": "team_skill",
        "target_surfaces": ["skill"],
    },
    {
        "intent": "output_contract_and_completion",
        "description": (
            "Probe whether a member can finish the requested deliverable with the "
            "required file shape, sections, constraints, and completion gate."
        ),
        "expected_optimization_target": "member_harness",
        "target_surfaces": ["prompt_section"],
    },
    {
        "intent": "task_methodology_and_domain_skill",
        "description": (
            "Probe whether a member has a reusable task method instead of generic advice or shallow domain reasoning."
        ),
        "expected_optimization_target": "member_harness",
        "target_surfaces": ["skill", "prompt_section"],
    },
    {
        "intent": "deterministic_execution_or_validation",
        "description": (
            "Probe whether the harness needs deterministic checks, calculations, "
            "artifact inspection, or validation behavior."
        ),
        "expected_optimization_target": "member_harness",
        "target_surfaces": ["tool"],
    },
    {
        "intent": "quality_review_and_revision",
        "description": (
            "Probe whether a member can review quality against explicit criteria and revise before final answer."
        ),
        "expected_optimization_target": "member_harness",
        "target_surfaces": ["skill", "prompt_section"],
    },
    {
        "intent": "runtime_or_tooling_gap",
        "description": (
            "Probe whether task success depends on using or creating the right "
            "tooling/runtime capability rather than only adding prose instructions."
        ),
        "expected_optimization_target": "member_harness",
        "target_surfaces": ["tool", "prompt_section"],
    },
)

DatasetLlmRunner = Callable[[str, Path], Awaitable[str]]


class DatasetGenerator:
    """Generate judgeable evaluation examples from a task using an LLM."""

    def __init__(
        self,
        config: DatasetGeneratorConfig,
        *,
        llm_runner: DatasetLlmRunner | None = None,
    ) -> None:
        self.config = config
        self._llm_runner = llm_runner

    async def generate(self, task: str, output_dir: str) -> DatasetArtifact:
        """Generate a task-specific JSON dataset consumed by DataLoader/Evaluator."""
        normalized_task = _normalize_task(task)
        output_path = Path(output_dir).expanduser().resolve()
        output_path.mkdir(parents=True, exist_ok=True)

        known_failures_payload = _load_known_failures_payload(self.config.known_failures_ref)
        target_count = _target_case_count(
            self.config.min_cases,
            known_failures_payload=known_failures_payload,
        )
        known_failures_text = _known_failures_text(known_failures_payload)
        task_analysis = await self._generate_task_analysis(
            task=normalized_task,
            output_path=output_path,
            target_case_count=target_count,
            known_failures_text=known_failures_text,
        )
        case_specs = await self._generate_case_specs_stage(
            task=normalized_task,
            output_path=output_path,
            target_case_count=target_count,
            task_analysis=task_analysis,
            known_failures_text=known_failures_text,
        )
        task_analysis["case_specs"] = case_specs

        cases: list[dict[str, Any]] = []
        seen_user_messages: set[str] = set()
        quality_report: dict[str, list[dict[str, Any]]] = {
            "accepted_cases": [],
            "rejected_cases": [],
        }
        for case_index in range(1, target_count + 1):
            case_spec = case_specs[case_index - 1]
            case, accepted_quality, rejected_qualities = await self._generate_quality_checked_case(
                task=normalized_task,
                task_analysis=task_analysis,
                case_spec=case_spec,
                seen_user_messages=seen_user_messages,
                case_index=case_index,
                case_count=target_count,
                output_path=output_path,
            )
            seen_user_messages.add(_canonical_user_message(case["input"]["user_message"]))
            cases.append(case)
            quality_report["rejected_cases"].extend(rejected_qualities)
            quality_report["accepted_cases"].append(accepted_quality)

        analysis_path = output_path / "_artifacts" / "task_analysis.json"
        _write_json(analysis_path, task_analysis)
        _write_json(output_path / "_artifacts" / "case_quality_report.json", quality_report)

        dataset_file = output_path / DATASET_FILENAME
        _write_json(
            dataset_file,
            {
                "dataset_id": output_path.name or "generated_dataset",
                "schema_version": "1.0",
                "source": DATASET_SOURCE,
                "task": normalized_task,
                "task_type": task_analysis.get("task_type", "agent_task"),
                "task_analysis_ref": str(analysis_path),
                "cases": cases,
            },
        )

        return DatasetArtifact(
            dataset_id=output_path.name or "generated_dataset",
            dataset_dir=str(output_path),
            dataset_files=[str(dataset_file)],
            cases=len(cases),
        )

    async def _generate_quality_checked_case(
        self,
        *,
        task: str,
        task_analysis: dict[str, Any],
        case_spec: dict[str, Any],
        seen_user_messages: set[str],
        case_index: int,
        case_count: int,
        output_path: Path,
    ) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
        """Generate one case and only accept it after quality review passes."""
        rejected_qualities: list[dict[str, Any]] = []
        quality_feedback = ""
        for attempt in range(1, MAX_CASE_GENERATION_ATTEMPTS + 1):
            raw_case = await self._generate_case(
                task=task,
                task_analysis=task_analysis,
                case_spec=case_spec,
                seen_user_messages=seen_user_messages,
                case_index=case_index,
                case_count=case_count,
                output_path=output_path,
                initial_previous_error=quality_feedback,
            )
            try:
                case = _normalize_case(
                    raw_case,
                    dataset_id=output_path.name or "generated_dataset",
                    task=task,
                    task_type=str(task_analysis.get("task_type", "agent_task")),
                    index=case_index,
                )
                _validate_required_case_evidence(
                    case,
                    required=task_analysis.get("required_case_evidence", []),
                    case_index=case_index,
                )
            except (RuntimeError, TypeError, ValueError) as exc:
                quality_feedback = f"Case schema validation failed: {exc}"
                rejected_qualities.append(
                    {
                        "case_id": str(case_spec.get("case_id_hint", "") or ""),
                        "accepted": False,
                        "stage": "schema_validation",
                        "main_issues": [str(exc)],
                    }
                )
                if attempt >= MAX_CASE_GENERATION_ATTEMPTS:
                    raise RuntimeError(
                        f"case[{case_index}] schema validation failed after {attempt} attempt(s): {exc}"
                    ) from exc
                continue
            quality = await self._review_case_quality(
                task=task,
                task_analysis=task_analysis,
                case_spec=case_spec,
                case=case,
                output_path=output_path,
            )
            if _quality_review_passes(quality, config=self.config, case=case):
                return case, quality, rejected_qualities
            rejected_qualities.append(quality)
            quality_feedback = _quality_review_feedback(quality)
            if attempt >= MAX_CASE_GENERATION_ATTEMPTS:
                raise RuntimeError(
                    f"case[{case_index}] quality review failed after {attempt} attempt(s): {quality_feedback}"
                )
        raise RuntimeError(f"case[{case_index}] quality review failed")

    async def _generate_task_analysis(
        self,
        *,
        task: str,
        output_path: Path,
        target_case_count: int,
        known_failures_text: str,
    ) -> dict[str, Any]:
        """Generate task analysis through short, validated planning stages."""
        capability_stage = await self._generate_capability_graph_stage(
            task=task,
            output_path=output_path,
            target_case_count=target_case_count,
            known_failures_text=known_failures_text,
        )
        capability_graph = capability_stage["capability_graph"]
        judge_skill_names = capability_stage["judge_skill_names"]
        combinations = await self._generate_capability_combinations_stage(
            task=task,
            output_path=output_path,
            target_case_count=target_case_count,
            capability_graph=capability_graph,
        )
        dimensions_stage = await self._generate_dimensions_stage(
            task=task,
            output_path=output_path,
            target_case_count=target_case_count,
            capability_graph=capability_graph,
            capability_combinations=combinations,
        )

        return _normalize_task_analysis(
            {
                "task_type": capability_stage.get("task_type", "agent_task"),
                "generator": "model",
                "scenario_summary": capability_stage.get("scenario_summary", ""),
                "judge_skill_names": judge_skill_names,
                "required_case_evidence": _required_case_evidence(resolve_judge_skills_by_name(judge_skill_names)),
                "capability_graph": capability_graph,
                "capability_combinations": combinations,
                "test_dimensions": dimensions_stage["test_dimensions"],
            },
            task=task,
            coverage_dimensions=list(self.config.coverage_dimensions),
            target_case_count=target_case_count,
        )

    async def _generate_capability_graph_stage(
        self,
        *,
        task: str,
        output_path: Path,
        target_case_count: int,
        known_failures_text: str,
    ) -> dict[str, Any]:
        previous_error = ""
        judge_skills = available_judge_skills()
        for attempt in range(1, MAX_TASK_ANALYSIS_ATTEMPTS + 1):
            raw_stage = await self._call_model(
                _build_capability_graph_prompt(
                    task=task,
                    coverage_dimensions=list(self.config.coverage_dimensions),
                    target_case_count=target_case_count,
                    known_failures_text=known_failures_text,
                    judge_skills=judge_skills,
                    previous_error=previous_error,
                ),
                output_path,
                operation="capability_graph",
                metadata={
                    "attempt": attempt,
                    "target_case_count": target_case_count,
                },
            )
            try:
                generated = _parse_model_json(raw_stage)
                stage = generated
                if not isinstance(stage, dict):
                    raise RuntimeError("capability graph response must be a JSON object")
                if "capability_graph" not in stage:
                    raise RuntimeError("capability graph response must contain canonical field: capability_graph")
                selected_names = _normalize_selected_judge_skills(
                    stage.get("judge_skill_names"),
                    available=judge_skills,
                )
                selected_names = list(
                    dict.fromkeys(selected_names + [skill.name for skill in resolve_judge_skills_for_task(task)])
                )
                return {
                    "task_type": str(stage.get("task_type", "agent_task") or "agent_task"),
                    "scenario_summary": str(stage.get("scenario_summary", "") or ""),
                    "capability_graph": _normalize_capability_graph(stage.get("capability_graph")),
                    "judge_skill_names": selected_names,
                }
            except RuntimeError as exc:
                previous_error = str(exc)
                if attempt >= MAX_TASK_ANALYSIS_ATTEMPTS:
                    raise
        raise RuntimeError("capability graph generation failed")

    async def _generate_capability_combinations_stage(
        self,
        *,
        task: str,
        output_path: Path,
        target_case_count: int,
        capability_graph: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        previous_error = ""
        target_combination_count = _target_capability_combination_count(target_case_count)
        capability_names = {
            str(capability["capability_name"])
            for capability in capability_graph
            if str(capability.get("capability_name", "")).strip()
        }
        for attempt in range(1, MAX_TASK_ANALYSIS_ATTEMPTS + 1):
            raw_stage = await self._call_model(
                _build_capability_combinations_prompt(
                    task=task,
                    capability_graph=capability_graph,
                    target_combination_count=target_combination_count,
                    previous_error=previous_error,
                ),
                output_path,
                operation="capability_combinations",
                metadata={
                    "attempt": attempt,
                    "target_case_count": target_case_count,
                    "target_combination_count": target_combination_count,
                },
            )
            try:
                generated = _parse_model_json(raw_stage)
                stage = generated
                if not isinstance(stage, dict):
                    raise RuntimeError("capability combinations response must be a JSON object")
                if "capability_combinations" not in stage:
                    raise RuntimeError(
                        "capability combinations response must contain canonical field: capability_combinations"
                    )
                combinations = _normalize_capability_combinations(
                    stage.get("capability_combinations"),
                    capability_names=capability_names,
                )
                return combinations[:target_combination_count]
            except RuntimeError as exc:
                previous_error = str(exc)
                if attempt >= MAX_TASK_ANALYSIS_ATTEMPTS:
                    raise
        raise RuntimeError("capability combinations generation failed")

    async def _generate_dimensions_stage(
        self,
        *,
        task: str,
        output_path: Path,
        target_case_count: int,
        capability_graph: list[dict[str, Any]],
        capability_combinations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        dimensions: list[dict[str, Any]] = []
        for dimension_index in range(1, target_case_count + 1):
            previous_error = ""
            for attempt in range(1, MAX_TASK_ANALYSIS_ATTEMPTS + 1):
                raw_stage = await self._call_model(
                    _build_single_dimension_prompt(
                        task=task,
                        coverage_dimensions=list(self.config.coverage_dimensions),
                        target_case_count=target_case_count,
                        dimension_index=dimension_index,
                        capability_graph=capability_graph,
                        capability_combinations=capability_combinations,
                        previous_dimensions=dimensions,
                        previous_error=previous_error,
                    ),
                    output_path,
                    operation="test_dimension",
                    metadata={
                        "attempt": attempt,
                        "dimension_index": dimension_index,
                        "target_case_count": target_case_count,
                    },
                )
                try:
                    generated = _parse_model_json(raw_stage)
                    stage = generated
                    if not isinstance(stage, dict):
                        raise RuntimeError("test dimension response must be a JSON object")
                    if "test_dimension" not in stage:
                        raise RuntimeError("test dimension response must contain canonical field: test_dimension")
                    dimension = stage.get("test_dimension")
                    if not isinstance(dimension, dict):
                        raise RuntimeError("test_dimension must be a JSON object")
                    capability_names = {
                        str(capability["capability_name"])
                        for capability in capability_graph
                        if str(capability.get("capability_name", "")).strip()
                    }
                    combination_names = {
                        str(combination["combination_name"])
                        for combination in capability_combinations
                        if str(combination.get("combination_name", "")).strip()
                    }
                    dimensions.append(
                        _normalize_test_dimension(
                            dimension,
                            index=dimension_index,
                            capability_names=capability_names,
                            combination_names=combination_names,
                        )
                    )
                    break
                except RuntimeError as exc:
                    previous_error = str(exc)
                    if attempt >= MAX_TASK_ANALYSIS_ATTEMPTS:
                        raise
        draft = _normalize_task_analysis(
            {
                "task_type": "stage_validation",
                "generator": "model",
                "scenario_summary": "stage validation",
                "capability_graph": capability_graph,
                "capability_combinations": capability_combinations,
                "test_dimensions": dimensions,
            },
            task=task,
            coverage_dimensions=list(self.config.coverage_dimensions),
            target_case_count=target_case_count,
        )
        return {
            "test_dimensions": draft["test_dimensions"],
        }

    async def _generate_case_specs_stage(
        self,
        *,
        task: str,
        output_path: Path,
        target_case_count: int,
        task_analysis: dict[str, Any],
        known_failures_text: str,
    ) -> list[dict[str, Any]]:
        specs: list[dict[str, Any]] = []
        for case_index in range(1, target_case_count + 1):
            specs.append(
                await self._generate_single_case_spec_stage(
                    task=task,
                    output_path=output_path,
                    target_case_count=target_case_count,
                    case_index=case_index,
                    task_analysis=task_analysis,
                    known_failures_text=known_failures_text,
                )
            )
        return _normalize_case_specs(
            specs,
            task_analysis=task_analysis,
            target_case_count=target_case_count,
        )

    async def _generate_single_case_spec_stage(
        self,
        *,
        task: str,
        output_path: Path,
        target_case_count: int,
        case_index: int,
        task_analysis: dict[str, Any],
        known_failures_text: str,
    ) -> dict[str, Any]:
        previous_error = ""
        for attempt in range(1, MAX_TASK_ANALYSIS_ATTEMPTS + 1):
            raw_stage = await self._call_model(
                _build_single_case_spec_prompt(
                    task=task,
                    target_case_count=target_case_count,
                    case_index=case_index,
                    task_analysis=task_analysis,
                    known_failures_text=known_failures_text,
                    previous_error=previous_error,
                ),
                output_path,
                operation="case_spec",
                metadata={
                    "attempt": attempt,
                    "case_index": case_index,
                    "target_case_count": target_case_count,
                },
            )
            try:
                generated = _parse_model_json(raw_stage)
                stage = generated
                if not isinstance(stage, dict):
                    raise RuntimeError("case spec response must be a JSON object")
                if "case_spec" not in stage:
                    raise RuntimeError("case spec response must contain canonical field: case_spec")
                case_spec = stage.get("case_spec")
                if not isinstance(case_spec, dict):
                    raise RuntimeError("case spec response must contain a JSON object")
                return _bind_case_spec_to_selected_dimension(
                    case_spec,
                    task_analysis=task_analysis,
                    case_index=case_index,
                )
            except RuntimeError as exc:
                previous_error = str(exc)
                if attempt >= MAX_TASK_ANALYSIS_ATTEMPTS:
                    raise
        raise RuntimeError(f"case spec[{case_index}] generation failed")

    async def _review_case_quality(
        self,
        *,
        task: str,
        task_analysis: dict[str, Any],
        case_spec: dict[str, Any],
        case: dict[str, Any],
        output_path: Path,
    ) -> dict[str, Any]:
        """Ask the dataset model whether a generated case is useful training signal."""
        if not self.config.quality_review_enabled:
            return _case_quality_acceptance(case, case_spec=case_spec)

        previous_error = ""
        for attempt in range(1, MAX_TASK_ANALYSIS_ATTEMPTS + 1):
            raw_review = await self._call_model(
                _build_case_quality_review_prompt(
                    task=task,
                    task_analysis=task_analysis,
                    case_spec=case_spec,
                    case=case,
                    previous_error=previous_error,
                ),
                output_path,
                operation="case_quality_review",
                metadata={
                    "attempt": attempt,
                    "case_id": str(case.get("case_id", "")),
                },
            )
            try:
                generated = _parse_model_json(raw_review)
                if "quality_review" not in generated:
                    raise RuntimeError("case quality review response must contain canonical field: quality_review")
                review = generated["quality_review"]
                return _normalize_case_quality_review(
                    review,
                    case=case,
                    case_spec=case_spec,
                )
            except RuntimeError as exc:
                previous_error = str(exc)
                if attempt >= MAX_TASK_ANALYSIS_ATTEMPTS:
                    raise
        raise RuntimeError("case quality review failed")

    async def _generate_case(
        self,
        *,
        task: str,
        task_analysis: dict[str, Any],
        case_spec: dict[str, Any],
        seen_user_messages: set[str],
        case_index: int,
        case_count: int,
        output_path: Path,
        initial_previous_error: str = "",
    ) -> dict[str, Any]:
        """Generate one case, retrying once when the model emits invalid JSON."""
        previous_error = initial_previous_error
        for attempt in range(1, MAX_CASE_GENERATION_ATTEMPTS + 1):
            raw_case = await self._call_model(
                _build_case_prompt(
                    task=task,
                    task_analysis=task_analysis,
                    case_spec=case_spec,
                    case_index=case_index,
                    case_count=case_count,
                    previous_error=previous_error,
                ),
                output_path,
                operation="case_generation",
                metadata={
                    "attempt": attempt,
                    "case_index": case_index,
                    "case_count": case_count,
                },
            )
            try:
                case_payload = _parse_model_json(raw_case)
                if "case" not in case_payload:
                    raise RuntimeError("case generation response must contain canonical field: case")
                case = _hydrate_case_from_spec(
                    case_payload["case"],
                    case_spec=case_spec,
                )
                _validate_raw_case_against_spec(
                    case,
                    case_spec=case_spec,
                    seen_user_messages=seen_user_messages,
                    case_index=case_index,
                )
                return case
            except RuntimeError as exc:
                raw_debug_path = _write_raw_model_output(
                    output_path=output_path,
                    filename=f"failed_case_{case_index:03d}_attempt_{attempt:03d}.raw.txt",
                    raw=raw_case,
                )
                previous_error = f"{exc}; raw_debug_path={raw_debug_path}"
                if attempt >= MAX_CASE_GENERATION_ATTEMPTS:
                    raise RuntimeError(previous_error) from exc
        raise RuntimeError(f"case[{case_index}] generation failed")

    async def analyze_task(self, task: str, output_path: str) -> str:
        """Ask the model for task analysis used to guide dataset generation."""
        normalized_task = _normalize_task(task)
        path = Path(output_path).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        task_analysis = await self._generate_task_analysis(
            task=normalized_task,
            output_path=path.parent,
            target_case_count=_target_case_count(
                self.config.min_cases,
                known_failures_payload=_load_known_failures_payload(self.config.known_failures_ref),
            ),
            known_failures_text=_load_known_failures_text(self.config.known_failures_ref),
        )
        _write_json(path, task_analysis)
        return str(path)

    async def _call_model(
        self,
        prompt: str,
        workspace: Path,
        *,
        operation: str = "model_call",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        async def call_once() -> str:
            if self._llm_runner is not None:
                text = await self._llm_runner(prompt, workspace)
                _raise_if_retryable_incomplete_json_output(text)
                return text

            model_config_ref = str(self.config.model_config_ref or "").strip()
            if not model_config_ref:
                raise RuntimeError("dataset_generator.model_config_ref is required when no dataset_dir is provided")

            model = load_member_optimizer_model(model_config_ref)
            response = await model.invoke(
                messages=[
                    {
                        "role": "system",
                        "content": _SYSTEM_PROMPT,
                    },
                    {
                        "role": "user",
                        "content": prompt,
                    },
                ],
                tools=None,
                temperature=0.2,
                max_tokens=4096,
            )
            record_llm_usage(
                getattr(response, "usage_metadata", None),
                metadata={
                    "component": "dataset_generator",
                    **(metadata or {}),
                },
            )
            text = _extract_model_text(response)
            _raise_if_retryable_incomplete_json_output(text)
            return text

        with llm_usage_scope(
            operation=operation,
            metadata={
                "component": "dataset_generator",
                **(metadata or {}),
            },
        ):
            return await run_model_call_with_retries(
                call_once,
                operation_name="dataset generation",
                max_retries=int(
                    getattr(
                        self.config,
                        "model_call_max_retries",
                        DEFAULT_MODEL_CALL_MAX_RETRIES,
                    )
                ),
            )


def _extract_model_text(response: Any) -> str:
    content = getattr(response, "content", response)
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("text", item.get("content", ""))))
            else:
                parts.append(str(item))
        return "".join(parts)
    return str(content or "")


def _normalize_task(task: str) -> str:
    normalized = repair_text_mojibake(str(task or "")).strip()
    if not normalized:
        raise ValueError("dataset generation task must not be empty")
    return normalized


def _target_case_count(
    min_cases: int,
    *,
    known_failures_payload: Any | None = None,
) -> int:
    budget_count = _known_failures_budget_total_cases(known_failures_payload)
    if budget_count is not None:
        return max(1, budget_count)
    return max(1, int(min_cases or DEFAULT_MIN_CASES))


def _target_capability_combination_count(target_case_count: int) -> int:
    return max(1, min(int(target_case_count or DEFAULT_MIN_CASES), 4))


def _load_known_failures_text(known_failures_ref: str) -> str:
    return _known_failures_text(_load_known_failures_payload(known_failures_ref))


def _load_known_failures_payload(known_failures_ref: str) -> Any | None:
    path_text = str(known_failures_ref or "").strip()
    if not path_text:
        return None
    path = Path(path_text).expanduser().resolve()
    if not path.is_file():
        raise RuntimeError(f"dataset_generator.known_failures_ref not found: {path}")
    with open(path, "r", encoding="utf-8") as file:
        payload = json.load(file)
    return repair_payload_mojibake(payload)


def _known_failures_text(payload: Any | None) -> str:
    if payload is None:
        return ""
    if isinstance(payload, dict):
        relevant = {
            "source": payload.get("source"),
            "source_eval_ref_path": payload.get("source_eval_ref_path"),
            "quality_gaps": payload.get("quality_gaps", []),
            "dataset_budget": payload.get("dataset_budget", {}),
            "recommended_synthetic_tasks": payload.get("recommended_synthetic_tasks", []),
        }
    else:
        relevant = {"recommended_synthetic_tasks": payload}
    return _bounded_json(relevant, limit=6000)


def _known_failures_budget_total_cases(payload: Any | None) -> int | None:
    if not isinstance(payload, dict):
        return None
    budget = payload.get("dataset_budget")
    if not isinstance(budget, dict):
        return None
    raw = budget.get("total_cases")
    if isinstance(raw, bool) or raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _bounded_json(payload: Any, *, limit: int) -> str:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _coverage_dimensions_text(
    coverage_dimensions: list[str],
    *,
    target_case_count: int,
) -> str:
    if coverage_dimensions:
        return "\n".join(f"- {dimension}" for dimension in coverage_dimensions)
    return f"- infer {target_case_count} task-specific capability dimensions from the target task and deliverable"


def _stage_repair_instruction(
    *,
    stage_name: str,
    previous_error: str = "",
) -> str:
    if not previous_error:
        return ""
    return f"""
Previous {stage_name} response failed validation:
{_truncate_prompt_error(previous_error)}

Repair requirement:
- Return one complete JSON object for the requested stage.
- Return raw JSON without Markdown fences.
- Escape all newlines inside JSON string values as \\n.
- Escape ASCII double quotes inside natural-language JSON string values as \\\".
- Make the JSON object the entire response.
- If the previous error says "Unterminated string" or "no closing JSON object",
  the previous response was too long. Return a shorter complete JSON object.
"""


def _build_capability_graph_prompt(
    *,
    task: str,
    coverage_dimensions: list[str],
    target_case_count: int,
    known_failures_text: str = "",
    judge_skills: list[JudgeSkill] | None = None,
    previous_error: str = "",
) -> str:
    dimensions_text = _coverage_dimensions_text(
        coverage_dimensions,
        target_case_count=target_case_count,
    )
    repair_instruction = _stage_repair_instruction(
        stage_name="capability graph",
        previous_error=previous_error,
    )
    judge_skill_catalog = _judge_skill_catalog_text(judge_skills or [])
    return f"""Generate the capability graph for a synthetic agent evaluation dataset.

Task:
{task}

Target case count:
{target_case_count}

Coverage guidance:
{dimensions_text}

Known agent weaknesses:
{known_failures_text or "None provided."}

Available domain Judge Skills:
{judge_skill_catalog}
{repair_instruction}

Return a raw JSON object as the entire response.

Top-level output contract:
- The top-level JSON object has the canonical fields `task_type`,
  `scenario_summary`, `judge_skill_names`, and `capability_graph`.
- `capability_graph` is the only field used for the capability list.

Required JSON shape:
{{
  "task_type": "short task family name",
  "scenario_summary": "what capability this dataset should improve",
  "judge_skill_names": ["only applicable names from the supplied catalog"],
  "capability_graph": [
    {{
      "capability_name": "concrete capability id",
      "description": "what the agent must be able to do",
      "observable_behavior": "how this capability is visible in artifacts or execution",
      "common_failure_modes": ["realistic ways agents fail this capability"],
      "prerequisite_capabilities": ["capability ids required first"],
      "difficulty_factors": ["what makes this capability harder"],
      "data_generation_strategy": "how to create cases that train this capability",
      "verifier_design": "how a judge or verifier should check it"
    }}
  ]
}}

Generation rules:
- Select every supplied Judge Skill whose described artifact domain is required
  by the task. Return an empty judge_skill_names list only when none applies.
- Judge Skill selection is a domain-policy choice, not a scoring decision.
- Decompose the target task into concrete execution capabilities.
- Capabilities should describe observable agent behavior, not generic labels.
- Include enough capabilities to support {target_case_count} distinct evaluation
  cases later.
- Use the coverage guidance as a seed, while deriving task-specific capabilities
  from the actual target task, expected deliverable, quality bar, and likely
  execution failure modes.
- Use complete, concrete values for every field.
"""


def _judge_skill_catalog_text(skills: list[JudgeSkill]) -> str:
    if not skills:
        return "No domain Judge Skills are available. Return an empty list."
    catalog = [
        {
            "name": skill.name,
            "description": skill.description,
            "required_case_evidence": list(skill.required_case_evidence),
        }
        for skill in skills
    ]
    return json.dumps(catalog, ensure_ascii=False, indent=2)


def _normalize_selected_judge_skills(
    raw: Any,
    *,
    available: list[JudgeSkill],
) -> list[str]:
    names = _normalize_optional_string_list(raw)
    available_names = {skill.name for skill in available}
    unknown = sorted(set(names) - available_names)
    if unknown:
        raise RuntimeError("judge_skill_names contains names outside the supplied catalog: " + ", ".join(unknown))
    return names[:3]


def _required_case_evidence(skills: list[JudgeSkill]) -> list[str]:
    evidence: list[str] = []
    seen: set[str] = set()
    for skill in skills:
        for field in skill.required_case_evidence:
            if field and field not in seen:
                seen.add(field)
                evidence.append(field)
    return evidence


def _build_capability_combinations_prompt(
    *,
    task: str,
    capability_graph: list[dict[str, Any]],
    target_combination_count: int,
    previous_error: str = "",
) -> str:
    repair_instruction = _stage_repair_instruction(
        stage_name="capability combinations",
        previous_error=previous_error,
    )
    graph_text = json.dumps(capability_graph, ensure_ascii=False, indent=2)
    return f"""Generate capability combinations for a synthetic agent evaluation dataset.

Task:
{task}

Target combination count:
{target_combination_count}

Capability graph:
{graph_text}
{repair_instruction}

Return a raw JSON object as the entire response.

Top-level output contract:
- The top-level JSON object has the canonical field `capability_combinations`.
- `capability_combinations` is the only field used for combination design.

Required JSON shape:
{{
  "capability_combinations": [
    {{
      "combination_name": "concrete combination id",
      "included_capabilities": ["capability ids from capability_graph"],
      "why_this_combination_is_hard": "why the combined capabilities fail in practice",
      "typical_agent_failure": "common execution failure caused by this combination",
      "target_task_pattern": "task pattern that exposes this combination",
      "minimum_required_context": "context needed to make the case realistic",
      "expected_tool_usage": ["tools or deterministic checks expected if relevant"],
      "evaluation_method": "how this combination should be evaluated",
      "difficulty_level": 1
    }}
  ]
}}

Generation rules:
- Generate exactly {target_combination_count} capability combinations.
- Create combinations that train capabilities together when that interaction is
  likely to cause real agent execution failure.
- Each combination must reference capability_name values from the capability graph.
- Each combination must explain why it is hard, the typical failure, the task
  pattern that exposes it, and how evaluation should check it.
- Cover team coordination, artifact delivery, methodology, deterministic
  validation, review/revision, and runtime/tooling when the task needs them.
- Keep each free-text field to one concise sentence, 25 words or fewer.
- Use 2 to 4 included_capabilities per combination.
- Keep the full JSON response under 1200 words.
"""


def _build_dimensions_prompt(
    *,
    task: str,
    coverage_dimensions: list[str],
    target_case_count: int,
    capability_graph: list[dict[str, Any]],
    capability_combinations: list[dict[str, Any]],
    previous_error: str = "",
) -> str:
    dimensions_text = _coverage_dimensions_text(
        coverage_dimensions,
        target_case_count=target_case_count,
    )
    repair_instruction = _stage_repair_instruction(
        stage_name="test dimensions",
        previous_error=previous_error,
    )
    graph_text = json.dumps(capability_graph, ensure_ascii=False, indent=2)
    combinations_text = json.dumps(
        capability_combinations,
        ensure_ascii=False,
        indent=2,
    )
    return f"""Generate test dimensions for a synthetic agent evaluation dataset.

Task:
{task}

Target case count:
{target_case_count}

Coverage guidance:
{dimensions_text}

Capability graph:
{graph_text}

Capability combinations:
{combinations_text}

Generic training intent taxonomy used later for case generation:
{json.dumps(list(GENERIC_TRAINING_INTENT_TAXONOMY), ensure_ascii=False, indent=2)}
{repair_instruction}

Return a raw JSON object as the entire response.

Top-level output contract:
- The top-level JSON object has the canonical field `test_dimensions`.
- `test_dimensions` is the only field used for the dimension list.
- Case-level judge rubrics are generated later from each selected dimension and
  case spec.

Required JSON shape:
{{
  "test_dimensions": [
    {{
      "name": "dimension id",
      "description": "observable capability being tested",
      "difficulty": "easy|medium|hard",
      "target_capabilities": ["capability ids from capability_graph"],
      "capability_combination": "combination_name from capability_combinations",
      "observable_behavior": "behavior this dimension will test",
      "common_failure_modes": ["failure modes this dimension should expose"],
      "difficulty_factors": ["difficulty factors used in generated cases"],
      "verifier_design": "specific verifier or judge design for this dimension"
    }}
  ]
}}

Generation rules:
- Generate exactly {target_case_count} distinct test dimensions.
- The run will generate exactly {target_case_count} cases.
- Each dimension will seed one generated case, so every dimension must represent
  a different observable capability gap or failure mode.
- Every dimension must reference target_capabilities from the capability graph
  and one capability_combination from the combinations list.
- If coverage guidance is provided, split broad guidance into judgeable,
  task-specific capability dimensions when needed.
- If coverage guidance is empty, infer dimensions from the task's artifact type,
  user interaction model, expected deliverable, quality bar, and likely execution
  failure modes.
- Keep the generic taxonomy as optimization-signal guidance; the dimensions
  should still come from the task and capability graph.
- For user-facing deliverables, include generalizable dimensions that measure
  user-perceived output quality from inspectable artifacts: structure,
  coherence, affordance, feedback, readability, audience fit, consistency, and
  completion quality.
- These output-quality dimensions must be judgeable from static, text-readable
  artifacts such as source files, outlines, schemas, generated content,
  configuration, tests, logs, or other inspectable deliverable evidence.
- Treat file shape, file count, checklist presence, and self-check notes as
  baseline gates. Use them as the main capability dimensions only when the task
  itself is explicitly about file-contract compliance.
- Keep each dimension compact: each natural-language string should be one
  concise sentence, 18 words or fewer.
- common_failure_modes and difficulty_factors should each contain 1 to 2 short
  items.
- verifier_design must be one concise sentence describing the judge/verifier
  approach.
- Do not write step-by-step verifier procedures in verifier_design.
- Return raw compact JSON without Markdown fences.
- Keep the full JSON response under 1200 words.
- Use complete, concrete values for every field.
"""


def _build_single_dimension_prompt(
    *,
    task: str,
    coverage_dimensions: list[str],
    target_case_count: int,
    dimension_index: int,
    capability_graph: list[dict[str, Any]],
    capability_combinations: list[dict[str, Any]],
    previous_dimensions: list[dict[str, Any]],
    previous_error: str = "",
) -> str:
    dimensions_text = _coverage_dimensions_text(
        coverage_dimensions,
        target_case_count=target_case_count,
    )
    repair_instruction = _stage_repair_instruction(
        stage_name=f"test dimension {dimension_index}",
        previous_error=previous_error,
    )
    graph_text = json.dumps(capability_graph, ensure_ascii=False, separators=(",", ":"))
    combinations_text = json.dumps(
        capability_combinations,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    previous_dimension_names = [
        str(item.get("name", "")).strip()
        for item in previous_dimensions
        if isinstance(item, dict) and str(item.get("name", "")).strip()
    ]
    return f"""Generate one test dimension for a synthetic agent evaluation dataset.

Task:
{task}

Target case count:
{target_case_count}

Case index:
{dimension_index} of {target_case_count}

Coverage guidance:
{dimensions_text}

Existing dimensions already generated:
{json.dumps(previous_dimension_names, ensure_ascii=False)}

Capability graph:
{graph_text}

Capability combinations:
{combinations_text}

Generic training intent taxonomy used later for case generation:
{json.dumps(list(GENERIC_TRAINING_INTENT_TAXONOMY), ensure_ascii=False, separators=(",", ":"))}
{repair_instruction}

Return a raw JSON object as the entire response.

Top-level output contract:
- The top-level JSON object has the canonical field `test_dimension`.
- `test_dimension` is the only field used for this dimension.
- Case-level judge rubrics are generated later from this dimension and case spec.

Required JSON shape:
{{
  "test_dimension": {{
    "name": "dimension id",
    "description": "observable capability being tested",
    "difficulty": "easy|medium|hard",
    "target_capabilities": ["capability ids from capability_graph"],
    "capability_combination": "combination_name from capability_combinations",
    "observable_behavior": "behavior this dimension will test",
    "common_failure_modes": ["failure mode this dimension should expose"],
    "difficulty_factors": ["difficulty factor used in generated cases"],
    "verifier_design": "specific verifier or judge design for this dimension"
  }}
}}

Generation rules:
- Generate exactly one dimension for case index {dimension_index}.
- Do not repeat existing dimension names.
- This dimension will seed one generated case, so it must represent one
  observable capability gap or failure mode.
- Reference target_capabilities from the capability graph and one
  capability_combination from the combinations list.
- If coverage guidance is provided, use it as guidance, not as a fixed label.
- Keep generic taxonomy as optimization-signal guidance; derive the dimension
  from the task, capability graph, and combination.
- For user-facing deliverables, dimensions may measure user-perceived output
  quality from inspectable artifacts: structure, coherence, affordance,
  feedback, readability, audience fit, consistency, and completion quality.
- Treat file shape, file count, checklist presence, and self-check notes as
  baseline gates unless the task is explicitly about file-contract compliance.
- Each natural-language string must be one concise sentence, 18 words or fewer.
- common_failure_modes and difficulty_factors must each contain 1 to 2 short items.
- verifier_design must be one concise sentence.
- Return raw compact JSON without Markdown fences.
- Keep the full JSON response under 220 words.
- Use complete, concrete values for every field.
"""


def _build_single_case_spec_prompt(
    *,
    task: str,
    target_case_count: int,
    case_index: int,
    task_analysis: dict[str, Any],
    known_failures_text: str = "",
    previous_error: str = "",
) -> str:
    dimensions = [item for item in task_analysis.get("test_dimensions", []) if isinstance(item, dict)]
    selected_dimension = dimensions[case_index - 1] if 0 <= case_index - 1 < len(dimensions) else {}
    repair_instruction = _stage_repair_instruction(
        stage_name=f"case spec {case_index}",
        previous_error=previous_error,
    )
    analysis_text = json.dumps(
        {
            "task_type": task_analysis.get("task_type"),
            "scenario_summary": task_analysis.get("scenario_summary"),
            "capability_combinations": task_analysis.get("capability_combinations", []),
            "selected_dimension": selected_dimension,
            "training_intent_taxonomy": task_analysis.get(
                "training_intent_taxonomy",
                list(GENERIC_TRAINING_INTENT_TAXONOMY),
            ),
        },
        ensure_ascii=False,
        indent=2,
    )
    return f"""Generate one concrete case spec for a synthetic agent evaluation dataset.

Task:
{task}

Target case count:
{target_case_count}

Case index:
{case_index} of {target_case_count}

Task analysis:
{analysis_text}

Known agent weaknesses:
{known_failures_text or "None provided."}
{repair_instruction}

Return a raw JSON object as the entire response.

Top-level output contract:
- The top-level JSON object has the canonical field `case_spec`.
- `case_spec` is the only field used for this case specification.

Required JSON shape:
{{
  "case_spec": {{
    "case_id_hint": "descriptive_unique_case_id",
    "source_gap": "quality gap id from Known agent weaknesses, or empty string",
    "observed_gap": {{"dimension": "gap dimension", "evidence": {{}}}},
    "dimension": "dimension id from selected_dimension",
    "difficulty": "easy|medium|hard",
    "training_intent": "intent id from training_intent_taxonomy",
    "expected_optimization_target": "team_skill|member_harness",
    "target_surfaces": ["skill|tool|prompt_section"],
    "target_capabilities": ["capability ids from selected_dimension"],
    "capability_combination": "combination name from selected_dimension",
    "user_visible_challenge": "the concrete challenge that must appear in user_message",
    "critical_user_constraints": ["constraints the executor must see before acting"],
    "expected_failure_modes": ["failures this case is designed to expose"],
    "verifier_contract": ["checks that decide whether the case passed"],
    "challenge_focus": "difficulty mechanism for this exact case"
  }}
}}

Generation rules:
- Generate exactly one case_spec for case index {case_index}.
- Each case spec becomes one concrete user-facing evaluation case.
- The dimension must equal selected_dimension.name exactly.
- case_id_hint must be descriptive and stable; do not use placeholder names
  such as stable_unique_id, sample_case, or case_id.
- Bind the case spec to one source_gap from Known agent weaknesses when
  quality_gaps or recommended_synthetic_tasks are provided.
- observed_gap should carry the seed gap dimension, evidence summary, affected
  roles, and target surfaces that this case is designed to exercise.
- When observed_gap includes quality_axes, choose the case challenge from those
  axes: functional effectiveness, user-visible effects or interaction quality,
  user-visible output quality, and acceptance-contract evidence.
- For seed artifact quality gaps, generate cases that expose functional,
  effect/interaction, and appearance/readability weaknesses in the requested
  artifact family.
- Spread case specs across available source gaps according to dataset_budget
  when the known weakness payload provides case_groups.
- user_visible_challenge and critical_user_constraints must be concrete enough
  to be copied into the later user_message.
- The later user_message will be required to include these critical constraints,
  so do not hide important judging requirements only in the rubric.
- Cover a curriculum from calibration cases to hard integration cases.
- Keep target_surfaces as optimization hints only; the evaluator/analyzer will
  still decide from execution evidence.
- Keep lists compact: 1 to 3 items for constraints, failure modes, and verifier
  contract.
- Use complete, concrete values for every field.
"""


def _build_case_prompt(
    *,
    task: str,
    task_analysis: dict[str, Any],
    case_spec: dict[str, Any],
    case_index: int,
    case_count: int,
    previous_error: str = "",
) -> str:
    target_dimension = case_spec["dimension"]
    target_difficulty = str(case_spec["difficulty"])
    challenge_focus = str(case_spec["challenge_focus"])
    training_intent = case_spec["training_intent"]
    dimension = json.dumps(target_dimension, ensure_ascii=False, indent=2)
    case_spec_text = json.dumps(
        {key: value for key, value in case_spec.items() if key not in {"dimension", "training_intent"}},
        ensure_ascii=False,
        indent=2,
    )
    training_intent_text = json.dumps(training_intent, ensure_ascii=False, indent=2)
    target_capabilities = json.dumps(
        case_spec.get("target_capabilities", target_dimension.get("target_capabilities", [])),
        ensure_ascii=False,
    )
    capability_combination = str(
        case_spec.get("capability_combination", target_dimension.get("capability_combination", "")) or ""
    )
    verifier_design = str(target_dimension.get("verifier_design", "") or "")
    dimension_failure_modes = json.dumps(
        target_dimension.get("common_failure_modes", []),
        ensure_ascii=False,
    )
    required_case_evidence = json.dumps(
        task_analysis.get("required_case_evidence", []),
        ensure_ascii=False,
    )
    repair_instruction = ""
    if previous_error:
        repair_instruction = f"""
Previous response failed validation:
{_truncate_prompt_error(previous_error)}

Repair requirement:
- Return one complete JSON object.
- Return raw JSON without Markdown fences.
- Escape all newlines inside JSON string values as \\n.
- Escape ASCII double quotes inside natural-language JSON string values as \\\".
- Make the JSON object the entire response.
- If the previous response was incomplete, shorten every list and every
  natural-language field before retrying.
- If the previous error says "Unterminated string" or "no closing JSON object",
  the previous response was too long. Return a shorter complete JSON object.
- Preserve every mandatory machine-evidence field listed below on repair.
  If quality review rejected an evidence contract, replace its invalid steps
  with reachable executable steps; never fix it by deleting the field.
"""
    return f"""Generate exactly 1 synthetic evaluation example for this agent task.

Task:
{task}

Case index:
{case_index} of {case_count}

Use the assigned case spec for this case. Keep the user task, reference,
training signal, and scoring signals aligned with that spec.

Case spec for this case:
{case_spec_text}

Observed seed gap for this case:
{json.dumps(case_spec.get("observed_gap", {}), ensure_ascii=False, indent=2)}

Critical user-visible constraints:
{json.dumps(case_spec.get("critical_user_constraints", []), ensure_ascii=False, indent=2)}

Assigned primary dimension:
{dimension}

Target capabilities for this case:
{target_capabilities}

Capability combination for this case:
{capability_combination}

Common failure modes to expose:
{dimension_failure_modes}

Verifier design hint for this case:
{verifier_design}

Machine evidence required by active domain Judge Skills:
{required_case_evidence}

Assigned difficulty:
{target_difficulty}

Assigned generic training intent:
{training_intent_text}

Challenge requirements:
{challenge_focus}
{repair_instruction}

Return a raw JSON object as the entire response.
Keep the response compact. The full JSON response must stay under 450 words.

Required JSON shape:
{{
  "case": {{
    "case_id": "{case_spec.get("case_id_hint", f"generated_case_{case_index:03d}")}",
    "input": {{
      "user_message": "the concrete user request to run"
    }},
    "reference": {{
      "required_behaviors": [
        {{
          "id": "behavior id",
          "description": "observable behavior required for success",
          "weight": 1.0,
          "rubric": "how to judge this behavior"
        }}
      ],
      "forbidden_behaviors": [
        {{
          "id": "behavior id",
          "description": "observable behavior that should reduce score",
          "penalty": 0.3
        }}
      ],
      "judge_rubric": {{
        "pass_threshold": 0.8,
        "criteria": [
          "specific scoring rule"
        ]
      }},
      "expected_steps": [
        "observable execution step the agent should perform"
      ],
      "distractors_or_traps": [
        "plausible trap that exposes the target capability gap"
      ],
      "success_criteria": [
        "explicit condition that makes the case pass"
      ],
      "failure_criteria": [
        "explicit condition that makes the case fail"
      ],
      "verifier": {{
        "type": "llm_judge_or_script_or_artifact_check",
        "check_method": "how the case should be checked",
        "test_cases_or_rules": [
          "concrete verifier rule"
        ]
      }},
      "web_verification": {{
        "steps": [
          {{"action": "click", "selector": "stable CSS selector"}},
          {{"action": "wait", "milliseconds": 300}},
          {{"assert": "exists", "selector": "stable CSS selector"}},
          {{"assert": "has_class", "selector": "stable CSS selector", "value": "class-name"}},
          {{"assert": "computed_style_not_default", "selector": "stable CSS selector", "value": "color"}}
        ]
      }},
      "gold_answer_or_expected_artifact": "the expected answer, artifact contract, or quality bar"
    }}
  }}
}}

Generation rules:
- Every field listed under machine evidence required by active domain Judge
  Skills is mandatory and must contain a real executable verification contract.
  Omitting it or replacing it with prose will fail deterministic validation.
- The case must be about improving execution of the task above, not an unrelated domain.
- Output exactly one complete JSON object and no surrounding explanation.
- The top-level JSON object must contain the canonical field `case`.
- `case` is the only field used for the generated evaluation case.
- Do not output training_signal, metadata, or input.critical_user_constraints.
  The generator fills those deterministic fields from the assigned case spec.
- The user_message must explicitly include the case-specific challenge and
  critical_user_constraints from the assigned case spec.
- Every fixed CSS selector used by web_verification must be disclosed as part
  of the user-visible artifact contract. Never grade against a hidden DOM id,
  class, or automation hook that the executing Team could not know to create.
- Every exact class name or text value asserted by web_verification must also
  be disclosed; do not hide BEM/class naming or literal text contracts.
- web_verification supports only click/wait actions and these assertions:
  exists, visible, hidden, has_class, not_has_class, text_contains, enabled,
  disabled, count_equals, count_at_least, count_at_most, and
  computed_style_not_default. For computed_style_not_default, value is a CSS
  property name such as color or border-top-width.
- `text_contains`, `has_class`, `not_has_class`, and
  `computed_style_not_default` require a non-empty string value. Count
  assertions require a non-negative integer value, and `count_at_least`
  specifically requires a positive integer because a lower bound of zero is
  vacuous. Empty or vacuous expected values fail deterministic validation
  before quality review.
- web_verification is a bounded immediate-interaction check, not a workflow
  engine. Every assertion must be reachable by executing the listed steps once
  from the initial page state. Do not assert a win, loss, completion, or other
  long-flow terminal state after a few arbitrary clicks. Put full-workflow
  requirements in verifier.test_cases_or_rules unless the user-visible task
  explicitly guarantees that the listed bounded actions reach that state.
- Prose in verifier.test_cases_or_rules does not execute setup or inject state
  for web_verification. Only the listed web_verification steps execute. A
  wait-only sequence may assert a dynamic AI/result state only when the task
  explicitly requires that state to be produced on initial page load.
- Keep user_message under 180 words while preserving the case-specific
  challenge and critical constraints.
- Do not generate a user_message that is interchangeable with another case.
- case_id must equal the assigned case_id_hint from the case spec.
- Every target capability listed above must be observable through the user
  request, required behaviors, success/failure criteria, verifier rules,
  expected steps, or training_signal.
- Use the capability combination as the core execution challenge: when it
  contains multiple capabilities, the case should make their interaction matter
  instead of testing only one isolated capability.
- Required behaviors must evaluate the target capability combination, not only
  the presence of output files or self-reported completion.
- At least one required behavior must score the interaction between two or more
  target capabilities when the capability combination contains multiple
  capabilities.
- Capability behaviors must carry most of the total behavior weight.
- Baseline artifact/file-contract behaviors must not exceed 20% of the total behavior weight
  unless the assigned generic training intent is specifically output_contract_and_completion.
- Do not let a case receive a high score just because files exist; high scores
  require evidence that the case-specific capability behaviors are satisfied.
- Keep natural-language values concise: each description, rubric, criterion,
  step, trap, or rationale should be one sentence, 16 words or fewer.
- required_behaviors: 2 to 3 items.
- forbidden_behaviors: 1 to 2 items.
- judge_rubric.criteria: 2 to 3 items.
- expected_steps: 2 to 3 items.
- distractors_or_traps: 1 to 2 items.
- success_criteria: 2 to 3 items.
- failure_criteria: 1 to 2 items.
- verifier.test_cases_or_rules: 1 to 2 items.
- gold_answer_or_expected_artifact must be a concise artifact contract, not a
  full reference solution.
- metadata.dimension must equal the assigned primary dimension name exactly.
- metadata.difficulty must equal the assigned difficulty exactly.
- metadata.training_intent and training_signal.diagnostic_intent must equal
  the assigned generic training intent exactly.
- expected_optimization_target must equal the assigned value exactly.
- The case difficulty must match the challenge requirements.
- The case must be directly judgeable from the agent output and generated artifacts.
- Required behaviors must be concrete enough for an LLM-as-judge evaluator.
- expected_steps must describe the intended execution path, not hidden chain of thought.
- distractors_or_traps must expose likely execution failures for the assigned capability.
- success_criteria and failure_criteria must be concrete enough to support automated judging.
- verifier must describe how to check the case using available outputs, artifacts, or rules.
- gold_answer_or_expected_artifact must state the expected deliverable contract or quality bar.
- training_signal must be specific to this case and must not be generic boilerplate.
- Instantiate the generic training intent for this task in concrete task terms.
- target_surfaces may contain only skill, tool, prompt_section.
- For artifact-producing tasks, require inspectable artifacts or artifact-equivalent
  sections suitable for automated judging.
- For user-facing tasks, required behaviors must emphasize user-perceived output
  quality that can be judged from static, text-readable evidence: document or
  artifact structure, information hierarchy, state feedback, consistency,
  readability, audience fit, and content-to-output mapping.
- The judge must be able to score the case by reading generated source artifacts such as
  source files, markdown briefs, structured content plans, schemas, logs, tests,
  or static verifier outputs.
- Escape ASCII double quotes inside natural-language JSON string values as \\\".
  Do not place raw ASCII double quotes inside JSON strings unless they are escaped.
- File count, checklist presence, and self-check notes are baseline gates. They may
  appear as one small required behavior or forbidden behavior, but they must not
  dominate the case unless the assigned generic training intent is specifically
  output_contract_and_completion.
- Use domain details as concrete case material while keeping the capability gap
  reusable for future tasks of the same artifact family.
- Keep the JSON compact enough to fit in one response.
- Use complete, concrete values for every field.
"""


def _build_case_quality_review_prompt(
    *,
    task: str,
    task_analysis: dict[str, Any],
    case_spec: dict[str, Any],
    case: dict[str, Any],
    previous_error: str = "",
) -> str:
    case_text = json.dumps(case, ensure_ascii=False, indent=2)
    case_spec_text = json.dumps(case_spec, ensure_ascii=False, indent=2)
    analysis_text = json.dumps(
        {
            "task_type": task_analysis.get("task_type"),
            "scenario_summary": task_analysis.get("scenario_summary"),
            "judge_skill_names": task_analysis.get("judge_skill_names", []),
            "required_case_evidence": task_analysis.get("required_case_evidence", []),
            "capability_graph": task_analysis.get("capability_graph", []),
            "capability_combinations": task_analysis.get("capability_combinations", []),
        },
        ensure_ascii=False,
        indent=2,
    )
    repair_instruction = _stage_repair_instruction(
        stage_name="case quality review",
        previous_error=previous_error,
    )
    return f"""Review synthetic case quality for agent training.

Task:
{task}

Task analysis:
{analysis_text}

Case spec:
{case_spec_text}

Generated case:
{case_text}
{repair_instruction}

Evaluate whether this case is useful for improving an LLM-based agent.
The review must focus on training value, capability alignment, realism, and
verifiability.

Return a raw JSON object as the entire response.

Top-level output contract:
- The top-level JSON object has the canonical field `quality_review`.
- `quality_review` is the only field used for quality acceptance.

Required JSON shape:
{{
  "quality_review": {{
    "accepted": true,
    "critical_constraints_covered": true,
    "quality_score": 9,
    "difficulty_score": 4,
    "capability_alignment_score": 9,
    "verifiability_score": 9,
    "realism_score": 8,
    "main_issues": [
      "specific issue if rejected"
    ],
    "revision_suggestions": [
      "specific rewrite instruction if rejected"
    ],
    "final_decision_reason": "why the case should be accepted or rewritten"
  }}
}}

Review criteria:
- critical_constraints_covered must be true only when the user_message
  semantically includes every critical_user_constraints item from the case spec.
  The wording may be translated or paraphrased, but the executor must see the
  same constraints before acting.
- quality_score measures whether the task is realistic, specific, and likely
  to produce useful optimization signal.
- difficulty_score uses 1 to 5 and must match the assigned case difficulty.
- capability_alignment_score measures whether the user request, reference,
  verifier, and training_signal all target the case spec capabilities.
- capability_alignment_score must account for every target capability in the
  case spec. A capability is aligned only when it is visible in the user request,
  reference behaviors, success/failure criteria, verifier rules, expected steps,
  or training_signal.
- For multi-capability combinations, the case should exercise the interaction
  among the included capabilities, not only one isolated capability.
- required_behaviors must include capability-combination behaviors that make
  the involved capabilities observable in the final artifacts or verifier
  evidence.
- Reject cases whose score can be high from artifact or file existence alone.
- baseline gates must not dominate the pass score unless the case spec
  explicitly targets output_contract_and_completion.
- verifiability_score measures whether success and failure can be judged from
  produced outputs, artifacts, or verifier rules.
- Execute web_verification mentally from a fresh initial page. Reject the case
  if a step lacks a required precondition or an asserted state cannot be reached
  by the preceding listed actions exactly once.
- Reject empty expected values for `text_contains`, `has_class`,
  `not_has_class`, or `computed_style_not_default`; these are vacuous checks.
  Count assertions must use non-negative integer values, and reject
  `count_at_least: 0` because it passes for every valid selector result.
- Treat verifier.test_cases_or_rules as judge guidance, not executable setup for
  web_verification. Never assume that prose about injecting state, driving a
  match, or running an AI turn happened before the bounded steps.
- Merely appending the machine-step disclosure to user_message does not make an
  impossible state reachable. Two generic card clicks do not prove a 30-HP
  match reached a terminal result, and waiting alone does not prove an AI turn
  ran unless initial-page auto execution is an explicit product requirement.
- Interpret assertion names literally: `exists` means the selector resolves to
  a DOM element regardless of whether it is displayed. Only `visible` and
  `hidden` assert display state. A hidden terminal overlay may therefore satisfy
  `exists` on the initial page without reaching the terminal workflow.
- A short fixed click sequence must not claim to validate a full match, complete
  workflow, or terminal success/failure state. Such outcomes belong in richer
  verifier rules unless the user-visible contract explicitly makes the bounded
  sequence sufficient.
- Judge evidence coverage across required_behaviors, verifier.test_cases_or_rules,
  artifact checks, and web_verification together. Do not reject a case merely
  because bounded web_verification checks initial DOM structure when richer
  verifier rules cover the long workflow and baseline checks do not dominate
  required-behavior weights.
- Never recommend adding an unsupported long gameplay sequence to bounded
  web_verification. Keep immediate DOM checks there and require long-flow
  behavior in verifier.test_cases_or_rules instead.
- Confirm that every exact selector, asserted class name, and asserted text
  literal in web_verification is disclosed to the executing Team. Reject hidden
  automation contracts even when the rest of the case is realistic.
- realism_score measures whether the task resembles a real work request for
  the target artifact family.
- Reject cases that are too easy, vague, duplicated, unverifiable, impossible,
  dependent on hidden assumptions, or not aligned with the target capability.
- Reject cases when critical_constraints_covered is false.
- If rejected, revision_suggestions must tell the next generator attempt what
  to change in the user_message, reference, verifier, or training_signal.
- If accepted is false, main_issues and revision_suggestions must be non-empty
  and final_decision_reason must explain the concrete rewrite needed.
- If the case needs no revision, accepted must be true and scores should reflect
  that conclusion.
- Return complete JSON only.
"""


def _truncate_prompt_error(error: str, limit: int = 800) -> str:
    text = str(error or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


_SYSTEM_PROMPT = """You generate synthetic evaluation datasets for agent optimization.
The output is consumed by an evaluator, so each example must contain an input,
reference behaviors, forbidden behaviors, and a judge rubric. Return valid JSON only.
For artifact-producing tasks, use inspectable source artifacts as evaluation evidence.
Escape ASCII double quotes inside natural-language JSON strings as \\\".
"""


def _parse_model_json(raw: str) -> dict[str, Any]:
    parsed = _extract_json_object(raw)
    if parsed is None:
        raise RuntimeError(_model_json_parse_error(raw))
    if not isinstance(parsed, dict):
        raise RuntimeError("dataset generation output must be a JSON object")
    return repair_payload_mojibake(parsed)


def _raise_if_retryable_incomplete_json_output(raw: str) -> None:
    """Treat transport-truncated JSON as a model-call retry, not schema feedback."""
    text = str(raw or "").strip()
    if not text or not (text.startswith("{") or text.startswith("[") or text.startswith("```")):
        return
    candidate = _strip_markdown_fence(text) if text.startswith("```") else text
    try:
        json.loads(candidate)
        return
    except json.JSONDecodeError as exc:
        completed = _complete_truncated_json_object(candidate)
        if completed is not None:
            try:
                json.loads(completed)
                return
            except json.JSONDecodeError:
                pass
        if _is_retryable_json_truncation(exc, candidate):
            raise RetryableModelOutputError("dataset generation model output is incomplete JSON") from exc


def _is_retryable_json_truncation(exc: json.JSONDecodeError, text: str) -> bool:
    message = str(exc.msg or "").lower()
    near_eof = len(text) - exc.pos <= 16
    if "unterminated string" in message:
        return True
    if near_eof and message in {
        "expecting value",
        "expecting property name enclosed in double quotes",
        "expecting ',' delimiter",
        "expecting ':' delimiter",
    }:
        return True
    return False


def _extract_json_object(raw: str) -> Any | None:
    text = str(raw or "").strip()
    if not text:
        return None
    if text.startswith("```"):
        text = _strip_markdown_fence(text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    surplus_repaired = _decode_json_with_surplus_closers(text)
    if surplus_repaired is not None:
        return surplus_repaired
    completed = _complete_truncated_json_object(text)
    if completed is not None:
        try:
            return json.loads(completed)
        except json.JSONDecodeError:
            pass

    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    candidate_end = end + 1
    candidate = text[start:candidate_end]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        completed = _complete_truncated_json_object(candidate)
        if completed is None:
            return None
        try:
            return json.loads(completed)
        except json.JSONDecodeError:
            return None


def _decode_json_with_surplus_closers(text: str) -> Any | None:
    """Accept one JSON value followed only by redundant closing delimiters."""
    try:
        value, end = json.JSONDecoder().raw_decode(text)
    except json.JSONDecodeError:
        return None
    trailing = text[end:].strip()
    if trailing and all(char in "}]" for char in trailing):
        return value
    return None


def _complete_truncated_json_object(text: str) -> str | None:
    """Close a JSON object cut off at EOF without repairing other syntax errors."""
    stripped = str(text or "").strip()
    if not stripped or stripped[0] not in "{[":
        return None

    stack: list[str] = []
    in_string = False
    escaped = False
    for char in stripped:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char in "{[":
            stack.append(char)
        elif char in "}]":
            if not stack:
                return None
            opening = stack.pop()
            if (opening, char) not in {("{", "}"), ("[", "]")}:
                return None

    if in_string or escaped or not stack:
        return None
    closers = "".join("}" if opening == "{" else "]" for opening in reversed(stack))
    return stripped + closers


def _model_json_parse_error(raw: str) -> str:
    text = str(raw or "").strip()
    details = _json_decode_error_details(text)
    return (
        "dataset generation output did not contain valid JSON: "
        f"length={len(text)}; {details}; "
        f"head={_json_error_excerpt(text[:500])}; "
        f"tail={_json_error_excerpt(text[-500:])}"
    )


def _json_decode_error_details(text: str) -> str:
    if not text:
        return "empty output"
    candidate = _strip_markdown_fence(text) if text.startswith("```") else text
    try:
        json.loads(candidate)
    except json.JSONDecodeError as exc:
        return f"line={exc.lineno}; column={exc.colno}; position={exc.pos}; message={exc.msg}"

    start = candidate.find("{")
    end = candidate.rfind("}")
    if start < 0:
        return "no opening JSON object brace"
    if end <= start:
        return "no closing JSON object brace"
    try:
        candidate_end = end + 1
        json.loads(candidate[start:candidate_end])
    except json.JSONDecodeError as exc:
        return f"line={exc.lineno}; column={exc.colno}; position={exc.pos}; message={exc.msg}"
    return "parsed JSON was not accepted"


def _json_error_excerpt(text: str) -> str:
    return repr(str(text or "").replace("\r", "\\r").replace("\n", "\\n"))


def _write_raw_model_output(*, output_path: Path, filename: str, raw: str) -> str:
    debug_dir = output_path / "_artifacts"
    debug_dir.mkdir(parents=True, exist_ok=True)
    debug_path = debug_dir / filename
    debug_path.write_text(str(raw or ""), encoding="utf-8")
    return str(debug_path)


def _strip_markdown_fence(text: str) -> str:
    lines = text.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _normalize_capability_graph(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not raw:
        raise RuntimeError("task_analysis.capability_graph must be a non-empty list")

    capabilities: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, capability in enumerate(raw, start=1):
        if not isinstance(capability, dict):
            raise RuntimeError(f"task_analysis.capability_graph[{index}] must be a mapping")
        name = str(capability.get("capability_name", "") or "").strip()
        description = str(capability.get("description", "") or "").strip()
        observable_behavior = str(capability.get("observable_behavior", "") or "").strip()
        data_generation_strategy = str(capability.get("data_generation_strategy", "") or "").strip()
        verifier_design = str(capability.get("verifier_design", "") or "").strip()
        if not name or not description:
            raise RuntimeError(f"task_analysis.capability_graph[{index}] requires capability_name and description")
        if name in seen:
            raise RuntimeError(f"duplicate capability_name in capability_graph: {name}")
        seen.add(name)
        if not observable_behavior or not data_generation_strategy or not verifier_design:
            raise RuntimeError(
                f"task_analysis.capability_graph[{index}] requires observable_behavior, "
                "data_generation_strategy, and verifier_design"
            )
        capabilities.append(
            {
                "capability_name": name,
                "description": description,
                "observable_behavior": observable_behavior,
                "common_failure_modes": _normalize_string_list(
                    capability.get("common_failure_modes"),
                    field=f"task_analysis.capability_graph[{index}].common_failure_modes",
                ),
                "prerequisite_capabilities": _normalize_optional_string_list(
                    capability.get("prerequisite_capabilities"),
                ),
                "difficulty_factors": _normalize_string_list(
                    capability.get("difficulty_factors"),
                    field=f"task_analysis.capability_graph[{index}].difficulty_factors",
                ),
                "data_generation_strategy": data_generation_strategy,
                "verifier_design": verifier_design,
            }
        )
    return capabilities


def _normalize_capability_combinations(
    raw: Any,
    *,
    capability_names: set[str],
) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not raw:
        raise RuntimeError("task_analysis.capability_combinations must be a non-empty list")

    combinations: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, combination in enumerate(raw, start=1):
        if not isinstance(combination, dict):
            raise RuntimeError(f"task_analysis.capability_combinations[{index}] must be a mapping")
        name = str(combination.get("combination_name", "") or "").strip()
        if not name:
            raise RuntimeError(f"task_analysis.capability_combinations[{index}] requires combination_name")
        if name in seen:
            raise RuntimeError(f"duplicate combination_name in capability_combinations: {name}")
        seen.add(name)
        included = _normalize_string_list(
            combination.get("included_capabilities"),
            field=f"task_analysis.capability_combinations[{index}].included_capabilities",
        )
        unknown = [capability for capability in included if capability not in capability_names]
        if unknown:
            raise RuntimeError(
                f"task_analysis.capability_combinations[{index}].included_capabilities "
                f"contains unknown capabilities: {unknown}"
            )
        required_fields = {
            "why_this_combination_is_hard": "why_this_combination_is_hard",
            "typical_agent_failure": "typical_agent_failure",
            "target_task_pattern": "target_task_pattern",
            "minimum_required_context": "minimum_required_context",
            "evaluation_method": "evaluation_method",
        }
        normalized_required: dict[str, str] = {}
        for source_key, output_key in required_fields.items():
            value = str(combination.get(source_key, "") or "").strip()
            if not value:
                raise RuntimeError(f"task_analysis.capability_combinations[{index}].{source_key} is required")
            normalized_required[output_key] = value
        combinations.append(
            {
                "combination_name": name,
                "included_capabilities": included,
                **normalized_required,
                "expected_tool_usage": _normalize_optional_string_list(
                    combination.get("expected_tool_usage"),
                ),
                "difficulty_level": int(combination.get("difficulty_level", 3) or 3),
            }
        )
    return combinations


def _normalize_test_dimension(
    dimension: Any,
    *,
    index: int,
    capability_names: set[str],
    combination_names: set[str],
) -> dict[str, Any]:
    field_prefix = f"task_analysis.test_dimensions[{index}]"
    if not isinstance(dimension, dict):
        raise RuntimeError(f"{field_prefix} must be a mapping")
    name = str(dimension.get("name", "")).strip()
    description = str(dimension.get("description", "")).strip()
    if not name or not description:
        raise RuntimeError(f"{field_prefix} requires name and description")
    target_capabilities = _normalize_string_list(
        dimension.get("target_capabilities"),
        field=f"{field_prefix}.target_capabilities",
    )
    unknown_capabilities = [capability for capability in target_capabilities if capability not in capability_names]
    if unknown_capabilities:
        raise RuntimeError(f"{field_prefix}.target_capabilities contains unknown capabilities: {unknown_capabilities}")
    capability_combination = str(dimension.get("capability_combination", "") or "").strip()
    if capability_combination not in combination_names:
        raise RuntimeError(f"{field_prefix}.capability_combination must reference capability_combinations")
    common_failure_modes = _normalize_string_list(
        dimension.get("common_failure_modes"),
        field=f"{field_prefix}.common_failure_modes",
    )
    difficulty_factors = _normalize_string_list(
        dimension.get("difficulty_factors"),
        field=f"{field_prefix}.difficulty_factors",
    )
    observable_behavior = str(dimension.get("observable_behavior", "") or "").strip()
    verifier_design = str(dimension.get("verifier_design", "") or "").strip()
    if not observable_behavior:
        raise RuntimeError(f"{field_prefix}.observable_behavior is required")
    if not verifier_design:
        raise RuntimeError(f"{field_prefix}.verifier_design is required")
    return {
        "name": name,
        "description": description,
        "difficulty": str(dimension.get("difficulty", "medium") or "medium"),
        "target_capabilities": target_capabilities,
        "capability_combination": capability_combination,
        "observable_behavior": observable_behavior,
        "common_failure_modes": common_failure_modes,
        "difficulty_factors": difficulty_factors,
        "verifier_design": verifier_design,
    }


def _normalize_task_analysis(
    raw: Any,
    *,
    task: str,
    coverage_dimensions: list[str],
    target_case_count: int,
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise RuntimeError("dataset generation output missing task_analysis mapping")

    dimensions = raw.get("test_dimensions")
    if not isinstance(dimensions, list) or not dimensions:
        raise RuntimeError("task_analysis.test_dimensions must be a non-empty list")
    if len(dimensions) != target_case_count:
        raise RuntimeError(
            "task_analysis.test_dimensions count must match target case count: "
            f"expected={target_case_count}, actual={len(dimensions)}"
        )

    capability_graph = _normalize_capability_graph(raw.get("capability_graph"))
    capability_names = {
        str(capability["capability_name"])
        for capability in capability_graph
        if str(capability.get("capability_name", "")).strip()
    }
    capability_combinations = _normalize_capability_combinations(
        raw.get("capability_combinations"),
        capability_names=capability_names,
    )
    combination_names = {
        str(combination["combination_name"])
        for combination in capability_combinations
        if str(combination.get("combination_name", "")).strip()
    }

    normalized_dimensions: list[dict[str, Any]] = []
    for index, dimension in enumerate(dimensions, start=1):
        normalized_dimensions.append(
            _normalize_test_dimension(
                dimension,
                index=index,
                capability_names=capability_names,
                combination_names=combination_names,
            )
        )

    return {
        "task": task,
        "task_type": str(raw.get("task_type", "agent_task") or "agent_task"),
        "generator": str(raw.get("generator", "model") or "model"),
        "generation_strategy": DATASET_SOURCE,
        "scenario_summary": str(raw.get("scenario_summary", "") or ""),
        "judge_skill_names": _normalize_optional_string_list(raw.get("judge_skill_names")),
        "required_case_evidence": _normalize_optional_string_list(raw.get("required_case_evidence")),
        "coverage_dimensions": coverage_dimensions,
        "capability_graph": capability_graph,
        "capability_combinations": capability_combinations,
        "test_dimensions": normalized_dimensions,
        "training_intent_taxonomy": list(GENERIC_TRAINING_INTENT_TAXONOMY),
    }


def _normalize_case_specs(
    raw: Any,
    *,
    task_analysis: dict[str, Any],
    target_case_count: int,
) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise RuntimeError("case_specs must be a list")
    if len(raw) != target_case_count:
        raise RuntimeError(
            f"case_specs count must match target case count: expected={target_case_count}, actual={len(raw)}"
        )

    dimensions = {
        str(dimension.get("name")): dimension
        for dimension in task_analysis.get("test_dimensions", [])
        if isinstance(dimension, dict) and str(dimension.get("name", "")).strip()
    }
    if not dimensions:
        raise RuntimeError("task_analysis.test_dimensions must be available for case specs")

    taxonomy = {
        str(intent.get("intent")): intent
        for intent in task_analysis.get("training_intent_taxonomy", GENERIC_TRAINING_INTENT_TAXONOMY)
        if isinstance(intent, dict) and str(intent.get("intent", "")).strip()
    }
    if not taxonomy:
        raise RuntimeError("training_intent_taxonomy must be available for case specs")

    combination_capabilities = {
        str(combination.get("combination_name")): [
            str(capability).strip()
            for capability in combination.get("included_capabilities", [])
            if str(capability).strip()
        ]
        for combination in task_analysis.get("capability_combinations", [])
        if isinstance(combination, dict) and str(combination.get("combination_name", "")).strip()
    }

    seen_ids: set[str] = set()
    seen_user_visible_requests: set[tuple[str, tuple[str, ...]]] = set()
    normalized: list[dict[str, Any]] = []
    for index, spec in enumerate(raw, start=1):
        if not isinstance(spec, dict):
            raise RuntimeError(f"case_specs[{index}] must be a mapping")

        case_id_hint = str(spec.get("case_id_hint", "") or "").strip()
        if not case_id_hint:
            raise RuntimeError(f"case_specs[{index}].case_id_hint is required")
        if _is_placeholder_case_id(case_id_hint):
            raise RuntimeError(f"case_specs[{index}].case_id_hint must be descriptive, got {case_id_hint!r}")
        if case_id_hint in seen_ids:
            base_case_id_hint = case_id_hint
            suffix = index
            while case_id_hint in seen_ids:
                case_id_hint = f"{base_case_id_hint}_{suffix:03d}"
                suffix += 1
            spec = dict(spec)
            spec["case_id_hint"] = case_id_hint
            metadata = dict(spec.get("metadata") or {})
            metadata["deduplicated_case_id_hint"] = base_case_id_hint
            spec["metadata"] = metadata
        seen_ids.add(case_id_hint)

        dimension_name = str(
            spec.get("dimension", spec.get("target_dimension", spec.get("dimension_name", ""))) or ""
        ).strip()
        if dimension_name not in dimensions:
            raise RuntimeError(f"case_specs[{index}].dimension must reference test_dimensions")
        dimension = dimensions[dimension_name]

        difficulty = str(spec.get("difficulty", dimension.get("difficulty", "medium")) or "").strip()
        if difficulty not in {"easy", "medium", "hard"}:
            raise RuntimeError(f"case_specs[{index}].difficulty must be easy|medium|hard")

        intent_name = str(spec.get("training_intent", "") or "").strip()
        if intent_name not in taxonomy:
            raise RuntimeError(f"case_specs[{index}].training_intent must reference training_intent_taxonomy")
        training_intent = dict(taxonomy[intent_name])

        expected_target = str(
            spec.get(
                "expected_optimization_target",
                training_intent.get("expected_optimization_target", "member_harness"),
            )
            or ""
        ).strip()
        if expected_target not in {"team_skill", "member_harness"}:
            raise RuntimeError(f"case_specs[{index}].expected_optimization_target must be team_skill or member_harness")
        training_intent["expected_optimization_target"] = expected_target

        target_surfaces = _normalize_target_surfaces(
            spec.get("target_surfaces", training_intent.get("target_surfaces", ["skill"])),
            case_index=index,
        )
        training_intent["target_surfaces"] = target_surfaces

        capability_combination = str(
            spec.get("capability_combination", dimension.get("capability_combination", "")) or ""
        ).strip()
        if not capability_combination:
            raise RuntimeError(f"case_specs[{index}].capability_combination is required")
        target_capabilities = _normalize_string_list(
            spec.get("target_capabilities", dimension.get("target_capabilities", [])),
            field=f"case_specs[{index}].target_capabilities",
        )
        target_capabilities = _merge_ordered_strings(
            combination_capabilities.get(capability_combination, []),
            target_capabilities,
        )

        user_visible_challenge = str(spec.get("user_visible_challenge", "") or "").strip()
        if not user_visible_challenge:
            raise RuntimeError(f"case_specs[{index}].user_visible_challenge is required")

        critical_constraints = _normalize_string_list(
            spec.get("critical_user_constraints"),
            field=f"case_specs[{index}].critical_user_constraints",
        )
        user_visible_request_signature = (
            _canonical_user_message(user_visible_challenge),
            tuple(_canonical_user_message(item) for item in critical_constraints),
        )
        if user_visible_request_signature in seen_user_visible_requests:
            raise RuntimeError(f"case_specs[{index}].user_visible_challenge duplicates another case")
        seen_user_visible_requests.add(user_visible_request_signature)
        expected_failure_modes = _normalize_string_list(
            spec.get("expected_failure_modes"),
            field=f"case_specs[{index}].expected_failure_modes",
        )
        verifier_contract = _normalize_string_list(
            spec.get("verifier_contract"),
            field=f"case_specs[{index}].verifier_contract",
        )
        challenge_focus = str(spec.get("challenge_focus", "") or "").strip()
        if not challenge_focus:
            challenge_focus = _challenge_focus_for_difficulty(difficulty)

        normalized.append(
            {
                "case_id_hint": case_id_hint,
                "source_gap": str(spec.get("source_gap", "") or "").strip(),
                "observed_gap": (dict(spec.get("observed_gap")) if isinstance(spec.get("observed_gap"), dict) else {}),
                "dimension": dict(dimension),
                "difficulty": difficulty,
                "training_intent": training_intent,
                "expected_optimization_target": expected_target,
                "target_surfaces": target_surfaces,
                "target_capabilities": target_capabilities,
                "capability_combination": capability_combination,
                "user_visible_challenge": user_visible_challenge,
                "critical_user_constraints": critical_constraints,
                "expected_failure_modes": expected_failure_modes,
                "verifier_contract": verifier_contract,
                "challenge_focus": challenge_focus,
            }
        )
    return normalized


def _bind_case_spec_to_selected_dimension(
    case_spec: dict[str, Any],
    *,
    task_analysis: dict[str, Any],
    case_index: int,
) -> dict[str, Any]:
    """Bind deterministic routing metadata owned by the dataset planner."""
    dimensions = [item for item in task_analysis.get("test_dimensions", []) if isinstance(item, dict)]
    selected_dimension = dimensions[case_index - 1] if 0 <= case_index - 1 < len(dimensions) else None
    if selected_dimension is None:
        return dict(case_spec)

    bound = dict(case_spec)
    dimension_name = str(selected_dimension.get("name", "") or "").strip()
    if dimension_name:
        bound["dimension"] = dimension_name
    if not bound.get("target_capabilities"):
        bound["target_capabilities"] = list(selected_dimension.get("target_capabilities", []) or [])
    if not str(bound.get("capability_combination", "") or "").strip():
        bound["capability_combination"] = str(selected_dimension.get("capability_combination", "") or "")
    if not str(bound.get("difficulty", "") or "").strip():
        bound["difficulty"] = str(selected_dimension.get("difficulty", "medium") or "medium")
    return bound


def _merge_ordered_strings(*groups: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            value = str(item).strip()
            if value and value not in seen:
                seen.add(value)
                merged.append(value)
    return merged


def _challenge_focus_for_difficulty(difficulty: str) -> str:
    if difficulty == "hard":
        return (
            "Make this a hard, discriminative evaluation case. Include multiple "
            "simultaneous constraints, cross-role handoffs or artifact "
            "dependencies, quantitative or acceptance criteria where relevant, "
            "evidence traceability expectations, and at least one failure mode "
            "that a generic model answer is likely to miss. The case should still "
            "be solvable, but it should expose weak planning, shallow domain "
            "reasoning, output-contract drift, missing validation, or a tool/runtime "
            "gap."
        )
    if difficulty == "medium":
        return (
            "Make this a medium calibration case. It should require concrete, "
            "task-specific output and artifacts, but avoid obscure domain traps. "
            "It should distinguish a structured task-specific answer from generic "
            "advice."
        )
    return (
        "Make this an easy sanity-check case that verifies basic task following, "
        "artifact shape, and relevance. Do not make the majority of the dataset easy."
    )


def _normalize_cases(
    raw: Any,
    *,
    dataset_id: str,
    task: str,
    task_analysis: dict[str, Any],
    target_count: int,
) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        raise RuntimeError("dataset generation output must include cases list")
    if len(raw) < target_count:
        raise RuntimeError(
            f"dataset generation returned fewer cases than requested: requested={target_count}, returned={len(raw)}"
        )

    normalized_cases: list[dict[str, Any]] = []
    for index, case in enumerate(raw[:target_count], start=1):
        normalized_cases.append(
            _normalize_case(
                case,
                dataset_id=dataset_id,
                task=task,
                task_type=str(task_analysis.get("task_type", "agent_task")),
                index=index,
            )
        )
    return normalized_cases


def _normalize_case(
    raw: Any,
    *,
    dataset_id: str,
    task: str,
    task_type: str,
    index: int,
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise RuntimeError(f"case[{index}] must be a mapping")

    case_id = str(raw.get("case_id", "")).strip()
    if not case_id:
        raise RuntimeError(f"case[{index}] requires case_id")
    if _is_placeholder_case_id(case_id):
        raise RuntimeError(f"case[{index}].case_id must be descriptive, got {case_id!r}")

    user_message = _extract_user_message(raw)
    reference = _normalize_reference(raw.get("reference"), case_index=index)
    user_message = _disclose_web_verification_selectors(user_message, reference)
    training_signal = _normalize_training_signal(raw.get("training_signal"), case_index=index)
    metadata = _normalize_case_metadata(
        raw.get("metadata"),
        task=task,
        task_type=task_type,
    )

    return {
        "case_id": case_id,
        "dataset_id": dataset_id,
        "schema_version": "1.0",
        "source": DATASET_SOURCE,
        "task_type": task_type,
        "input": {"user_message": user_message},
        "reference": reference,
        "training_signal": training_signal,
        "metadata": metadata,
    }


def _disclose_web_verification_selectors(
    user_message: str,
    reference: dict[str, Any],
) -> str:
    """Make fixed browser hooks and expected states part of the user contract."""
    verification = reference.get("web_verification", {})
    steps = verification.get("steps", []) if isinstance(verification, dict) else []
    selectors: list[str] = []
    state_contracts: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for step in steps if isinstance(steps, list) else []:
        if not isinstance(step, dict):
            continue
        selector = str(step.get("selector", "") or "").strip()
        if selector and selector not in seen:
            selectors.append(selector)
            seen.add(selector)
        assertion = str(step.get("assert", "") or "").strip()
        if assertion in {"has_class", "not_has_class", "text_contains"}:
            value = str(step.get("value", "") or "").strip()
            if selector and value:
                state_contracts.append((assertion, selector, value))
    missing = [selector for selector in selectors if selector not in user_message]
    missing_states = [item for item in state_contracts if item[2] not in user_message]
    is_chinese = bool(re.search(r"[\u4e00-\u9fff]", user_message))
    clauses: list[str] = []
    if missing:
        selector_text = ", ".join(missing)
        if is_chinese:
            clauses.append(f"相关交互步骤必须提供可操作的 DOM 选择器 {selector_text}")
        else:
            clauses.append(f"related interactions must expose operable DOM selectors {selector_text}")
    for assertion, selector, value in missing_states:
        if is_chinese:
            if assertion == "has_class":
                clauses.append(f"{selector} 必须具有 CSS 类 {value}")
            elif assertion == "not_has_class":
                clauses.append(f"{selector} 不得具有 CSS 类 {value}")
            else:
                clauses.append(f"{selector} 的文本必须包含“{value}”")
        elif assertion == "has_class":
            clauses.append(f"{selector} must have CSS class {value}")
        elif assertion == "not_has_class":
            clauses.append(f"{selector} must not have CSS class {value}")
        else:
            clauses.append(f'{selector} text must contain "{value}"')
    sequence: list[str] = []
    for index, step in enumerate(steps if isinstance(steps, list) else [], start=1):
        if not isinstance(step, dict):
            continue
        selector = str(step.get("selector", "") or "").strip()
        action = str(step.get("action", "") or "").strip()
        assertion = str(step.get("assert", "") or "").strip()
        value = str(step.get("value", "") or "").strip()
        if action == "wait":
            milliseconds = int(step.get("milliseconds", 0) or 0)
            text = f"等待 {milliseconds}ms" if is_chinese else f"wait {milliseconds}ms"
        elif action:
            if is_chinese:
                text = f"对 {selector} 执行 {action}（此时该元素必须存在且可操作）"
            else:
                text = f"perform {action} on {selector} (it must exist and be operable then)"
        elif assertion:
            expected = f"，期望值 {value}" if is_chinese and value else ""
            if not is_chinese and value:
                expected = f", expected value {value}"
            if is_chinese:
                text = f"断言 {selector} 满足 {assertion}{expected}（此状态必须由前述步骤可达）"
            else:
                text = (
                    f"assert {selector} satisfies {assertion}{expected} "
                    "(this state must be reachable from the preceding steps)"
                )
        else:
            continue
        sequence.append(f"{index}. {text}")

    sequence_marker = (
        "机器步骤（从初始页面仅执行一次）"
        if is_chinese
        else ("Machine steps (execute exactly once from a fresh initial page)")
    )
    if sequence and sequence_marker not in user_message:
        separator = "：" if is_chinese else ": "
        joiner = "；" if is_chinese else "; "
        clauses.append(f"{sequence_marker}{separator}{joiner.join(sequence)}")
    if not clauses:
        return user_message
    if is_chinese:
        disclosure = f"自动化验收约定：{'；'.join(clauses)}。"
    else:
        disclosure = f"Automation contract: {'; '.join(clauses)}."
    return f"{user_message.rstrip()} {disclosure}"


def _hydrate_case_from_spec(raw: Any, *, case_spec: dict[str, Any]) -> Any:
    """Fill deterministic case fields from the assigned spec.

    Case generation asks the model only for the variable task and judge content.
    Routing metadata and structured constraints already exist in the case spec,
    so copying them in code keeps the model response compact and prevents schema
    drift between generation and evaluation.
    """
    if not isinstance(raw, dict):
        return raw

    case = dict(raw)
    expected_case_id = str(case_spec.get("case_id_hint", "") or "").strip()
    if expected_case_id:
        case["case_id"] = expected_case_id

    input_block = dict(case.get("input")) if isinstance(case.get("input"), dict) else {}
    if "user_message" not in input_block and case.get("user_message"):
        input_block["user_message"] = case.get("user_message")
    input_block["critical_user_constraints"] = list(case_spec.get("critical_user_constraints") or [])
    case["input"] = input_block

    case["reference"] = _hydrate_reference_from_spec(
        case.get("reference"),
        case_spec=case_spec,
    )
    case["training_signal"] = _hydrate_training_signal_from_spec(
        case.get("training_signal"),
        case_spec=case_spec,
    )
    case["metadata"] = _hydrate_metadata_from_spec(
        case.get("metadata"),
        case_spec=case_spec,
    )
    return case


def _hydrate_reference_from_spec(
    raw: Any,
    *,
    case_spec: dict[str, Any],
) -> dict[str, Any]:
    reference = dict(raw) if isinstance(raw, dict) else {}
    verifier_rules = _first_non_empty_list(
        reference.get("verifier", {}).get("test_cases_or_rules")
        if isinstance(reference.get("verifier"), dict)
        else None,
        case_spec.get("verifier_contract"),
        case_spec.get("critical_user_constraints"),
    )
    failure_modes = _first_non_empty_list(
        reference.get("distractors_or_traps"),
        case_spec.get("expected_failure_modes"),
        case_spec.get("critical_user_constraints"),
    )
    capability_summary = ", ".join(_normalize_optional_string_list(case_spec.get("target_capabilities"))[:3])
    challenge = str(case_spec.get("challenge_focus") or "").strip()
    if not challenge:
        challenge = str(case_spec.get("user_visible_challenge") or "").strip()

    reference.setdefault(
        "required_behaviors",
        [
            {
                "id": "capability_contract",
                "description": ("The deliverable demonstrates the assigned capability combination."),
                "weight": 0.7,
                "rubric": challenge or capability_summary or "Judge the target capability.",
            },
            {
                "id": "artifact_acceptance",
                "description": "The final artifacts satisfy the visible task contract.",
                "weight": 0.3,
                "rubric": "; ".join(verifier_rules[:2]),
            },
        ],
    )
    reference.setdefault(
        "forbidden_behaviors",
        [
            {
                "id": "known_failure_mode",
                "description": failure_modes[0],
                "penalty": 0.3,
            }
        ],
    )
    reference.setdefault(
        "judge_rubric",
        {
            "pass_threshold": 0.8,
            "criteria": verifier_rules[:3],
        },
    )
    reference.setdefault("expected_steps", verifier_rules[:3])
    reference.setdefault("distractors_or_traps", failure_modes[:2])
    reference.setdefault(
        "success_criteria",
        _first_non_empty_list(
            reference.get("success_criteria"),
            case_spec.get("critical_user_constraints"),
            verifier_rules,
        )[:3],
    )
    reference.setdefault("failure_criteria", failure_modes[:2])
    reference.setdefault(
        "verifier",
        {
            "type": "llm_judge_or_artifact_check",
            "check_method": "; ".join(verifier_rules[:2]),
            "test_cases_or_rules": verifier_rules[:2],
        },
    )
    reference.setdefault(
        "gold_answer_or_expected_artifact",
        challenge or "The final deliverable satisfies the assigned case contract.",
    )
    return reference


def _hydrate_training_signal_from_spec(
    raw: Any,
    *,
    case_spec: dict[str, Any],
) -> dict[str, Any]:
    training_intent = (
        dict(case_spec.get("training_intent")) if isinstance(case_spec.get("training_intent"), dict) else {}
    )
    signal = dict(raw) if isinstance(raw, dict) else {}
    signal["diagnostic_intent"] = str(
        training_intent.get("intent", signal.get("diagnostic_intent", "model_generated")) or "model_generated"
    )
    signal["expected_optimization_target"] = str(
        case_spec.get(
            "expected_optimization_target",
            training_intent.get("expected_optimization_target", "member_harness"),
        )
        or "member_harness"
    )
    signal["target_capabilities"] = list(case_spec.get("target_capabilities") or [])
    signal["capability_combination"] = str(case_spec.get("capability_combination", "") or "")
    signal["expected_failure_modes"] = _first_non_empty_list(
        case_spec.get("expected_failure_modes"),
        signal.get("expected_failure_modes"),
        case_spec.get("critical_user_constraints"),
    )
    signal["capability_gap"] = str(
        signal.get("capability_gap")
        or case_spec.get("challenge_focus")
        or case_spec.get("user_visible_challenge")
        or "Probe the assigned capability gap."
    )
    target_surfaces = list(case_spec.get("target_surfaces") or training_intent.get("target_surfaces") or ["skill"])
    signal["target_surfaces"] = target_surfaces
    signal["expected_target_surfaces"] = target_surfaces
    signal["difficulty_rationale"] = str(
        signal.get("difficulty_rationale")
        or case_spec.get("challenge_focus")
        or f"Assigned difficulty: {case_spec.get('difficulty', 'medium')}"
    )
    return signal


def _hydrate_metadata_from_spec(raw: Any, *, case_spec: dict[str, Any]) -> dict[str, Any]:
    metadata = dict(raw) if isinstance(raw, dict) else {}
    dimension = case_spec.get("dimension")
    dimension_name = (
        str(dimension.get("name", "") or "").strip() if isinstance(dimension, dict) else str(dimension or "").strip()
    )
    if dimension_name:
        metadata["dimension"] = dimension_name
    metadata["difficulty"] = str(case_spec.get("difficulty", "medium") or "medium")
    training_intent = case_spec.get("training_intent")
    if isinstance(training_intent, dict):
        metadata["training_intent"] = str(training_intent.get("intent", "model_generated") or "model_generated")
    return metadata


def _first_non_empty_list(*values: Any) -> list[str]:
    for value in values:
        normalized = _normalize_optional_string_list(value)
        if normalized:
            return normalized
    return ["Judge the case-specific deliverable contract."]


def _validate_raw_case_against_spec(
    raw: Any,
    *,
    case_spec: dict[str, Any],
    seen_user_messages: set[str],
    case_index: int,
) -> None:
    if not isinstance(raw, dict):
        raise RuntimeError(f"case[{case_index}] must be a mapping")

    case_id = str(raw.get("case_id", "") or "").strip()
    expected_case_id = str(case_spec.get("case_id_hint", "") or "").strip()
    if not case_id:
        raise RuntimeError(f"case[{case_index}] requires case_id")
    if _is_placeholder_case_id(case_id):
        raise RuntimeError(f"case[{case_index}].case_id must be descriptive")
    if expected_case_id and case_id != expected_case_id:
        raise RuntimeError(f"case[{case_index}].case_id must equal case_id_hint: {expected_case_id}")

    user_message = _extract_user_message(raw)
    canonical_user_message = _canonical_user_message(user_message)
    if canonical_user_message in seen_user_messages:
        raise RuntimeError(f"case[{case_index}].input.user_message duplicates an earlier case")

    expected_constraints = _normalize_optional_string_list(case_spec.get("critical_user_constraints"))
    if expected_constraints:
        input_block = raw.get("input")
        raw_constraints = input_block.get("critical_user_constraints") if isinstance(input_block, dict) else None
        if raw_constraints != expected_constraints:
            raise RuntimeError(f"case[{case_index}].input.critical_user_constraints must match case spec")

    training_signal = raw.get("training_signal")
    if not isinstance(training_signal, dict):
        raise RuntimeError(f"case[{case_index}].training_signal must be a mapping")
    expected_target = str(case_spec.get("expected_optimization_target", "") or "").strip()
    if (
        expected_target
        and str(training_signal.get("expected_optimization_target", "") or "").strip() != expected_target
    ):
        raise RuntimeError(f"case[{case_index}].training_signal.expected_optimization_target must match case spec")
    expected_surfaces = list(case_spec.get("target_surfaces", []))
    raw_surfaces = training_signal.get("target_surfaces")
    if expected_surfaces and raw_surfaces != expected_surfaces:
        raise RuntimeError(f"case[{case_index}].training_signal.target_surfaces must match case spec")

    metadata = raw.get("metadata")
    if not isinstance(metadata, dict):
        raise RuntimeError(f"case[{case_index}].metadata must be a mapping")
    dimension_name = str(case_spec.get("dimension", {}).get("name", "") or "").strip()
    if dimension_name and str(metadata.get("dimension", "") or "").strip() != dimension_name:
        raise RuntimeError(f"case[{case_index}].metadata.dimension must match case spec")
    difficulty = str(case_spec.get("difficulty", "") or "").strip()
    if difficulty and str(metadata.get("difficulty", "") or "").strip() != difficulty:
        raise RuntimeError(f"case[{case_index}].metadata.difficulty must match case spec")


def _case_quality_acceptance(
    case: dict[str, Any],
    *,
    case_spec: dict[str, Any],
) -> dict[str, Any]:
    reference = case.get("reference", {})
    verifier = reference.get("verifier", {}) if isinstance(reference, dict) else {}
    difficulty = str(case.get("metadata", {}).get("difficulty", "medium"))
    difficulty_score = {"easy": 2, "medium": 3, "hard": 4}.get(difficulty, 3)
    return {
        "case_id": case["case_id"],
        "accepted": True,
        "quality_score": 10,
        "difficulty_score": difficulty_score,
        "capability_alignment_score": 10,
        "verifiability_score": 10 if verifier.get("test_cases_or_rules") else 8,
        "realism_score": 8,
        "target_capabilities": case.get("training_signal", {}).get("target_capabilities", []),
        "capability_combination": case_spec.get("capability_combination", ""),
        "final_decision_reason": "Passed deterministic dataset quality gates.",
        "main_issues": [],
        "revision_suggestions": [],
    }


def _normalize_case_quality_review(
    raw: Any,
    *,
    case: dict[str, Any],
    case_spec: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise RuntimeError("case quality review must be a JSON object")

    quality_score = _quality_score(raw.get("quality_score"), default=0, maximum=10)
    difficulty_score = _quality_score(raw.get("difficulty_score"), default=0, maximum=5)
    capability_alignment_score = _quality_score(
        raw.get("capability_alignment_score"),
        default=0,
        maximum=10,
    )
    verifiability_score = _quality_score(
        raw.get("verifiability_score"),
        default=0,
        maximum=10,
    )
    realism_score = _quality_score(raw.get("realism_score"), default=0, maximum=10)
    accepted = bool(raw.get("accepted", False))
    main_issues = _normalize_optional_string_list(raw.get("main_issues"))
    revision_suggestions = _normalize_optional_string_list(raw.get("revision_suggestions"))
    final_decision_reason = str(raw.get("final_decision_reason", "") or "").strip()
    critical_constraints_covered = bool(
        raw.get("critical_constraints_covered", raw.get("constraints_covered", accepted))
    )
    if not accepted and (not main_issues or not revision_suggestions):
        raise RuntimeError("rejected case quality review requires non-empty main_issues and revision_suggestions")
    if accepted and not critical_constraints_covered:
        raise RuntimeError("case quality review is inconsistent: accepted but critical_constraints_covered is false")
    lowered_reason = final_decision_reason.lower()
    if not accepted and ("no revision" in lowered_reason or "no revisions" in lowered_reason):
        raise RuntimeError("case quality review is inconsistent: rejected but says no revision is needed")

    return {
        "case_id": case["case_id"],
        "accepted": accepted,
        "critical_constraints_covered": critical_constraints_covered,
        "quality_score": quality_score,
        "difficulty_score": difficulty_score,
        "capability_alignment_score": capability_alignment_score,
        "verifiability_score": verifiability_score,
        "realism_score": realism_score,
        "target_capabilities": case.get("training_signal", {}).get(
            "target_capabilities",
            [],
        ),
        "capability_combination": case_spec.get("capability_combination", ""),
        "main_issues": main_issues,
        "revision_suggestions": revision_suggestions,
        "final_decision_reason": final_decision_reason,
    }


def _quality_score(raw: Any, *, default: int, maximum: int) -> int:
    try:
        score = int(raw)
    except (TypeError, ValueError):
        score = default
    return max(0, min(maximum, score))


def _quality_review_passes(
    review: dict[str, Any],
    *,
    config: DatasetGeneratorConfig,
    case: dict[str, Any],
) -> bool:
    difficulty_score_threshold = _difficulty_score_threshold_for_case(
        case,
        default=config.difficulty_score_threshold,
    )
    return (
        bool(review.get("accepted", False))
        and bool(review.get("critical_constraints_covered", False))
        and int(review.get("quality_score", 0)) >= config.quality_score_threshold
        and int(review.get("capability_alignment_score", 0)) >= config.capability_alignment_score_threshold
        and int(review.get("verifiability_score", 0)) >= config.verifiability_score_threshold
        and int(review.get("difficulty_score", 0)) >= difficulty_score_threshold
    )


def _difficulty_score_threshold_for_case(
    case: dict[str, Any],
    *,
    default: int,
) -> int:
    difficulty = str(case.get("metadata", {}).get("difficulty", "") or "").strip().lower()
    return {"easy": 2, "medium": 3, "hard": 4}.get(difficulty, default)


def _quality_review_feedback(review: dict[str, Any]) -> str:
    return (
        "Quality review rejected the previous generated case. "
        f"scores={{quality:{review.get('quality_score')}, "
        f"difficulty:{review.get('difficulty_score')}, "
        f"alignment:{review.get('capability_alignment_score')}, "
        f"verifiability:{review.get('verifiability_score')}, "
        f"realism:{review.get('realism_score')}}}; "
        f"main_issues={review.get('main_issues', [])}; "
        f"revision_suggestions={review.get('revision_suggestions', [])}; "
        f"reason={review.get('final_decision_reason', '')}"
    )


def _is_placeholder_case_id(case_id: str) -> bool:
    lowered = str(case_id or "").strip().lower()
    placeholder_fragments = (
        "stable_unique_id",
        "descriptive_unique_case_id",
        "sample_case",
        "example_case",
        "case_id",
    )
    return any(fragment in lowered for fragment in placeholder_fragments)


def _canonical_user_message(message: str) -> str:
    return " ".join(str(message or "").strip().lower().split())


def _extract_user_message(raw: dict[str, Any]) -> str:
    input_block = raw.get("input", raw.get("inputs"))
    if isinstance(input_block, dict):
        user_message = input_block.get("user_message", input_block.get("query", ""))
    else:
        user_message = raw.get("user_message", raw.get("query", ""))
    user_message_text = str(user_message or "").strip()
    if not user_message_text:
        raise RuntimeError("dataset case requires input.user_message")
    return user_message_text


def _normalize_reference(raw: Any, *, case_index: int) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise RuntimeError(f"case[{case_index}].reference must be a mapping")

    required = _normalize_required_behaviors(
        raw.get("required_behaviors"),
        case_index=case_index,
    )
    forbidden = _normalize_forbidden_behaviors(raw.get("forbidden_behaviors"))
    judge_rubric = raw.get("judge_rubric", raw.get("rubric"))
    if not isinstance(judge_rubric, dict) or not judge_rubric:
        raise RuntimeError(f"case[{case_index}].reference.judge_rubric is required")
    expected_steps = _normalize_string_list(
        raw.get("expected_steps"),
        field=f"case[{case_index}].reference.expected_steps",
    )
    distractors_or_traps = _normalize_string_list(
        raw.get("distractors_or_traps"),
        field=f"case[{case_index}].reference.distractors_or_traps",
    )
    success_criteria = _normalize_string_list(
        raw.get("success_criteria"),
        field=f"case[{case_index}].reference.success_criteria",
    )
    failure_criteria = _normalize_string_list(
        raw.get("failure_criteria"),
        field=f"case[{case_index}].reference.failure_criteria",
    )
    verifier = _normalize_reference_verifier(
        raw.get("verifier"),
        case_index=case_index,
    )
    web_verification = _normalize_web_verification(raw.get("web_verification"))
    gold_artifact = str(
        raw.get(
            "gold_answer_or_expected_artifact",
            raw.get("expected_artifact", raw.get("expected_output", "")),
        )
        or ""
    ).strip()
    if not gold_artifact:
        raise RuntimeError(f"case[{case_index}].reference.gold_answer_or_expected_artifact is required")

    return {
        "required_behaviors": required,
        "forbidden_behaviors": forbidden,
        "judge_rubric": judge_rubric,
        "expected_steps": expected_steps,
        "distractors_or_traps": distractors_or_traps,
        "success_criteria": success_criteria,
        "failure_criteria": failure_criteria,
        "verifier": verifier,
        "web_verification": web_verification,
        "gold_answer_or_expected_artifact": gold_artifact,
    }


def _normalize_web_verification(raw: Any) -> dict[str, Any]:
    """Keep a small declarative browser contract; executable code is forbidden."""
    if not isinstance(raw, dict):
        return {}
    steps = raw.get("steps")
    if not isinstance(steps, list):
        return {}
    allowed_actions = {"click", "wait"}
    assertion_aliases = {
        "element_exists": "exists",
        "is_visible": "visible",
        "is_hidden": "hidden",
    }
    allowed_assertions = {
        "exists",
        "visible",
        "hidden",
        "has_class",
        "not_has_class",
        "text_contains",
        "enabled",
        "disabled",
        "count_equals",
        "count_at_least",
        "count_at_most",
        "computed_style_not_default",
    }
    non_empty_value_assertions = {
        "has_class",
        "not_has_class",
        "text_contains",
        "computed_style_not_default",
    }
    count_assertions = {"count_equals", "count_at_least", "count_at_most"}
    normalized: list[dict[str, Any]] = []
    for item in steps[:20]:
        if not isinstance(item, dict):
            continue
        action = str(item.get("action", "") or "").strip()
        assertion = str(item.get("assert", "") or "").strip()
        assertion = assertion_aliases.get(assertion, assertion)
        if action in allowed_actions:
            step: dict[str, Any] = {"action": action}
            if action == "click":
                selector = str(item.get("selector", "") or "").strip()[:300]
                if not selector:
                    continue
                step["selector"] = selector
            else:
                step["milliseconds"] = max(0, min(int(item.get("milliseconds", 0) or 0), 3000))
            normalized.append(step)
        elif assertion in allowed_assertions:
            selector = str(item.get("selector", "") or "").strip()[:300]
            if not selector:
                continue
            step = {"assert": assertion, "selector": selector}
            if assertion in non_empty_value_assertions:
                value = str(item.get("value", "") or "").strip()[:300]
                if not value:
                    raise RuntimeError(f"web_verification assertion {assertion!r} requires a non-empty value")
                step["value"] = value
            elif assertion in count_assertions:
                value = item.get("value")
                if isinstance(value, bool):
                    numeric_value = None
                elif isinstance(value, int):
                    numeric_value = value
                elif isinstance(value, float) and value.is_integer():
                    numeric_value = int(value)
                elif isinstance(value, str) and re.fullmatch(r"\d+", value.strip()):
                    numeric_value = int(value.strip())
                else:
                    numeric_value = None
                if numeric_value is None or numeric_value < 0:
                    raise RuntimeError(
                        f"web_verification assertion {assertion!r} requires a non-negative integer value"
                    )
                if assertion == "count_at_least" and numeric_value == 0:
                    raise RuntimeError("web_verification assertion 'count_at_least' requires a positive integer value")
                step["value"] = numeric_value
            elif "value" in item:
                value = item.get("value")
                step["value"] = value if isinstance(value, (int, float)) else str(value)[:300]
            normalized.append(step)
    if not any("assert" in step for step in normalized):
        return {}
    return {"steps": normalized}


def _validate_required_case_evidence(
    case: dict[str, Any],
    *,
    required: Any,
    case_index: int,
) -> None:
    """Enforce evidence declared by active domain Judge Skills."""
    required_fields = _normalize_optional_string_list(required)
    reference = case.get("reference", {})
    for field in required_fields:
        value: Any = reference
        for part in field.split("."):
            value = value.get(part) if isinstance(value, dict) else None
        if not value:
            raise RuntimeError(
                f"case[{case_index}].reference.{field} is required by the active "
                "domain Judge Skill and must contain executable machine evidence"
            )


def _normalize_reference_verifier(raw: Any, *, case_index: int) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise RuntimeError(f"case[{case_index}].reference.verifier must be a mapping")
    verifier_type = str(raw.get("type", "")).strip()
    check_method = str(raw.get("check_method", "")).strip()
    raw_rules = raw.get("test_cases_or_rules")
    if raw_rules is None:
        raw_rules = raw.get("test_cases")
    rules = _normalize_string_list(
        raw_rules,
        field=f"case[{case_index}].reference.verifier.test_cases_or_rules",
    )
    if not verifier_type or not check_method:
        raise RuntimeError(f"case[{case_index}].reference.verifier requires type and check_method")
    return {
        "type": verifier_type,
        "check_method": check_method,
        "test_cases_or_rules": rules,
    }


def _normalize_required_behaviors(raw: Any, *, case_index: int) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not raw:
        raise RuntimeError(f"case[{case_index}].reference.required_behaviors must be a non-empty list")

    behaviors: list[dict[str, Any]] = []
    for behavior_index, behavior in enumerate(raw, start=1):
        if isinstance(behavior, str):
            description = behavior.strip()
            behavior = {
                "id": f"required_{behavior_index:02d}",
                "description": description,
                "weight": 1.0,
                "rubric": description,
            }
        if not isinstance(behavior, dict):
            raise RuntimeError(f"case[{case_index}].required_behaviors[{behavior_index}] must be a mapping")
        behavior_id = str(behavior.get("id", "")).strip()
        description = str(behavior.get("description", "")).strip()
        if not behavior_id or not description:
            raise RuntimeError(f"case[{case_index}].required_behaviors[{behavior_index}] requires id and description")
        behaviors.append(
            {
                "id": behavior_id,
                "description": description,
                "weight": float(behavior.get("weight", 1.0) or 1.0),
                "rubric": str(behavior.get("rubric", description) or description),
            }
        )
    return behaviors


def _normalize_forbidden_behaviors(raw: Any) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise RuntimeError("reference.forbidden_behaviors must be a list when present")

    behaviors: list[dict[str, Any]] = []
    for behavior_index, behavior in enumerate(raw, start=1):
        if isinstance(behavior, str):
            description = behavior.strip()
            behavior = {
                "id": f"forbidden_{behavior_index:02d}",
                "description": description,
                "penalty": 0.3,
            }
        if not isinstance(behavior, dict):
            raise RuntimeError("forbidden behavior must be a mapping")
        behavior_id = str(behavior.get("id", "")).strip()
        description = str(behavior.get("description", "")).strip()
        if not behavior_id or not description:
            raise RuntimeError("forbidden behavior requires id and description")
        behaviors.append(
            {
                "id": behavior_id,
                "description": description,
                "penalty": float(behavior.get("penalty", 0.3) or 0.3),
            }
        )
    return behaviors


def _normalize_training_signal(raw: Any, *, case_index: int) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise RuntimeError(f"case[{case_index}].training_signal must be a mapping")

    diagnostic_intent = str(raw.get("diagnostic_intent", "model_generated") or "").strip()
    if not diagnostic_intent:
        diagnostic_intent = "model_generated"
    expected_optimization_target = str(raw.get("expected_optimization_target", "member_harness") or "").strip()
    if expected_optimization_target not in {"team_skill", "member_harness"}:
        raise RuntimeError(
            f"case[{case_index}].training_signal.expected_optimization_target must be team_skill or member_harness"
        )
    expected_failure_modes = _normalize_string_list(
        raw.get("expected_failure_modes"),
        field=f"case[{case_index}].training_signal.expected_failure_modes",
    )
    target_capabilities = _normalize_string_list(
        raw.get("target_capabilities"),
        field=f"case[{case_index}].training_signal.target_capabilities",
    )
    capability_combination = str(raw.get("capability_combination", "") or "").strip()
    if not capability_combination:
        raise RuntimeError(f"case[{case_index}].training_signal.capability_combination is required")
    target_surfaces = _normalize_target_surfaces(
        raw.get("target_surfaces"),
        case_index=case_index,
    )
    expected_target_surfaces = _normalize_target_surfaces(
        raw.get("expected_target_surfaces", target_surfaces),
        case_index=case_index,
    )
    capability_gap = str(raw.get("capability_gap", "") or "").strip()
    difficulty_rationale = str(raw.get("difficulty_rationale", "") or "").strip()
    if not capability_gap:
        raise RuntimeError(f"case[{case_index}].training_signal.capability_gap is required")
    if not difficulty_rationale:
        raise RuntimeError(f"case[{case_index}].training_signal.difficulty_rationale is required")

    return {
        "diagnostic_intent": diagnostic_intent,
        "expected_optimization_target": expected_optimization_target,
        "target_capabilities": target_capabilities,
        "capability_combination": capability_combination,
        "expected_failure_modes": expected_failure_modes,
        "capability_gap": capability_gap,
        "target_surfaces": target_surfaces,
        "expected_target_surfaces": expected_target_surfaces,
        "difficulty_rationale": difficulty_rationale,
    }


def _normalize_string_list(raw: Any, *, field: str) -> list[str]:
    if not isinstance(raw, list) or not raw:
        raise RuntimeError(f"{field} must be a non-empty list")
    values = [str(item).strip() for item in raw if str(item).strip()]
    if not values:
        raise RuntimeError(f"{field} must contain non-empty strings")
    return values


def _normalize_optional_string_list(raw: Any) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


def _normalize_target_surfaces(raw: Any, *, case_index: int) -> list[str]:
    allowed = {"skill", "tool", "prompt_section"}
    surfaces = _normalize_string_list(
        raw,
        field=f"case[{case_index}].training_signal.target_surfaces",
    )
    invalid = [surface for surface in surfaces if surface not in allowed]
    if invalid:
        raise RuntimeError(f"case[{case_index}].training_signal.target_surfaces contains unsupported values: {invalid}")
    return list(dict.fromkeys(surfaces))


def _normalize_case_metadata(
    raw: Any,
    *,
    task: str,
    task_type: str,
) -> dict[str, Any]:
    metadata = dict(raw) if isinstance(raw, dict) else {}
    metadata.setdefault("dimension", "model_generated")
    metadata.setdefault("difficulty", "medium")
    metadata["source"] = DATASET_SOURCE
    metadata["related_task"] = task
    metadata["task_type"] = task_type
    metadata["synthetic"] = True
    metadata["judgeable"] = True
    metadata["provenance"] = {
        "generator": "DatasetGenerator",
        "method": "llm_synthetic_generation",
    }
    return metadata


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(repair_payload_mojibake(payload), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


__all__ = [
    "DatasetGenerator",
]

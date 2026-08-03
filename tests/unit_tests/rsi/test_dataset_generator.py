# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for ``DatasetGenerator``."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import openjiuwen.rsi.dataset_generator.generator as generator_module
from openjiuwen.rsi.config import (
    DataLoaderConfig,
    DatasetGeneratorConfig,
)
from openjiuwen.rsi.data_loader import DataLoader
from openjiuwen.rsi.dataset_generator import DatasetGenerator
from openjiuwen.rsi.dataset_generator.generator import (
    GENERIC_TRAINING_INTENT_TAXONOMY,
    _build_capability_graph_prompt,
    _build_case_prompt,
    _build_case_quality_review_prompt,
    _build_single_case_spec_prompt,
    _normalize_case,
    _normalize_case_quality_review,
    _normalize_case_specs,
    _normalize_target_surfaces,
    _parse_model_json,
    _quality_review_passes,
    _validate_raw_case_against_spec,
)
from openjiuwen.rsi.orchestrator.context import OrchestratorContextStore
from openjiuwen.rsi.usage_recorder import (
    llm_usage_ledger,
    llm_usage_scope,
    summarize_llm_usage_file,
)


def test_normalize_case_discloses_fixed_web_verification_selectors() -> None:
    raw = _llm_case(1, user_message="制作一个可交互的浏览器小游戏。")
    raw["reference"]["web_verification"] = {
        "steps": [
            {"action": "click", "selector": "#card-slot-0"},
            {"assert": "text_contains", "selector": "#status-log", "value": "No cards"},
        ]
    }

    case = _normalize_case(
        raw,
        dataset_id="dataset_001",
        task="build a browser game",
        task_type="browser_game",
        index=1,
    )

    message = case["input"]["user_message"]
    assert "#card-slot-0" in message
    assert "#status-log" in message
    assert "自动化验收约定" in message
    assert "机器步骤（从初始页面仅执行一次）" in message
    assert "此时该元素必须存在且可操作" in message
    assert "此状态必须由前述步骤可达" in message


def test_normalize_case_accepts_common_verifier_test_cases_alias() -> None:
    raw_case = _llm_case(1)
    verifier = raw_case["reference"]["verifier"]
    verifier["test_cases"] = verifier.pop("test_cases_or_rules")

    case = _normalize_case(
        raw_case,
        dataset_id="dataset_001",
        task="Create an artifact",
        task_type="artifact_task",
        index=1,
    )

    assert case["reference"]["verifier"]["test_cases_or_rules"]


def test_normalize_case_accepts_common_web_assertions_and_computed_style() -> None:
    raw = _llm_case(1, user_message="Create styled cards with .card-title.")
    raw["reference"]["web_verification"] = {
        "steps": [
            {"action": "navigate", "selector": "index.html"},
            {"assert": "element_exists", "selector": ".card-title"},
            {
                "assert": "computed_style_not_default",
                "selector": ".card-title",
                "value": "color",
            },
        ]
    }

    case = _normalize_case(
        raw,
        dataset_id="dataset_001",
        task="build a page",
        task_type="web_page",
        index=1,
    )

    assert case["reference"]["web_verification"]["steps"] == [
        {"assert": "exists", "selector": ".card-title"},
        {
            "assert": "computed_style_not_default",
            "selector": ".card-title",
            "value": "color",
        },
    ]
    assert "computed_style_not_default" in case["input"]["user_message"]


@pytest.mark.parametrize(
    "assertion",
    ["text_contains", "has_class", "not_has_class", "computed_style_not_default"],
)
def test_normalize_case_rejects_vacuous_web_assertion_values(assertion: str) -> None:
    raw = _llm_case(1)
    raw["reference"]["web_verification"] = {"steps": [{"assert": assertion, "selector": "#status", "value": "   "}]}

    with pytest.raises(RuntimeError, match="requires a non-empty value"):
        _normalize_case(
            raw,
            dataset_id="dataset_001",
            task="build a page",
            task_type="web_page",
            index=1,
        )


@pytest.mark.parametrize("value", [None, True, -1, 1.5, "one"])
def test_normalize_case_rejects_invalid_web_count_values(value: object) -> None:
    raw = _llm_case(1)
    raw["reference"]["web_verification"] = {
        "steps": [{"assert": "count_at_least", "selector": ".card", "value": value}]
    }

    with pytest.raises(RuntimeError, match="requires a non-negative integer value"):
        _normalize_case(
            raw,
            dataset_id="dataset_001",
            task="build a page",
            task_type="web_page",
            index=1,
        )


def test_normalize_case_rejects_vacuous_count_at_least_zero() -> None:
    raw = _llm_case(1)
    raw["reference"]["web_verification"] = {"steps": [{"assert": "count_at_least", "selector": ".card", "value": 0}]}

    with pytest.raises(RuntimeError, match="requires a positive integer value"):
        _normalize_case(
            raw,
            dataset_id="dataset_001",
            task="build a page",
            task_type="web_page",
            index=1,
        )


def test_normalize_case_normalizes_integral_web_count_value() -> None:
    raw = _llm_case(1)
    raw["reference"]["web_verification"] = {"steps": [{"assert": "count_at_least", "selector": ".card", "value": "1"}]}

    case = _normalize_case(
        raw,
        dataset_id="dataset_001",
        task="build a page",
        task_type="web_page",
        index=1,
    )

    assert case["reference"]["web_verification"]["steps"][0]["value"] == 1


def test_normalize_case_discloses_class_selector_and_expected_class() -> None:
    raw = _llm_case(1, user_message="制作一个支持卡牌选择的浏览器小游戏。")
    raw["reference"]["web_verification"] = {
        "steps": [
            {"action": "click", "selector": ".card"},
            {"assert": "has_class", "selector": ".card", "value": "selected"},
        ]
    }

    case = _normalize_case(
        raw,
        dataset_id="dataset_001",
        task="build a browser game",
        task_type="browser_game",
        index=1,
    )

    message = case["input"]["user_message"]
    assert ".card" in message
    assert "CSS 类 selected" in message


def test_normalize_case_still_discloses_sequence_for_known_web_selector() -> None:
    raw = _llm_case(1, user_message="Create a page with #status-log.")
    raw["reference"]["web_verification"] = {"steps": [{"assert": "visible", "selector": "#status-log"}]}

    case = _normalize_case(
        raw,
        dataset_id="dataset_001",
        task="build a page",
        task_type="web_page",
        index=1,
    )

    message = case["input"]["user_message"]
    assert message.count("#status-log") == 2
    assert "Machine steps (execute exactly once from a fresh initial page)" in message


def test_capability_prompt_offers_domain_judge_skills_without_core_domain_logic() -> None:
    prompt = _build_capability_graph_prompt(
        task="Build a website",
        coverage_dimensions=[],
        target_case_count=1,
        judge_skills=generator_module.available_judge_skills(),
    )

    assert '"name": "web"' in prompt
    assert '"required_case_evidence": [' in prompt
    assert '"web_verification"' in prompt
    assert '"judge_skill_names"' in prompt


@pytest.mark.asyncio
async def test_generate_quality_checked_case_retries_schema_validation_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalid_case = _llm_case(1)
    verifier = invalid_case["reference"]["verifier"]
    verifier.pop("test_cases_or_rules")
    valid_case = _llm_case(1)
    generated = [invalid_case, valid_case]
    feedback: list[str] = []

    async def fake_generate_case(**kwargs):
        feedback.append(kwargs["initial_previous_error"])
        return generated.pop(0)

    async def fake_review_case_quality(**_kwargs):
        return {
            "case_id": "ppt_eval_001",
            "accepted": True,
            "critical_constraints_covered": True,
            "quality_score": 9,
            "difficulty_score": 4,
            "capability_alignment_score": 9,
            "verifiability_score": 9,
            "realism_score": 9,
            "main_issues": [],
            "revision_suggestions": [],
            "final_decision_reason": "valid",
        }

    generator = DatasetGenerator(
        DatasetGeneratorConfig(model_config_ref="models/dataset.yaml", min_cases=1),
        llm_runner=lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(generator, "_generate_case", fake_generate_case)
    monkeypatch.setattr(generator, "_review_case_quality", fake_review_case_quality)

    case, quality, rejected = await generator._generate_quality_checked_case(
        task="Create an artifact",
        task_analysis={"task_type": "artifact_task"},
        case_spec={"case_id_hint": "ppt_eval_001"},
        seen_user_messages=set(),
        case_index=1,
        case_count=1,
        output_path=tmp_path,
    )

    assert case["case_id"] == "ppt_eval_001"
    assert quality["accepted"] is True
    assert rejected[0]["stage"] == "schema_validation"
    assert "test_cases_or_rules must be a non-empty list" in feedback[1]


@pytest.mark.asyncio
async def test_generate_quality_checked_case_retries_vacuous_web_assertion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalid_case = _llm_case(1)
    invalid_case["reference"]["web_verification"] = {
        "steps": [{"assert": "text_contains", "selector": "#mana", "value": ""}]
    }
    valid_case = _llm_case(1)
    generated = [invalid_case, valid_case]
    feedback: list[str] = []

    async def fake_generate_case(**kwargs):
        feedback.append(kwargs["initial_previous_error"])
        return generated.pop(0)

    async def fake_review_case_quality(**_kwargs):
        return {
            "case_id": "ppt_eval_001",
            "accepted": True,
            "critical_constraints_covered": True,
            "quality_score": 9,
            "difficulty_score": 4,
            "capability_alignment_score": 9,
            "verifiability_score": 9,
            "realism_score": 9,
            "main_issues": [],
            "revision_suggestions": [],
            "final_decision_reason": "valid",
        }

    generator = DatasetGenerator(
        DatasetGeneratorConfig(model_config_ref="models/dataset.yaml", min_cases=1),
        llm_runner=lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(generator, "_generate_case", fake_generate_case)
    monkeypatch.setattr(generator, "_review_case_quality", fake_review_case_quality)

    case, _, rejected = await generator._generate_quality_checked_case(
        task="Build a website",
        task_analysis={"task_type": "web_page", "required_case_evidence": ["web_verification"]},
        case_spec={"case_id_hint": "ppt_eval_001"},
        seen_user_messages=set(),
        case_index=1,
        case_count=1,
        output_path=tmp_path,
    )

    assert case["reference"]["web_verification"]["steps"]
    assert rejected[0]["stage"] == "schema_validation"
    assert "requires a non-empty value" in feedback[1]


@pytest.mark.asyncio
async def test_generate_quality_checked_case_retries_vacuous_count_at_least(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalid_case = _llm_case(1)
    invalid_case["reference"]["web_verification"] = {
        "steps": [{"assert": "count_at_least", "selector": ".card", "value": 0}]
    }
    valid_case = _llm_case(1)
    generated = [invalid_case, valid_case]
    feedback: list[str] = []

    async def fake_generate_case(**kwargs):
        feedback.append(kwargs["initial_previous_error"])
        return generated.pop(0)

    async def fake_review_case_quality(**_kwargs):
        return {
            "case_id": "ppt_eval_001",
            "accepted": True,
            "critical_constraints_covered": True,
            "quality_score": 9,
            "difficulty_score": 4,
            "capability_alignment_score": 9,
            "verifiability_score": 9,
            "realism_score": 9,
            "main_issues": [],
            "revision_suggestions": [],
            "final_decision_reason": "valid",
        }

    generator = DatasetGenerator(
        DatasetGeneratorConfig(model_config_ref="models/dataset.yaml", min_cases=1),
        llm_runner=lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(generator, "_generate_case", fake_generate_case)
    monkeypatch.setattr(generator, "_review_case_quality", fake_review_case_quality)

    case, _, rejected = await generator._generate_quality_checked_case(
        task="Build a website",
        task_analysis={"task_type": "web_page", "required_case_evidence": ["web_verification"]},
        case_spec={"case_id_hint": "ppt_eval_001"},
        seen_user_messages=set(),
        case_index=1,
        case_count=1,
        output_path=tmp_path,
    )

    assert case["reference"]["web_verification"]["steps"]
    assert rejected[0]["stage"] == "schema_validation"
    assert "requires a positive integer value" in feedback[1]


@pytest.mark.asyncio
async def test_generate_quality_checked_case_retries_missing_skill_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing_evidence = _llm_case(1)
    missing_evidence["reference"].pop("web_verification")
    valid_case = _llm_case(1)
    valid_case["reference"]["web_verification"] = {"steps": [{"assert": "visible", "selector": "#app"}]}
    generated = [missing_evidence, valid_case]
    feedback: list[str] = []

    async def fake_generate_case(**kwargs):
        feedback.append(kwargs["initial_previous_error"])
        return generated.pop(0)

    async def fake_review_case_quality(**_kwargs):
        return {
            "case_id": "ppt_eval_001",
            "accepted": True,
            "critical_constraints_covered": True,
            "quality_score": 9,
            "difficulty_score": 4,
            "capability_alignment_score": 9,
            "verifiability_score": 9,
            "realism_score": 9,
            "main_issues": [],
            "revision_suggestions": [],
            "final_decision_reason": "valid",
        }

    generator = DatasetGenerator(
        DatasetGeneratorConfig(model_config_ref="models/dataset.yaml", min_cases=1),
        llm_runner=lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(generator, "_generate_case", fake_generate_case)
    monkeypatch.setattr(generator, "_review_case_quality", fake_review_case_quality)

    case, _, rejected = await generator._generate_quality_checked_case(
        task="Build a website",
        task_analysis={
            "task_type": "web_page",
            "required_case_evidence": ["web_verification"],
        },
        case_spec={"case_id_hint": "ppt_eval_001"},
        seen_user_messages=set(),
        case_index=1,
        case_count=1,
        output_path=tmp_path,
    )

    assert case["reference"]["web_verification"]["steps"]
    assert rejected[0]["stage"] == "schema_validation"
    assert "required by the active domain Judge Skill" in feedback[1]


@pytest.mark.asyncio
async def test_generate_quality_checked_case_allows_two_evidence_repairs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing_evidence = _llm_case(1)
    missing_evidence["reference"].pop("web_verification")
    second_missing_evidence = _llm_case(1)
    second_missing_evidence["reference"].pop("web_verification")
    valid_case = _llm_case(1)
    valid_case["reference"]["web_verification"] = {"steps": [{"assert": "visible", "selector": "#app"}]}
    generated = [missing_evidence, second_missing_evidence, valid_case]
    feedback: list[str] = []

    async def fake_generate_case(**kwargs):
        feedback.append(kwargs["initial_previous_error"])
        return generated.pop(0)

    async def fake_review_case_quality(**_kwargs):
        return {
            "case_id": "web_eval_001",
            "accepted": True,
            "critical_constraints_covered": True,
            "quality_score": 9,
            "difficulty_score": 4,
            "capability_alignment_score": 9,
            "verifiability_score": 9,
            "realism_score": 9,
            "main_issues": [],
            "revision_suggestions": [],
            "final_decision_reason": "valid",
        }

    generator = DatasetGenerator(
        DatasetGeneratorConfig(model_config_ref="models/dataset.yaml", min_cases=1),
        llm_runner=lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(generator, "_generate_case", fake_generate_case)
    monkeypatch.setattr(generator, "_review_case_quality", fake_review_case_quality)

    case, _, rejected = await generator._generate_quality_checked_case(
        task="Build a website",
        task_analysis={
            "task_type": "web_page",
            "required_case_evidence": ["web_verification"],
        },
        case_spec={"case_id_hint": "web_eval_001"},
        seen_user_messages=set(),
        case_index=1,
        case_count=1,
        output_path=tmp_path,
    )

    assert case["reference"]["web_verification"]["steps"]
    assert len(rejected) == 2
    assert all("required by the active domain Judge Skill" in item for item in feedback[1:])


@pytest.mark.asyncio
async def test_generate_uses_model_runner_and_writes_exact_case_count(tmp_path: Path) -> None:
    """DatasetGenerator asks the model for analysis and one compact case per call."""
    prompts: list[str] = []

    async def fake_llm(prompt: str, workspace: Path) -> str:
        prompts.append(prompt)
        assert workspace.is_dir()
        stage_response = _llm_task_analysis_stage_response(prompt)
        if stage_response is not None:
            return stage_response
        index = _case_index_from_prompt(prompt)
        return json.dumps(
            {
                "case": _llm_case(
                    index,
                    dimension=_assigned_dimension_from_prompt(prompt),
                    difficulty=_assigned_difficulty_from_prompt(prompt),
                    diagnostic_intent=_assigned_diagnostic_intent_from_prompt(prompt),
                    expected_optimization_target=(_assigned_expected_optimization_target_from_prompt(prompt)),
                    target_capabilities=_assigned_target_capabilities_from_prompt(prompt),
                    capability_combination=_assigned_capability_combination_from_prompt(prompt),
                    target_surfaces=_assigned_target_surfaces_from_prompt(prompt),
                )
            },
            ensure_ascii=False,
        )

    generator = DatasetGenerator(
        DatasetGeneratorConfig(
            model_config_ref="models/dataset.yaml",
            min_cases=6,
            coverage_dimensions=["storyline", "visual_quality"],
        ),
        llm_runner=fake_llm,
    )
    output_dir = tmp_path / "dataset_001"
    task = "Create an 8-page financing pitch deck for a new-energy storage company."

    artifact = await generator.generate(task, str(output_dir))

    assert len(prompts) == 26
    assert "Generate the capability graph" in prompts[0]
    assert "Target case count:\n6" in prompts[0]
    assert '"capability_graph"' in prompts[0]
    assert "verifier_design" in prompts[0]
    assert "Generate capability combinations" in prompts[1]
    assert "Target combination count:\n4" in prompts[1]
    assert "Generate exactly 4 capability combinations." in prompts[1]
    assert '"capability_combinations"' in prompts[1]
    assert "why_this_combination_is_hard" in prompts[1]
    assert "Keep each free-text field to one concise sentence, 25 words or fewer." in prompts[1]
    assert "Keep the full JSON response under 1200 words." in prompts[1]
    assert "Generate one test dimension" in prompts[2]
    assert "Top-level output contract" in prompts[2]
    assert "canonical field `test_dimension`" in prompts[2]
    assert "Generate exactly one dimension for case index 1." in prompts[2]
    assert "Case index:\n1 of 6" in prompts[2]
    assert "This dimension will seed one generated case" in prompts[2]
    assert "target_capabilities" in prompts[2]
    assert "user-facing deliverables" in prompts[2]
    assert "Keep the full JSON response under 220 words." in prompts[2]
    first_case_spec_prompt = next(prompt for prompt in prompts if "Generate one concrete case spec" in prompt)
    assert "user_visible_challenge" in first_case_spec_prompt
    assert "critical_user_constraints" in first_case_spec_prompt
    assert "verifier_contract" in first_case_spec_prompt
    case_generation_prompts = [
        prompt for prompt in prompts if "Generate exactly 1 synthetic evaluation example" in prompt
    ]
    quality_review_prompts = [prompt for prompt in prompts if "Review synthetic case quality" in prompt]
    case_spec_prompts = [prompt for prompt in prompts if "Generate one concrete case spec" in prompt]
    assert len(case_spec_prompts) == 6
    assert len(case_generation_prompts) == 6
    assert len(quality_review_prompts) == 6
    assert all("Generate exactly 1 synthetic evaluation example" in prompt for prompt in case_generation_prompts)
    first_case_prompt = case_generation_prompts[0]
    assert "Case spec for this case" in first_case_prompt
    assert "Critical user-visible constraints" in first_case_prompt
    assert "The user_message must explicitly include the case-specific challenge" in first_case_prompt
    assert "Assigned primary dimension" in first_case_prompt
    assert "Target capabilities for this case" in first_case_prompt
    assert "Capability combination for this case" in first_case_prompt
    assert "Verifier design hint for this case" in first_case_prompt
    assert "expected_steps" in first_case_prompt
    assert "distractors_or_traps" in first_case_prompt
    assert "success_criteria" in first_case_prompt
    assert "failure_criteria" in first_case_prompt
    assert "gold_answer_or_expected_artifact" in first_case_prompt
    assert "Assigned difficulty" in first_case_prompt
    assert "Assigned generic training intent" in first_case_prompt
    assert "The full JSON response must stay under 450 words" in first_case_prompt
    assert "Do not output training_signal, metadata, or input.critical_user_constraints" in (first_case_prompt)
    assert "Every target capability listed above must be observable" in first_case_prompt
    assert "Use the capability combination as the core execution challenge" in first_case_prompt
    assert "required_behaviors: 2 to 3 items" in first_case_prompt
    assert "judge_rubric.criteria: 2 to 3 items" in first_case_prompt
    assert "expected_steps: 2 to 3 items" in first_case_prompt
    assert "Keep user_message under 180 words" in first_case_prompt
    assert "gold_answer_or_expected_artifact must be a concise artifact contract" in first_case_prompt
    assert "Task dimensions:" not in first_case_prompt
    assert "quality_score" in quality_review_prompts[0]
    assert "capability_alignment_score" in quality_review_prompts[0]
    assert "critical_constraints_covered" in quality_review_prompts[0]
    assert "capability_alignment_score must account for every target capability" in (quality_review_prompts[0])
    assert "For multi-capability combinations" in quality_review_prompts[0]
    assert "among the included capabilities" in quality_review_prompts[0]
    assert "verifiability_score" in quality_review_prompts[0]
    case_prompts = "\n".join(case_generation_prompts)
    assert "static, text-readable evidence" in case_prompts
    assert "score the case by reading generated source artifacts" in case_prompts
    assert "File count, checklist presence, and self-check notes are baseline gates" in case_prompts
    assert "Escape ASCII double quotes" in case_prompts
    assert "challenge requirements" in case_generation_prompts[1].lower()
    assert task in prompts[0]
    assert "investor/CFO tradeoffs" not in "\n".join(prompts[3:])
    assert "generic deck writing" not in "\n".join(prompts[3:])

    assert artifact.dataset_id == "dataset_001"
    assert artifact.dataset_dir == str(output_dir.resolve())
    assert len(artifact.dataset_files) == 1

    dataset_path = Path(artifact.dataset_files[0])
    assert dataset_path.name == "synthetic_cases.json"
    data = json.loads(dataset_path.read_text(encoding="utf-8"))

    assert data["dataset_id"] == "dataset_001"
    assert data["source"] == "llm_synthetic_evaluation_dataset"
    assert data["task"] == task
    assert len(data["cases"]) == 6
    assert {case["case_id"] for case in data["cases"]} == {f"ppt_eval_{index:03d}" for index in range(1, 7)}
    assert [case["metadata"]["dimension"] for case in data["cases"]] == [
        "investor_storyline",
        "visual_decision",
        "risk_control",
        "artifact_contract",
        "deterministic_review",
        "runtime_tooling",
    ]
    assert [case["metadata"]["difficulty"] for case in data["cases"]] == [
        "medium",
        "medium",
        "hard",
        "hard",
        "hard",
        "hard",
    ]
    assert all(case["input"]["user_message"] for case in data["cases"])
    assert len({case["input"]["user_message"] for case in data["cases"]}) == 6
    assert all(
        f"constraint {index}" in case["input"]["user_message"] for index, case in enumerate(data["cases"], start=1)
    )
    assert all(case["reference"]["required_behaviors"] for case in data["cases"])
    assert all(case["reference"]["judge_rubric"] for case in data["cases"])
    assert all(case["reference"]["expected_steps"] for case in data["cases"])
    assert all(case["reference"]["distractors_or_traps"] for case in data["cases"])
    assert all(case["reference"]["success_criteria"] for case in data["cases"])
    assert all(case["reference"]["failure_criteria"] for case in data["cases"])
    assert all(case["reference"]["verifier"]["type"] for case in data["cases"])
    assert all(case["reference"]["gold_answer_or_expected_artifact"] for case in data["cases"])
    assert all(case["training_signal"]["expected_failure_modes"] for case in data["cases"])
    assert all(case["training_signal"]["capability_gap"] for case in data["cases"])
    assert all(case["training_signal"]["target_surfaces"] for case in data["cases"])
    assert all(case["training_signal"]["difficulty_rationale"] for case in data["cases"])
    assert [case["training_signal"]["diagnostic_intent"] for case in data["cases"]] == [
        "team_coordination_and_role_design",
        "output_contract_and_completion",
        "task_methodology_and_domain_skill",
        "deterministic_execution_or_validation",
        "quality_review_and_revision",
        "runtime_or_tooling_gap",
    ]
    assert [case["training_signal"]["expected_optimization_target"] for case in data["cases"]] == [
        "team_skill",
        "member_harness",
        "member_harness",
        "member_harness",
        "member_harness",
        "member_harness",
    ]
    assert all(case["metadata"]["provenance"]["method"] == "llm_synthetic_generation" for case in data["cases"])
    assert "mock_case_001" not in json.dumps(data, ensure_ascii=False)
    assert "requirement_understanding" not in json.dumps(data, ensure_ascii=False)

    task_analysis_path = output_dir / "_artifacts" / "task_analysis.json"
    task_analysis = json.loads(task_analysis_path.read_text(encoding="utf-8"))
    assert task_analysis["task_type"] == "presentation_generation"
    assert task_analysis["generation_strategy"] == "llm_synthetic_evaluation_dataset"
    assert task_analysis["coverage_dimensions"] == ["storyline", "visual_quality"]
    assert task_analysis["capability_graph"][0]["capability_name"] == "investor_storyline_planning"
    assert task_analysis["capability_combinations"][0]["combination_name"] == "storyline_visual_risk_package"
    assert task_analysis["test_dimensions"][0]["target_capabilities"] == ["investor_storyline_planning"]
    assert task_analysis["test_dimensions"][0]["capability_combination"] == ("storyline_visual_risk_package")
    assert task_analysis["case_specs"][0]["case_id_hint"] == "ppt_eval_001"
    assert task_analysis["case_specs"][0]["critical_user_constraints"] == ["constraint 1"]

    quality_report_path = output_dir / "_artifacts" / "case_quality_report.json"
    quality_report = json.loads(quality_report_path.read_text(encoding="utf-8"))
    assert len(quality_report["accepted_cases"]) == 6
    assert quality_report["accepted_cases"][0]["case_id"] == "ppt_eval_001"
    assert quality_report["accepted_cases"][0]["quality_score"] == 9


@pytest.mark.asyncio
async def test_generate_binds_case_spec_dimension_to_selected_dimension(
    tmp_path: Path,
) -> None:
    """Case-spec routing metadata should come from the selected dimension."""

    async def fake_llm(prompt: str, workspace: Path) -> str:
        assert workspace.is_dir()
        if "Generate one concrete case spec" in prompt:
            spec = _llm_case_spec(_case_index_from_prompt(prompt))
            spec["dimension"] = "seed_artifact_quality_gap"
            return json.dumps({"case_spec": spec}, ensure_ascii=False)

        stage_response = _llm_task_analysis_stage_response(prompt)
        if stage_response is not None:
            return stage_response

        index = _case_index_from_prompt(prompt)
        return json.dumps(
            {
                "case": _llm_case(
                    index,
                    dimension=_assigned_dimension_from_prompt(prompt),
                    difficulty=_assigned_difficulty_from_prompt(prompt),
                    diagnostic_intent=_assigned_diagnostic_intent_from_prompt(prompt),
                    expected_optimization_target=(_assigned_expected_optimization_target_from_prompt(prompt)),
                    target_capabilities=_assigned_target_capabilities_from_prompt(prompt),
                    capability_combination=_assigned_capability_combination_from_prompt(prompt),
                    target_surfaces=_assigned_target_surfaces_from_prompt(prompt),
                )
            },
            ensure_ascii=False,
        )

    generator = DatasetGenerator(
        DatasetGeneratorConfig(model_config_ref="models/dataset.yaml", min_cases=2),
        llm_runner=fake_llm,
    )
    output_dir = tmp_path / "dataset_001"

    await generator.generate("Create an 8-page financing pitch deck.", str(output_dir))

    task_analysis = json.loads((output_dir / "_artifacts" / "task_analysis.json").read_text(encoding="utf-8"))
    assert task_analysis["case_specs"][0]["dimension"]["name"] == "investor_storyline"
    assert task_analysis["case_specs"][1]["dimension"]["name"] == "visual_decision"


@pytest.mark.asyncio
async def test_generate_repairs_mojibake_task_before_prompting_and_persistence(tmp_path: Path) -> None:
    """Synthetic dataset boundaries should not persist mojibake task text."""
    readable_task = "\u8bf7\u4e3a\u65b0\u80fd\u6e90\u50a8\u80fd\u4f01\u4e1a\u5236\u4f5c\u7f51\u9875\u3002"
    mojibake_task = readable_task.encode("utf-8").decode("gbk", errors="ignore")
    prompts: list[str] = []

    async def fake_llm(prompt: str, workspace: Path) -> str:
        prompts.append(prompt)
        assert "\u8bf7\u4e3a\u65b0\u80fd\u6e90\u50a8\u80fd\u4f01\u4e1a\u5236\u4f5c\u7f51\u9875" in prompt
        assert "\u7487\u8702\u8d1f" not in prompt
        stage_response = _llm_task_analysis_stage_response(prompt)
        if stage_response is not None:
            return stage_response
        return json.dumps(
            {
                "case": _llm_case(
                    1,
                    user_message=f"{readable_task} constraint 1",
                    dimension=_assigned_dimension_from_prompt(prompt),
                    difficulty=_assigned_difficulty_from_prompt(prompt),
                )
            },
            ensure_ascii=False,
        )

    generator = DatasetGenerator(
        DatasetGeneratorConfig(model_config_ref="models/dataset.yaml", min_cases=1),
        llm_runner=fake_llm,
    )

    artifact = await generator.generate(mojibake_task, str(tmp_path / "dataset"))

    data = json.loads(Path(artifact.dataset_files[0]).read_text(encoding="utf-8"))
    serialized = json.dumps(data, ensure_ascii=False)
    assert "\u8bf7\u4e3a\u65b0\u80fd\u6e90\u50a8\u80fd\u4f01\u4e1a\u5236\u4f5c\u7f51\u9875" in serialized
    assert "\u7487\u8702\u8d1f" not in serialized


def test_context_store_repairs_mojibake_task_before_yaml_persistence(tmp_path: Path) -> None:
    """orchestrator_context.yaml must keep the run task readable."""
    readable_task = "\u8bf7\u4e3a\u65b0\u80fd\u6e90\u50a8\u80fd\u4f01\u4e1a\u5236\u4f5c\u7f51\u9875\u3002"
    mojibake_task = readable_task.encode("utf-8").decode("gbk", errors="ignore")
    store = OrchestratorContextStore(str(tmp_path / "orchestrator_context.yaml"))

    context = store.create(mojibake_task)
    store.save(context)

    raw_context = yaml.safe_load(Path(store.context_path).read_text(encoding="utf-8"))
    assert "\u8bf7\u4e3a\u65b0\u80fd\u6e90\u50a8\u80fd\u4f01\u4e1a\u5236\u4f5c\u7f51\u9875" in raw_context["task"]
    assert "\u7487\u8702\u8d1f" not in raw_context["task"]


def test_case_spec_constraint_validation_accepts_translated_task_with_technical_anchors() -> None:
    """Translated user requests should pass when required technical anchors remain visible."""
    case_spec = _llm_case_spec(1)
    case_spec["dimension"] = {
        "name": str(case_spec["dimension"]),
        "target_capabilities": list(case_spec["target_capabilities"]),
        "capability_combination": str(case_spec["capability_combination"]),
    }
    case_spec["critical_user_constraints"] = [
        (
            "Output exactly three files named index.html, styles.css, and game.js "
            "in the current working directory; no subdirectories, no extra files."
        ),
        (
            "index.html must reference styles.css via a relative <link> and "
            "game.js via a relative <script> tag; no CDN or absolute paths."
        ),
        ("The game must be fully playable offline via file:// protocol with zero failed network requests."),
    ]
    raw_case = _llm_case(
        1,
        user_message=(
            "\u8bf7\u5236\u4f5c\u4e00\u4e2a\u7f51\u9875\u7248\u5361\u724c\u5bf9"
            "\u6218\u5c0f\u6e38\u620f\u3002\n"
            "\u5173\u952e\u7ea6\u675f\uff1a\n"
            "1. \u5728\u5f53\u524d\u5de5\u4f5c\u76ee\u5f55\u8f93\u51fa\u6070"
            "\u597d\u4e09\u4e2a\u6587\u4ef6\uff0c\u547d\u540d\u4e3a index.html"
            "\u3001styles.css\u3001game.js\uff1b\u4e0d\u5f97\u4f7f\u7528"
            "\u5b50\u76ee\u5f55\uff0c\u4e0d\u5f97\u6709\u989d\u5916\u6587"
            "\u4ef6\u3002\n"
            "2. index.html \u5fc5\u987b\u901a\u8fc7\u76f8\u5bf9\u8def"
            "\u5f84 <link> \u5f15\u7528 styles.css\uff0c\u5e76\u901a"
            "\u8fc7\u76f8\u5bf9\u8def\u5f84 <script> \u6807\u7b7e\u5f15"
            "\u7528 game.js\uff1b\u4e0d\u5f97\u4f7f\u7528 CDN \u6216"
            "\u7edd\u5bf9\u8def\u5f84\u3002\n"
            "3. \u6e38\u620f\u5fc5\u987b\u80fd\u901a\u8fc7 file:// "
            "\u534f\u8bae\u5b8c\u5168\u79bb\u7ebf\u8fd0\u884c\uff0c\u96f6"
            "\u5931\u8d25\u7f51\u7edc\u8bf7\u6c42\u3002"
        ),
        expected_optimization_target=str(case_spec["expected_optimization_target"]),
        target_surfaces=list(case_spec["target_surfaces"]),
        dimension=str(case_spec["dimension"]["name"]),
        difficulty=str(case_spec["difficulty"]),
    )
    raw_case["input"]["critical_user_constraints"] = list(case_spec["critical_user_constraints"])

    _validate_raw_case_against_spec(
        raw_case,
        case_spec=case_spec,
        seen_user_messages=set(),
        case_index=1,
    )


def test_case_spec_constraint_validation_accepts_translated_natural_constraints() -> None:
    """Natural-language constraints may be translated instead of copied verbatim."""
    case_spec = _llm_case_spec(1)
    case_spec["dimension"] = {
        "name": str(case_spec["dimension"]),
        "target_capabilities": list(case_spec["target_capabilities"]),
        "capability_combination": str(case_spec["capability_combination"]),
    }
    case_spec["case_id_hint"] = "card_game_energy_cost_enforcement_divergence"
    case_spec["critical_user_constraints"] = [
        (
            "Each card must display an energy cost, and the game UI must include "
            "a visible rules section explaining the energy system to players"
        ),
        (
            "The AI opponent's move logic must reference the same energy "
            "constraint checks as the player's; the AI cannot play cards it "
            "cannot afford"
        ),
    ]
    raw_case = _llm_case(
        1,
        user_message=(
            "请制作一个网页版卡牌对战小游戏。\n"
            "关键约束：\n"
            "1. 每张卡牌必须显示能量费用，并且游戏 UI 必须包含一个可见"
            "的规则说明区域，向玩家解释能量系统。\n"
            "2. AI 对手的出牌逻辑必须引用与玩家相同的能量约束检查；"
            "AI 不能出它无法负担的卡牌。"
        ),
        expected_optimization_target=str(case_spec["expected_optimization_target"]),
        target_surfaces=list(case_spec["target_surfaces"]),
        dimension=str(case_spec["dimension"]["name"]),
        difficulty=str(case_spec["difficulty"]),
    )
    raw_case["case_id"] = "card_game_energy_cost_enforcement_divergence"
    raw_case["input"]["critical_user_constraints"] = list(case_spec["critical_user_constraints"])

    _validate_raw_case_against_spec(
        raw_case,
        case_spec=case_spec,
        seen_user_messages=set(),
        case_index=3,
    )


@pytest.mark.asyncio
async def test_generate_includes_known_failures_seed_in_planning_prompts(
    tmp_path: Path,
) -> None:
    """Targeted replay seeds should guide the next synthetic dataset generation."""
    seed_path = tmp_path / "targeted_dataset_seed.json"
    seed_path.write_text(
        json.dumps(
            {
                "recommended_synthetic_tasks": [
                    {
                        "source_case_id": "card_game_dom_sync",
                        "target_capabilities": ["dom_interaction_wiring"],
                        "capability_combination": "dom_state_sync",
                        "specific_trap_to_include": "selector mismatch",
                        "generation_reason": "Agent lacks a DOM/state contract.",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    prompts: list[str] = []

    async def fake_llm(prompt: str, workspace: Path) -> str:
        prompts.append(prompt)
        stage_response = _llm_task_analysis_stage_response(prompt)
        if stage_response is not None:
            return stage_response
        return json.dumps(
            {
                "case": _llm_case(
                    _case_index_from_prompt(prompt),
                    dimension=_assigned_dimension_from_prompt(prompt),
                    difficulty=_assigned_difficulty_from_prompt(prompt),
                    diagnostic_intent=_assigned_diagnostic_intent_from_prompt(prompt),
                    expected_optimization_target=(_assigned_expected_optimization_target_from_prompt(prompt)),
                    target_capabilities=_assigned_target_capabilities_from_prompt(prompt),
                    capability_combination=_assigned_capability_combination_from_prompt(prompt),
                    target_surfaces=_assigned_target_surfaces_from_prompt(prompt),
                )
            },
            ensure_ascii=False,
        )

    generator = DatasetGenerator(
        DatasetGeneratorConfig(
            model_config_ref="models/dataset.yaml",
            min_cases=1,
            known_failures_ref=str(seed_path),
        ),
        llm_runner=fake_llm,
    )

    await generator.generate("Create a browser card battle game.", str(tmp_path / "dataset"))

    assert "Known agent weaknesses" in prompts[0]
    assert "card_game_dom_sync" in prompts[0]
    assert "selector mismatch" in prompts[0]
    assert "Known agent weaknesses" in prompts[3]
    assert "dom_interaction_wiring" in prompts[3]


@pytest.mark.asyncio
async def test_generate_uses_known_failure_dataset_budget_for_case_count(
    tmp_path: Path,
) -> None:
    """Seed evaluation budget should decide targeted dataset size."""
    seed_path = tmp_path / "targeted_dataset_seed.json"
    seed_path.write_text(
        json.dumps(
            {
                "source": "seed_evaluation",
                "quality_gaps": [
                    {
                        "id": "interactive_quality_gap",
                        "data_needed_to_fix": "Generate focused interaction cases.",
                    }
                ],
                "dataset_budget": {
                    "total_cases": 3,
                    "case_groups": [
                        {
                            "source_gap": "interactive_quality_gap",
                            "case_count": 3,
                        }
                    ],
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    prompts: list[str] = []

    async def fake_llm(prompt: str, workspace: Path) -> str:
        prompts.append(prompt)
        stage_response = _llm_task_analysis_stage_response(prompt)
        if stage_response is not None:
            return stage_response
        return json.dumps(
            {
                "case": _llm_case(
                    _case_index_from_prompt(prompt),
                    dimension=_assigned_dimension_from_prompt(prompt),
                    difficulty=_assigned_difficulty_from_prompt(prompt),
                    diagnostic_intent=_assigned_diagnostic_intent_from_prompt(prompt),
                    expected_optimization_target=(_assigned_expected_optimization_target_from_prompt(prompt)),
                    target_capabilities=_assigned_target_capabilities_from_prompt(prompt),
                    capability_combination=_assigned_capability_combination_from_prompt(prompt),
                    target_surfaces=_assigned_target_surfaces_from_prompt(prompt),
                )
            },
            ensure_ascii=False,
        )

    generator = DatasetGenerator(
        DatasetGeneratorConfig(
            model_config_ref="models/dataset.yaml",
            min_cases=6,
            known_failures_ref=str(seed_path),
        ),
        llm_runner=fake_llm,
    )

    artifact = await generator.generate("Create an interactive browser game.", str(tmp_path / "dataset"))
    cases = json.loads(Path(artifact.dataset_files[0]).read_text(encoding="utf-8"))

    assert len(cases["cases"]) == 3
    assert "Target case count:\n3" in prompts[0]
    assert "interactive_quality_gap" in prompts[0]
    case_spec_prompts = [prompt for prompt in prompts if "Generate one concrete case spec" in prompt]
    assert len(case_spec_prompts) == 3


@pytest.mark.asyncio
async def test_generate_rewrites_case_when_quality_review_rejects(
    tmp_path: Path,
) -> None:
    """Quality review is an active gate, not a static all-accepted report."""
    case_attempts = 0
    quality_attempts = 0

    async def fake_llm(prompt: str, workspace: Path) -> str:
        nonlocal case_attempts, quality_attempts
        if "Review synthetic case quality" in prompt:
            quality_attempts += 1
            if quality_attempts == 1:
                return json.dumps(
                    {
                        "quality_review": {
                            "accepted": False,
                            "quality_score": 6,
                            "difficulty_score": 3,
                            "capability_alignment_score": 6,
                            "verifiability_score": 7,
                            "realism_score": 8,
                            "main_issues": ["user request hides the target constraint"],
                            "revision_suggestions": ["Put the DOM/state synchronization requirement in user_message."],
                            "final_decision_reason": "The task is not aligned enough.",
                        }
                    },
                    ensure_ascii=False,
                )
            return json.dumps(
                {"quality_review": _llm_quality_review(accepted=True)},
                ensure_ascii=False,
            )
        stage_response = _llm_task_analysis_stage_response(prompt)
        if stage_response is not None:
            return stage_response
        case_attempts += 1
        return json.dumps(
            {
                "case": _llm_case(
                    1,
                    dimension=_assigned_dimension_from_prompt(prompt),
                    difficulty=_assigned_difficulty_from_prompt(prompt),
                    diagnostic_intent=_assigned_diagnostic_intent_from_prompt(prompt),
                    expected_optimization_target=(_assigned_expected_optimization_target_from_prompt(prompt)),
                    target_capabilities=_assigned_target_capabilities_from_prompt(prompt),
                    capability_combination=_assigned_capability_combination_from_prompt(prompt),
                    target_surfaces=_assigned_target_surfaces_from_prompt(prompt),
                    user_message=(
                        "Create an investor pitch deck for a storage company; "
                        f"challenge 1; constraint 1; revision {case_attempts}."
                    ),
                )
            },
            ensure_ascii=False,
        )

    generator = DatasetGenerator(
        DatasetGeneratorConfig(model_config_ref="models/dataset.yaml", min_cases=1),
        llm_runner=fake_llm,
    )

    artifact = await generator.generate("Create a storage project investor deck.", str(tmp_path / "dataset"))

    data = json.loads(Path(artifact.dataset_files[0]).read_text(encoding="utf-8"))
    assert "revision 2" in data["cases"][0]["input"]["user_message"]
    report = json.loads((tmp_path / "dataset" / "_artifacts" / "case_quality_report.json").read_text(encoding="utf-8"))
    assert len(report["rejected_cases"]) == 1
    assert report["rejected_cases"][0]["case_id"] == "ppt_eval_001"
    assert report["rejected_cases"][0]["quality_score"] == 6
    assert len(report["accepted_cases"]) == 1
    assert case_attempts == 2
    assert quality_attempts == 2


@pytest.mark.asyncio
async def test_generate_retries_inconsistent_quality_review_without_rewriting_case(
    tmp_path: Path,
) -> None:
    """A self-contradictory quality review should be retried as review error."""
    case_attempts = 0
    quality_attempts = 0

    async def fake_llm(prompt: str, workspace: Path) -> str:
        nonlocal case_attempts, quality_attempts
        if "Review synthetic case quality" in prompt:
            quality_attempts += 1
            if quality_attempts == 1:
                return json.dumps(
                    {
                        "quality_review": {
                            "accepted": False,
                            "quality_score": 4,
                            "difficulty_score": 3,
                            "capability_alignment_score": 5,
                            "verifiability_score": 5,
                            "realism_score": 4,
                            "main_issues": [],
                            "revision_suggestions": [],
                            "final_decision_reason": (
                                "The case is well-constructed and highly aligned. No revisions needed."
                            ),
                        }
                    },
                    ensure_ascii=False,
                )
            assert "Previous case quality review response failed validation" in prompt
            return json.dumps(
                {"quality_review": _llm_quality_review(accepted=True)},
                ensure_ascii=False,
            )
        stage_response = _llm_task_analysis_stage_response(prompt)
        if stage_response is not None:
            return stage_response
        case_attempts += 1
        return json.dumps(
            {
                "case": _llm_case(
                    1,
                    dimension=_assigned_dimension_from_prompt(prompt),
                    difficulty=_assigned_difficulty_from_prompt(prompt),
                    diagnostic_intent=_assigned_diagnostic_intent_from_prompt(prompt),
                    expected_optimization_target=(_assigned_expected_optimization_target_from_prompt(prompt)),
                    target_capabilities=_assigned_target_capabilities_from_prompt(prompt),
                    capability_combination=_assigned_capability_combination_from_prompt(prompt),
                    target_surfaces=_assigned_target_surfaces_from_prompt(prompt),
                )
            },
            ensure_ascii=False,
        )

    generator = DatasetGenerator(
        DatasetGeneratorConfig(model_config_ref="models/dataset.yaml", min_cases=1),
        llm_runner=fake_llm,
    )

    await generator.generate("Create a browser card battle game.", str(tmp_path / "dataset"))

    assert case_attempts == 1
    assert quality_attempts == 2


@pytest.mark.asyncio
async def test_generate_retries_capability_graph_when_model_returns_invalid_json(
    tmp_path: Path,
) -> None:
    """Malformed stage output should be repaired by retrying with validation feedback."""
    prompts: list[str] = []
    graph_attempts = 0

    async def fake_llm(prompt: str, workspace: Path) -> str:
        nonlocal graph_attempts
        prompts.append(prompt)
        if "Generate the capability graph" in prompt:
            graph_attempts += 1
            if graph_attempts == 1:
                return '```json\n{"capability_graph": ['
        stage_response = _llm_task_analysis_stage_response(prompt)
        if stage_response is not None:
            return stage_response
        return json.dumps(
            {
                "case": _llm_case(
                    1,
                    dimension=_assigned_dimension_from_prompt(prompt),
                    difficulty=_assigned_difficulty_from_prompt(prompt),
                )
            },
            ensure_ascii=False,
        )

    generator = DatasetGenerator(
        DatasetGeneratorConfig(model_config_ref="models/dataset.yaml", min_cases=1),
        llm_runner=fake_llm,
    )

    await generator.generate("Create a browser card battle game.", str(tmp_path / "dataset"))

    assert graph_attempts == 2
    assert "Previous capability graph response failed validation" in prompts[1]
    assert "Return raw JSON without Markdown fences" in prompts[1]


@pytest.mark.asyncio
async def test_analyze_task_retries_dimension_with_unknown_capability(tmp_path: Path) -> None:
    """A dimension must be validated while its retry loop is still active."""
    dimension_prompts: list[str] = []

    async def fake_llm(prompt: str, workspace: Path) -> str:
        assert workspace.is_dir()
        if "Generate one test dimension" in prompt:
            dimension_prompts.append(prompt)
            if len(dimension_prompts) == 1:
                invalid = _llm_dimensions(1)[0]
                invalid["target_capabilities"] = ["invented_capability"]
                return json.dumps({"test_dimension": invalid}, ensure_ascii=False)
        stage_response = _llm_task_analysis_stage_response(prompt)
        assert stage_response is not None
        return stage_response

    generator = DatasetGenerator(
        DatasetGeneratorConfig(model_config_ref="models/dataset.yaml", min_cases=1),
        llm_runner=fake_llm,
    )

    output_path = tmp_path / "task_analysis.json"
    await generator.analyze_task("Create a browser card battle game.", str(output_path))

    assert len(dimension_prompts) == 2
    assert "Previous test dimension 1 response failed validation" in dimension_prompts[1]
    assert "contains unknown capabilities" in dimension_prompts[1]
    task_analysis = json.loads(output_path.read_text(encoding="utf-8"))
    assert task_analysis["test_dimensions"][0]["target_capabilities"] == ["investor_storyline_planning"]


@pytest.mark.asyncio
async def test_generate_retries_case_when_model_returns_invalid_json(tmp_path: Path) -> None:
    """A single malformed case response should be repaired by one retry, not abort the dataset."""
    prompts: list[str] = []
    case_attempts = 0

    async def fake_llm(prompt: str, workspace: Path) -> str:
        nonlocal case_attempts
        prompts.append(prompt)
        stage_response = _llm_task_analysis_stage_response(prompt)
        if stage_response is not None:
            return stage_response
        case_attempts += 1
        if case_attempts == 1:
            return '```json\n{"case": {"case_id": "broken", "input": '
        return json.dumps(
            {
                "case": _llm_case(
                    1,
                    dimension=_assigned_dimension_from_prompt(prompt),
                    difficulty=_assigned_difficulty_from_prompt(prompt),
                )
            },
            ensure_ascii=False,
        )

    generator = DatasetGenerator(
        DatasetGeneratorConfig(model_config_ref="models/dataset.yaml", min_cases=1),
        llm_runner=fake_llm,
    )

    await generator.generate("Create a storage project investor deck.", str(tmp_path / "dataset"))

    assert case_attempts == 2
    case_prompts = [prompt for prompt in prompts if "Generate exactly 1 synthetic evaluation example" in prompt]
    assert "Previous response failed validation" not in case_prompts[-1]
    assert "Escape ASCII double quotes" in case_prompts[-1]


@pytest.mark.asyncio
async def test_generate_rejects_unwrapped_case_response(tmp_path: Path) -> None:
    """Case generation must use the canonical top-level ``case`` contract."""

    async def fake_llm(prompt: str, workspace: Path) -> str:
        stage_response = _llm_task_analysis_stage_response(prompt)
        if stage_response is not None:
            return stage_response
        return json.dumps(
            _llm_case(
                1,
                dimension=_assigned_dimension_from_prompt(prompt),
                difficulty=_assigned_difficulty_from_prompt(prompt),
            ),
            ensure_ascii=False,
        )

    generator = DatasetGenerator(
        DatasetGeneratorConfig(model_config_ref="models/dataset.yaml", min_cases=1),
        llm_runner=fake_llm,
    )

    with pytest.raises(RuntimeError, match="case generation response must contain canonical field"):
        await generator.generate("Create a storage project investor deck.", str(tmp_path / "dataset"))


def test_validate_raw_case_requires_structured_critical_constraints() -> None:
    """Critical constraints from the case spec must be carried as structured input."""
    case = _llm_case(1)
    case_spec = _llm_case_spec(1)
    case_spec["dimension"] = {"name": case_spec["dimension"]}
    case_spec["critical_user_constraints"] = ["The executor must preserve this capability constraint before acting."]

    with pytest.raises(RuntimeError, match="input.critical_user_constraints must match case spec"):
        _validate_raw_case_against_spec(
            case,
            case_spec=case_spec,
            seen_user_messages=set(),
            case_index=1,
        )


@pytest.mark.asyncio
async def test_generate_persists_raw_case_output_when_json_never_repairs(tmp_path: Path) -> None:
    """The final parse failure should leave full raw model output for debugging."""

    async def fake_llm(prompt: str, workspace: Path) -> str:
        stage_response = _llm_task_analysis_stage_response(prompt)
        if stage_response is not None:
            return stage_response
        return '{"case": {"case_id": "broken" "input": {"user_message": "unfinished"}}}'

    generator = DatasetGenerator(
        DatasetGeneratorConfig(model_config_ref="models/dataset.yaml", min_cases=1),
        llm_runner=fake_llm,
    )
    output_dir = tmp_path / "dataset"

    with pytest.raises(RuntimeError) as exc_info:
        await generator.generate("Create a browser card battle game.", str(output_dir))

    raw_files = sorted((output_dir / "_artifacts").glob("failed_case_001_attempt_*.raw.txt"))
    assert raw_files
    assert raw_files[-1].read_text(encoding="utf-8") == (
        '{"case": {"case_id": "broken" "input": {"user_message": "unfinished"}}}'
    )
    error = str(exc_info.value)
    assert "raw_debug_path=" in error
    assert "line=" in error
    assert "column=" in error


def test_parse_model_json_repairs_eof_object_truncation_only() -> None:
    """A response cut off at EOF can be closed when its structure is unambiguous."""
    raw = '{"case":{"case_id":"card","input":{"user_message":"play"},"reference":{"gold_answer_or_expected_artifact":"files"}}'

    parsed = _parse_model_json(raw)

    assert parsed["case"]["case_id"] == "card"
    assert parsed["case"]["reference"]["gold_answer_or_expected_artifact"] == "files"


def test_parse_model_json_removes_only_surplus_closing_delimiters() -> None:
    """A complete object with an extra model-emitted closer is deterministic to repair."""
    parsed = _parse_model_json('{"case":{"case_id":"card"}}}')

    assert parsed == {"case": {"case_id": "card"}}

    with pytest.raises(RuntimeError, match="did not contain valid JSON"):
        _parse_model_json('{"case":{"case_id":"card"}}{"other":1}')


def test_parse_model_json_does_not_repair_non_truncation_syntax_errors() -> None:
    """The repair path must not mask ordinary malformed JSON."""
    raw = '{"case":{"case_id":"card" "input":{"user_message":"play"}}'

    with pytest.raises(RuntimeError, match="did not contain valid JSON"):
        _parse_model_json(raw)


@pytest.mark.asyncio
async def test_generate_retries_when_model_returns_mojibake() -> None:
    """Model-service mojibake should be retried before parsing or persisting."""
    attempts = 0

    async def fake_llm(prompt: str, workspace: Path) -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return "\u7487\u8702\u8d1f\u93c2\u62cc\u5165\u6d93\u5d87\u6b91\u6d93\u5d87\u6d49\u6ed5\u6939"
        stage_response = _llm_task_analysis_stage_response(prompt)
        if stage_response is not None:
            return stage_response
        return json.dumps(
            {
                "case": _llm_case(
                    1,
                    dimension=_assigned_dimension_from_prompt(prompt),
                    difficulty=_assigned_difficulty_from_prompt(prompt),
                )
            },
            ensure_ascii=False,
        )

    generator = DatasetGenerator(
        DatasetGeneratorConfig(model_config_ref="models/dataset.yaml", min_cases=1),
        llm_runner=fake_llm,
    )

    artifact = await generator.generate(
        "Create a storage project investor deck.", str(Path(".tmp_dataset_generator_retry_mojibake"))
    )

    data = json.loads(Path(artifact.dataset_files[0]).read_text(encoding="utf-8"))
    assert data["cases"][0]["case_id"] == "ppt_eval_001"
    assert attempts == 7


@pytest.mark.asyncio
async def test_generate_retries_when_model_call_times_out() -> None:
    """Transient model-service timeouts should be retried before failing the run."""
    attempts = 0

    async def fake_llm(prompt: str, workspace: Path) -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise asyncio.TimeoutError("model request timed out")
        stage_response = _llm_task_analysis_stage_response(prompt)
        if stage_response is not None:
            return stage_response
        return json.dumps(
            {
                "case": _llm_case(
                    1,
                    dimension=_assigned_dimension_from_prompt(prompt),
                    difficulty=_assigned_difficulty_from_prompt(prompt),
                )
            },
            ensure_ascii=False,
        )

    generator = DatasetGenerator(
        DatasetGeneratorConfig(model_config_ref="models/dataset.yaml", min_cases=1),
        llm_runner=fake_llm,
    )

    artifact = await generator.generate(
        "Create a storage project investor deck.", str(Path(".tmp_dataset_generator_retry_timeout"))
    )

    data = json.loads(Path(artifact.dataset_files[0]).read_text(encoding="utf-8"))
    assert data["cases"][0]["case_id"] == "ppt_eval_001"
    assert attempts == 7


@pytest.mark.asyncio
async def test_generate_retries_when_model_returns_incomplete_json() -> None:
    """Transport-truncated JSON should be retried before stage parsing."""
    attempts = 0
    truncated_dimensions_returned = False
    dimension_prompts: list[str] = []

    async def fake_llm(prompt: str, workspace: Path) -> str:
        nonlocal attempts, truncated_dimensions_returned
        attempts += 1
        if "Generate one test dimension" in prompt:
            dimension_prompts.append(prompt)
        if "Generate one test dimension" in prompt and not truncated_dimensions_returned:
            truncated_dimensions_returned = True
            return (
                "```json\n"
                "{\n"
                '  "test_dimensions": [\n'
                "    {\n"
                '      "name": "orphan_attack_phase_dead_code",\n'
                '      "description": "Attack phase is checked but never assigned.",\n'
                '      "capability_combination": "state'
            )
        stage_response = _llm_task_analysis_stage_response(prompt)
        if stage_response is not None:
            return stage_response
        return json.dumps(
            {
                "case": _llm_case(
                    1,
                    dimension=_assigned_dimension_from_prompt(prompt),
                    difficulty=_assigned_difficulty_from_prompt(prompt),
                )
            },
            ensure_ascii=False,
        )

    generator = DatasetGenerator(
        DatasetGeneratorConfig(
            model_config_ref="models/dataset.yaml",
            min_cases=1,
        ),
        llm_runner=fake_llm,
    )

    artifact = await generator.generate(
        "Create a browser card battle game.",
        str(Path(".tmp_dataset_generator_retry_incomplete_json")),
    )

    data = json.loads(Path(artifact.dataset_files[0]).read_text(encoding="utf-8"))
    assert data["cases"][0]["case_id"] == "ppt_eval_001"
    assert truncated_dimensions_returned is True
    assert len(dimension_prompts) == 2
    assert all("Previous test dimensions response failed validation" not in prompt for prompt in dimension_prompts)


@pytest.mark.asyncio
async def test_generate_requires_model_config_without_injected_runner(tmp_path: Path) -> None:
    """Production generation must fail loudly when no model is configured."""
    generator = DatasetGenerator(DatasetGeneratorConfig(min_cases=2))

    with pytest.raises(RuntimeError, match="dataset_generator.model_config_ref is required"):
        await generator.generate("Generate PPT evaluation examples.", str(tmp_path / "dataset"))


@pytest.mark.asyncio
async def test_generate_calls_model_directly_without_agent_wrapper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Production generation should parse direct model text, not DeepAgent result wrappers."""
    prompts: list[str] = []

    class FakeResponse:
        def __init__(self, content: str) -> None:
            self.content = content

    class FakeModel:
        async def invoke(self, **kwargs: object) -> FakeResponse:
            messages = kwargs["messages"]  # type: ignore[index]
            prompt = str(messages[-1]["content"])  # type: ignore[index]
            prompts.append(prompt)
            stage_response = _llm_task_analysis_stage_response(prompt)
            if stage_response is not None:
                return FakeResponse(stage_response)
            return FakeResponse(
                json.dumps(
                    {
                        "case": _llm_case(
                            _case_index_from_prompt(prompt),
                            dimension=_assigned_dimension_from_prompt(prompt),
                            difficulty=_assigned_difficulty_from_prompt(prompt),
                            diagnostic_intent=_assigned_diagnostic_intent_from_prompt(prompt),
                            expected_optimization_target=(_assigned_expected_optimization_target_from_prompt(prompt)),
                            target_surfaces=_assigned_target_surfaces_from_prompt(prompt),
                        )
                    },
                    ensure_ascii=False,
                )
            )

    monkeypatch.setattr(
        generator_module,
        "load_member_optimizer_model",
        lambda _: FakeModel(),
    )

    generator = DatasetGenerator(
        DatasetGeneratorConfig(model_config_ref="models/dataset.yaml", min_cases=1),
    )

    artifact = await generator.generate("Create a browser card battle game.", str(tmp_path / "dataset"))

    data = json.loads(Path(artifact.dataset_files[0]).read_text(encoding="utf-8"))
    assert data["cases"][0]["case_id"] == "ppt_eval_001"
    assert any("Generate exactly 1 synthetic evaluation example" in prompt for prompt in prompts)


@pytest.mark.asyncio
async def test_analyze_task_uses_model_generated_analysis(tmp_path: Path) -> None:
    """Task analysis is model-authored instead of inferred from local keyword rules."""
    prompts: list[str] = []

    async def fake_llm(prompt: str, workspace: Path) -> str:
        prompts.append(prompt)
        stage_response = _llm_task_analysis_stage_response(prompt)
        assert stage_response is not None
        return stage_response

    generator = DatasetGenerator(
        DatasetGeneratorConfig(model_config_ref="models/dataset.yaml", min_cases=1),
        llm_runner=fake_llm,
    )
    output_path = tmp_path / "analysis.json"

    await generator.analyze_task("Evaluate a code repair agent.", str(output_path))

    task_analysis = json.loads(output_path.read_text(encoding="utf-8"))
    assert [prompt.splitlines()[0] for prompt in prompts] == [
        "Generate the capability graph for a synthetic agent evaluation dataset.",
        "Generate capability combinations for a synthetic agent evaluation dataset.",
        "Generate one test dimension for a synthetic agent evaluation dataset.",
    ]
    assert task_analysis["task_type"] == "presentation_generation"
    assert task_analysis["test_dimensions"][0]["name"] == "investor_storyline"
    assert task_analysis["generator"] == "model"


@pytest.mark.asyncio
async def test_dimensions_stage_outputs_only_test_dimensions(
    tmp_path: Path,
) -> None:
    """Dimensions stage should not ask for unused task-level scoring fields."""
    prompts: list[str] = []

    async def fake_llm(prompt: str, workspace: Path) -> str:
        prompts.append(prompt)
        if "Generate one test dimension" in prompt:
            return json.dumps(
                {
                    "test_dimension": _llm_dimensions(_case_index_from_prompt(prompt))[
                        _case_index_from_prompt(prompt) - 1
                    ]
                },
                ensure_ascii=False,
            )
        stage_response = _llm_task_analysis_stage_response(prompt)
        assert stage_response is not None
        return stage_response

    generator = DatasetGenerator(
        DatasetGeneratorConfig(model_config_ref="models/dataset.yaml", min_cases=1),
        llm_runner=fake_llm,
    )
    output_path = tmp_path / "analysis.json"

    await generator.analyze_task("Create a browser card battle game.", str(output_path))

    task_analysis = json.loads(output_path.read_text(encoding="utf-8"))
    assert task_analysis["test_dimensions"]


@pytest.mark.asyncio
async def test_generated_dataset_can_be_loaded_in_batches(tmp_path: Path) -> None:
    """Generated JSON dataset remains compatible with DataLoader batching."""

    async def fake_llm(prompt: str, workspace: Path) -> str:
        stage_response = _llm_task_analysis_stage_response(prompt)
        if stage_response is not None:
            return stage_response
        return json.dumps(
            {
                "case": _llm_case(
                    _case_index_from_prompt(prompt),
                    dimension=_assigned_dimension_from_prompt(prompt),
                    difficulty=_assigned_difficulty_from_prompt(prompt),
                    diagnostic_intent=_assigned_diagnostic_intent_from_prompt(prompt),
                    expected_optimization_target=(_assigned_expected_optimization_target_from_prompt(prompt)),
                    target_surfaces=_assigned_target_surfaces_from_prompt(prompt),
                )
            },
            ensure_ascii=False,
        )

    generator = DatasetGenerator(
        DatasetGeneratorConfig(model_config_ref="models/dataset.yaml", min_cases=6),
        llm_runner=fake_llm,
    )
    output_dir = tmp_path / "dataset_002"

    await generator.generate("Create a storage project investor deck.", str(output_dir))

    loader = DataLoader(DataLoaderConfig(batch_size=2))
    batches = list(loader.load(str(output_dir)))

    assert [len(batch) for batch in batches] == [2, 2, 2]
    assert batches[0][0]["case_id"] == "ppt_eval_001"
    assert batches[0][0]["case_path"].endswith("synthetic_cases.json")
    assert batches[0][0]["metadata"]["judgeable"] is True


@pytest.mark.asyncio
async def test_generate_records_direct_model_usage_by_dataset_step(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Direct dataset model calls should be visible in the run usage ledger."""

    class FakeDatasetModel:
        async def invoke(self, messages, **kwargs):  # type: ignore[no-untyped-def]
            prompt = messages[-1]["content"]
            content = _llm_task_analysis_stage_response(prompt)
            if content is None:
                index = _case_index_from_prompt(prompt)
                content = json.dumps(
                    {
                        "case": _llm_case(
                            index,
                            dimension=_assigned_dimension_from_prompt(prompt),
                            difficulty=_assigned_difficulty_from_prompt(prompt),
                            diagnostic_intent=_assigned_diagnostic_intent_from_prompt(prompt),
                            expected_optimization_target=(_assigned_expected_optimization_target_from_prompt(prompt)),
                            target_capabilities=_assigned_target_capabilities_from_prompt(prompt),
                            capability_combination=_assigned_capability_combination_from_prompt(prompt),
                            target_surfaces=_assigned_target_surfaces_from_prompt(prompt),
                        )
                    },
                    ensure_ascii=False,
                )
            return SimpleNamespace(
                content=content,
                usage_metadata=SimpleNamespace(
                    model_name="dataset-model",
                    input_tokens=10,
                    output_tokens=2,
                    total_tokens=12,
                    cache_tokens=3,
                ),
            )

    monkeypatch.setattr(
        generator_module,
        "load_member_optimizer_model",
        lambda _model_config_ref: FakeDatasetModel(),
    )
    generator = DatasetGenerator(
        DatasetGeneratorConfig(
            model_config_ref="models/dataset.yaml",
            min_cases=2,
            quality_review_enabled=True,
        )
    )
    usage_path = tmp_path / "llm_usage.jsonl"

    with llm_usage_ledger(usage_path, run_id="dataset-run"):
        with llm_usage_scope(stage="dataset_generation", operation="generate_dataset"):
            await generator.generate(
                "Create a browser card battle game.",
                str(tmp_path / "dataset_001"),
            )

    summary = summarize_llm_usage_file(usage_path, run_id="dataset-run")

    assert summary["by_stage"]["dataset_generation"]["calls"] == 10
    assert summary["by_stage"]["dataset_generation"]["total_tokens"] == 120
    assert summary["by_stage"]["dataset_generation"]["cache_tokens"] == 30
    assert summary["by_operation"]["capability_graph"]["calls"] == 1
    assert summary["by_operation"]["capability_combinations"]["calls"] == 1
    assert summary["by_operation"]["test_dimension"]["calls"] == 2
    assert summary["by_operation"]["case_spec"]["calls"] == 2
    assert summary["by_operation"]["case_generation"]["calls"] == 2
    assert summary["by_operation"]["case_quality_review"]["calls"] == 2


def _llm_task_analysis_stage_response(prompt: str) -> str | None:
    if "Review synthetic case quality" in prompt:
        return json.dumps(
            {"quality_review": _llm_quality_review(accepted=True)},
            ensure_ascii=False,
        )
    if "Generate the capability graph" in prompt:
        return json.dumps(
            {
                "task_type": "presentation_generation",
                "scenario_summary": ("Improve a presentation agent for investor-ready storage financing decks."),
                "capability_graph": _llm_capability_graph(),
            },
            ensure_ascii=False,
        )
    if "Generate capability combinations" in prompt:
        return json.dumps(
            {"capability_combinations": _llm_capability_combinations()},
            ensure_ascii=False,
        )
    if "Generate one test dimension" in prompt:
        return json.dumps(
            {
                "test_dimension": _llm_dimensions(_case_index_from_prompt(prompt))[_case_index_from_prompt(prompt) - 1],
            },
            ensure_ascii=False,
        )
    if "Generate one concrete case spec" in prompt:
        return json.dumps(
            {"case_spec": _llm_case_spec(_case_index_from_prompt(prompt))},
            ensure_ascii=False,
        )
    if "Generate concrete case specs" in prompt:
        return json.dumps(
            {"case_specs": _llm_case_specs(_target_case_count_from_prompt(prompt))},
            ensure_ascii=False,
        )
    return None


def _llm_quality_review(*, accepted: bool) -> dict[str, object]:
    return {
        "accepted": accepted,
        "critical_constraints_covered": accepted,
        "quality_score": 9 if accepted else 6,
        "difficulty_score": 4,
        "capability_alignment_score": 9 if accepted else 6,
        "verifiability_score": 9,
        "realism_score": 8,
        "main_issues": [] if accepted else ["not aligned"],
        "revision_suggestions": [] if accepted else ["tighten user-visible constraints"],
        "final_decision_reason": "accepted" if accepted else "rejected",
    }


def test_quality_review_passes_easy_case_with_matching_difficulty_score() -> None:
    """Easy curriculum cases should not be rejected by a medium global threshold."""
    review = _llm_quality_review(accepted=True)
    review["difficulty_score"] = 2
    case = {"metadata": {"difficulty": "easy"}}

    assert _quality_review_passes(
        review,
        config=DatasetGeneratorConfig(),
        case=case,
    )


def test_quality_review_rejects_hard_case_with_easy_difficulty_score() -> None:
    """Hard cases still need a hard-level difficulty review score."""
    review = _llm_quality_review(accepted=True)
    review["difficulty_score"] = 2
    case = {"metadata": {"difficulty": "hard"}}

    assert not _quality_review_passes(
        review,
        config=DatasetGeneratorConfig(),
        case=case,
    )


def test_quality_review_rejects_when_critical_constraints_are_not_covered() -> None:
    review = _llm_quality_review(accepted=True)
    review["critical_constraints_covered"] = False
    case = {"metadata": {"difficulty": "medium"}}

    assert not _quality_review_passes(
        review,
        config=DatasetGeneratorConfig(),
        case=case,
    )


def test_quality_review_validation_rejects_inconsistent_constraint_coverage() -> None:
    review = _llm_quality_review(accepted=True)
    review["critical_constraints_covered"] = False
    case = _llm_case(1)
    case_spec = _llm_case_spec(1)

    with pytest.raises(RuntimeError, match="critical_constraints_covered is false"):
        _normalize_case_quality_review(
            review,
            case=case,
            case_spec=case_spec,
        )


def test_case_specs_expand_target_capabilities_from_capability_combination() -> None:
    task_analysis = _llm_task_analysis()
    raw_specs = _llm_case_specs()

    normalized = _normalize_case_specs(
        raw_specs,
        task_analysis=task_analysis,
        target_case_count=6,
    )

    assert normalized[0]["target_capabilities"] == [
        "investor_storyline_planning",
        "visual_tradeoff_design",
        "risk_narrative_grounding",
    ]
    assert normalized[3]["target_capabilities"] == [
        "deliverable_contract_execution",
        "verifier_driven_revision",
    ]


def test_case_specs_allow_same_challenge_with_distinct_user_constraints() -> None:
    task_analysis = _llm_task_analysis(target_case_count=2)
    raw_specs = _llm_case_specs(target_case_count=2)
    raw_specs[1]["user_visible_challenge"] = raw_specs[0]["user_visible_challenge"]
    raw_specs[1]["critical_user_constraints"] = ["different executor-visible constraint"]

    normalized = _normalize_case_specs(
        raw_specs,
        task_analysis=task_analysis,
        target_case_count=2,
    )

    assert normalized[0]["user_visible_challenge"] == normalized[1]["user_visible_challenge"]
    assert normalized[0]["critical_user_constraints"] != normalized[1]["critical_user_constraints"]


def test_case_specs_deduplicate_model_generated_id_collisions() -> None:
    task_analysis = _llm_task_analysis(target_case_count=2)
    raw_specs = _llm_case_specs(target_case_count=2)
    raw_specs[1]["case_id_hint"] = raw_specs[0]["case_id_hint"]

    normalized = _normalize_case_specs(
        raw_specs,
        task_analysis=task_analysis,
        target_case_count=2,
    )

    assert normalized[0]["case_id_hint"] != normalized[1]["case_id_hint"]
    assert normalized[1]["case_id_hint"].startswith(normalized[0]["case_id_hint"])


def test_case_specs_reject_same_challenge_with_same_user_constraints() -> None:
    task_analysis = _llm_task_analysis(target_case_count=2)
    raw_specs = _llm_case_specs(target_case_count=2)
    raw_specs[1]["user_visible_challenge"] = raw_specs[0]["user_visible_challenge"]
    raw_specs[1]["critical_user_constraints"] = raw_specs[0]["critical_user_constraints"]

    with pytest.raises(RuntimeError, match="duplicates another case"):
        _normalize_case_specs(
            raw_specs,
            task_analysis=task_analysis,
            target_case_count=2,
        )


def test_dataset_generator_no_longer_constructs_rail_targets() -> None:
    assert all("rail" not in intent.get("target_surfaces", []) for intent in GENERIC_TRAINING_INTENT_TAXONOMY)

    prompt = _build_case_prompt(
        task="Create a browser game.",
        task_analysis=_llm_task_analysis(),
        case_spec=_normalize_case_specs(
            _llm_case_specs(),
            task_analysis=_llm_task_analysis(),
            target_case_count=6,
        )[1],
        case_index=2,
        case_count=6,
    )

    assert '"target_surfaces": ["skill|tool|prompt_section|rail"]' not in prompt
    assert "target_surfaces may contain only skill, tool, prompt_section, rail" not in prompt
    assert "target_surfaces may contain only skill, tool, prompt_section" in prompt


def test_case_prompt_makes_capability_combination_drive_behavior_scoring() -> None:
    """Dataset cases should not get high scores from file-contract checks alone."""
    task_analysis = _llm_task_analysis()
    case_spec = _normalize_case_specs(
        _llm_case_specs(),
        task_analysis=task_analysis,
        target_case_count=6,
    )[0]

    prompt = _build_case_prompt(
        task="Create a multi-step work product.",
        task_analysis=task_analysis,
        case_spec=case_spec,
        case_index=1,
        case_count=6,
    )

    assert "Required behaviors must evaluate the target capability combination" in prompt
    assert "At least one required behavior must score the interaction" in prompt
    assert "Capability behaviors must carry most of the total behavior weight" in prompt
    assert "must not exceed 20% of the total behavior weight" in prompt
    assert "Do not let a case receive a high score just because files exist" in prompt


def test_case_spec_preserves_seed_gap_context_for_concrete_case_prompt() -> None:
    """Known seed gaps should remain traceable through case spec generation."""
    task_analysis = _llm_task_analysis(target_case_count=1)
    raw_specs = _llm_case_specs(target_case_count=1)
    raw_specs[0]["source_gap"] = "seed_artifact_quality_gap"
    raw_specs[0]["observed_gap"] = {
        "dimension": "end_to_end_artifact_quality",
        "quality_axes": [
            {"name": "functional_effectiveness"},
            {"name": "user_visible_output_quality"},
        ],
        "evidence": {
            "harvested_artifacts": ["index.html", "styles.css", "game.js"],
        },
    }

    normalized = _normalize_case_specs(
        raw_specs,
        task_analysis=task_analysis,
        target_case_count=1,
    )

    assert normalized[0]["source_gap"] == "seed_artifact_quality_gap"
    assert normalized[0]["observed_gap"]["dimension"] == "end_to_end_artifact_quality"
    prompt = _build_case_prompt(
        task="Create a browser game.",
        task_analysis=task_analysis,
        case_spec=normalized[0],
        case_index=1,
        case_count=1,
    )
    assert "seed_artifact_quality_gap" in prompt
    assert "end_to_end_artifact_quality" in prompt
    assert "functional_effectiveness" in prompt
    assert "user_visible_output_quality" in prompt


def test_case_spec_prompt_requires_binding_to_known_seed_gap() -> None:
    prompt = _build_single_case_spec_prompt(
        task="Create a browser game.",
        target_case_count=3,
        case_index=1,
        task_analysis=_llm_task_analysis(target_case_count=3),
        known_failures_text=json.dumps(
            {
                "quality_gaps": [
                    {
                        "id": "seed_artifact_quality_gap",
                        "dimension": "end_to_end_artifact_quality",
                    }
                ],
                "recommended_synthetic_tasks": [{"task_pattern": "observed_deliverable_quality_gap"}],
            },
            ensure_ascii=False,
        ),
    )

    assert '"source_gap"' in prompt
    assert "Bind the case spec to one source_gap" in prompt
    assert "observed_gap" in prompt
    assert "functional effectiveness" in prompt
    assert "user-visible output quality" in prompt


def test_quality_review_prompt_rejects_artifact_only_scoring() -> None:
    """Quality review should reject cases whose rubric is only a static file gate."""
    task_analysis = _llm_task_analysis()
    case_spec = _normalize_case_specs(
        _llm_case_specs(),
        task_analysis=task_analysis,
        target_case_count=6,
    )[0]

    prompt = _build_case_quality_review_prompt(
        task="Create a multi-step work product.",
        task_analysis=task_analysis,
        case_spec=case_spec,
        case=_llm_case(1),
    )

    assert "Reject cases whose score can be high from artifact or file existence alone" in prompt
    assert "required_behaviors must include capability-combination behaviors" in prompt
    assert "baseline gates must not dominate the pass score" in prompt
    assert "Execute web_verification mentally from a fresh initial page" in prompt
    assert "`exists` means the selector resolves to" in prompt
    assert "Only `visible` and" in prompt
    assert "A short fixed click sequence must not claim to validate" in prompt
    assert "Judge evidence coverage across required_behaviors" in prompt
    assert "Do not reject a case merely" in prompt
    assert "Never recommend adding an unsupported long gameplay sequence" in prompt
    assert "automation contracts even when the rest of the case is realistic" in prompt
    assert "Treat verifier.test_cases_or_rules as judge guidance" in prompt
    assert "Two generic card clicks do not prove a 30-HP" in prompt
    assert "waiting alone does not prove an AI turn" in prompt


def test_case_repair_prompt_preserves_required_machine_evidence() -> None:
    prompt = _build_case_prompt(
        task="Build an interactive website.",
        task_analysis={"required_case_evidence": ["web_verification"]},
        case_spec={
            "case_id_hint": "web_eval_001",
            "dimension": {"name": "runtime", "target_capabilities": ["runtime"]},
            "difficulty": "medium",
            "challenge_focus": "Verify an immediate interaction.",
            "training_intent": "deterministic_execution_or_validation",
        },
        case_index=1,
        case_count=1,
        previous_error="Quality review rejected unreachable web verification steps.",
    )

    assert "Preserve every mandatory machine-evidence field" in prompt
    assert "never fix it by deleting the field" in prompt
    assert 'Machine evidence required by active domain Judge Skills:\n["web_verification"]' in prompt


def test_target_surfaces_rejects_rail() -> None:
    with pytest.raises(RuntimeError, match="unsupported values"):
        _normalize_target_surfaces(["skill", "rail"], case_index=1)


def _llm_task_analysis(target_case_count: int = 6) -> dict[str, object]:
    return {
        "task_type": "presentation_generation",
        "generator": "model",
        "scenario_summary": "Improve a presentation agent for investor-ready storage financing decks.",
        "capability_graph": _llm_capability_graph(),
        "capability_combinations": _llm_capability_combinations(),
        "test_dimensions": _llm_dimensions(target_case_count),
        "case_specs": _llm_case_specs(target_case_count),
    }


def _llm_case_specs(target_case_count: int = 6) -> list[dict[str, object]]:
    return [_llm_case_spec(index) for index in range(1, target_case_count + 1)]


def _llm_case_spec(index: int) -> dict[str, object]:
    dimension = _llm_dimensions(index)[index - 1]
    difficulty = "medium" if index <= 2 else "hard"
    intent = [
        "team_coordination_and_role_design",
        "output_contract_and_completion",
        "task_methodology_and_domain_skill",
        "deterministic_execution_or_validation",
        "quality_review_and_revision",
        "runtime_or_tooling_gap",
    ][(index - 1) % 6]
    target = "team_skill" if index == 1 else "member_harness"
    return {
        "case_id_hint": f"ppt_eval_{index:03d}",
        "dimension": dimension["name"],
        "difficulty": difficulty,
        "training_intent": intent,
        "expected_optimization_target": target,
        "target_surfaces": ["skill"] if index == 1 else ["skill", "prompt_section"],
        "target_capabilities": dimension["target_capabilities"],
        "capability_combination": dimension["capability_combination"],
        "user_visible_challenge": f"challenge {index}",
        "critical_user_constraints": [f"constraint {index}"],
        "expected_failure_modes": [f"failure {index}"],
        "verifier_contract": [f"verify {index}"],
        "challenge_focus": f"focus {index}",
    }


def _llm_dimensions(target_case_count: int = 6) -> list[dict[str, object]]:
    dimensions: list[dict[str, object]] = [
        {
            "name": "investor_storyline",
            "description": "Build a persuasive finance narrative for industrial investors.",
            "difficulty": "medium",
            "target_capabilities": ["investor_storyline_planning"],
            "capability_combination": "storyline_visual_risk_package",
            "observable_behavior": "Deck narrative connects market, product, risk, and ROI.",
            "common_failure_modes": ["generic storyline without investor logic"],
            "difficulty_factors": ["audience fit", "financial arc"],
            "verifier_design": "Judge slide-level narrative continuity.",
        },
        {
            "name": "visual_decision",
            "description": "Choose chart and page structures that make financial tradeoffs scannable.",
            "difficulty": "hard",
            "target_capabilities": ["visual_tradeoff_design"],
            "capability_combination": "storyline_visual_risk_package",
            "observable_behavior": "Deck uses visual structure to compare tradeoffs.",
            "common_failure_modes": ["wall-of-text slides"],
            "difficulty_factors": ["visual hierarchy", "metric density"],
            "verifier_design": "Judge artifact structure and visual decision rationale.",
        },
        {
            "name": "risk_control",
            "description": "Expose risks and connect mitigations to investor concerns.",
            "difficulty": "medium",
            "target_capabilities": ["risk_narrative_grounding"],
            "capability_combination": "storyline_visual_risk_package",
            "observable_behavior": "Risks and mitigations are explicit and investor-relevant.",
            "common_failure_modes": ["risk section is vague or disconnected"],
            "difficulty_factors": ["risk specificity", "mitigation grounding"],
            "verifier_design": "Judge risk-mitigation mapping.",
        },
        {
            "name": "artifact_contract",
            "description": "Create all required deliverables with inspectable structure.",
            "difficulty": "hard",
            "target_capabilities": ["deliverable_contract_execution"],
            "capability_combination": "artifact_contract_with_review",
            "observable_behavior": "All requested files and sections are present.",
            "common_failure_modes": ["missing deliverable file"],
            "difficulty_factors": ["multi-file output", "contract checking"],
            "verifier_design": "Check required artifact paths and sections.",
        },
        {
            "name": "deterministic_review",
            "description": "Revise artifacts against explicit scoring criteria.",
            "difficulty": "hard",
            "target_capabilities": ["verifier_driven_revision"],
            "capability_combination": "artifact_contract_with_review",
            "observable_behavior": "Artifacts reflect criteria-driven revision.",
            "common_failure_modes": ["no revision after self-review"],
            "difficulty_factors": ["criteria mapping", "revision traceability"],
            "verifier_design": "Judge evidence of review-driven improvements.",
        },
        {
            "name": "runtime_tooling",
            "description": "Use deterministic checks when artifact validity matters.",
            "difficulty": "hard",
            "target_capabilities": ["artifact_runtime_validation"],
            "capability_combination": "runtime_validation_loop",
            "observable_behavior": "Artifacts are checked using deterministic validation where relevant.",
            "common_failure_modes": ["claims validity without checking artifacts"],
            "difficulty_factors": ["tool choice", "error recovery"],
            "verifier_design": "Inspect validation outputs and artifact consistency.",
        },
    ]
    return dimensions[:target_case_count]


def _llm_capability_graph() -> list[dict[str, object]]:
    return [
        _capability(
            "investor_storyline_planning",
            "Build a persuasive finance narrative for industrial investors.",
        ),
        _capability(
            "visual_tradeoff_design",
            "Choose chart and page structures that make financial tradeoffs scannable.",
        ),
        _capability(
            "risk_narrative_grounding",
            "Expose risks and connect mitigations to investor concerns.",
        ),
        _capability(
            "deliverable_contract_execution",
            "Produce the requested files and artifact shape.",
        ),
        _capability(
            "verifier_driven_revision",
            "Review generated artifacts against explicit scoring criteria.",
        ),
        _capability(
            "artifact_runtime_validation",
            "Use deterministic artifact checks where prose review is insufficient.",
        ),
    ]


def _llm_capability_combinations() -> list[dict[str, object]]:
    return [
        _combination(
            "storyline_visual_risk_package",
            [
                "investor_storyline_planning",
                "visual_tradeoff_design",
                "risk_narrative_grounding",
            ],
        ),
        _combination(
            "artifact_contract_with_review",
            ["deliverable_contract_execution", "verifier_driven_revision"],
        ),
        _combination(
            "runtime_validation_loop",
            ["artifact_runtime_validation", "verifier_driven_revision"],
        ),
    ]


def _capability(name: str, description: str) -> dict[str, object]:
    return {
        "capability_name": name,
        "description": description,
        "observable_behavior": f"Observable behavior for {name}.",
        "common_failure_modes": [f"Common failure for {name}."],
        "prerequisite_capabilities": [],
        "difficulty_factors": [f"Difficulty factor for {name}."],
        "data_generation_strategy": f"Generate cases that expose {name}.",
        "verifier_design": f"Verify {name} from artifacts and judge evidence.",
    }


def _combination(name: str, included_capabilities: list[str]) -> dict[str, object]:
    return {
        "combination_name": name,
        "included_capabilities": included_capabilities,
        "why_this_combination_is_hard": f"{name} combines multiple failure-prone steps.",
        "typical_agent_failure": f"Agent fails to coordinate {name}.",
        "target_task_pattern": f"Task pattern for {name}.",
        "minimum_required_context": f"Minimum context for {name}.",
        "expected_tool_usage": ["artifact_inspection"],
        "evaluation_method": "llm_as_judge_with_artifact_checks",
        "difficulty_level": 4,
    }


def _llm_case(
    index: int,
    *,
    dimension: str = "investor_storyline",
    difficulty: str = "medium",
    diagnostic_intent: str = "task_methodology_and_domain_skill",
    expected_optimization_target: str | None = None,
    target_capabilities: list[str] | None = None,
    capability_combination: str = "storyline_visual_risk_package",
    target_surfaces: list[str] | None = None,
    user_message: str | None = None,
) -> dict[str, object]:
    expected_target = (
        expected_optimization_target
        if expected_optimization_target is not None
        else ("team_skill" if index == 1 else "member_harness")
    )
    surfaces = target_surfaces or (["skill"] if expected_target == "team_skill" else ["skill", "tool"])
    capabilities = target_capabilities or ["investor_storyline_planning"]
    return {
        "case_id": f"ppt_eval_{index:03d}",
        "input": {
            "user_message": user_message
            or (f"Create an investor pitch deck for a storage company; challenge {index}; constraint {index}."),
            "critical_user_constraints": [f"constraint {index}"],
        },
        "reference": {
            "required_behaviors": [
                {
                    "id": "storyline",
                    "description": "Produces a slide-level investment narrative.",
                    "weight": 1.0,
                }
            ],
            "forbidden_behaviors": [
                {
                    "id": "generic_report",
                    "description": "Writes a generic report instead of deck artifacts.",
                    "penalty": 0.5,
                }
            ],
            "judge_rubric": {
                "pass_threshold": 0.8,
                "criteria": [
                    "Matches audience and goal",
                    "Contains slide-level artifacts",
                    "Includes verification or revision notes",
                ],
            },
            "expected_steps": [
                "Plan the artifact around the assigned capability target.",
                "Produce inspectable deliverables that expose the target behavior.",
                "Check the deliverables against the case criteria.",
            ],
            "distractors_or_traps": [
                f"Trap {index}: shallow output can look plausible without {capability_combination}."
            ],
            "success_criteria": [
                "The output satisfies the assigned target capability.",
                "The artifact can be judged from generated files.",
            ],
            "failure_criteria": [
                "The output ignores the assigned capability gap.",
                "The artifact is too generic to evaluate.",
            ],
            "verifier": {
                "type": "llm_as_judge",
                "check_method": "Inspect generated artifacts against required behaviors.",
                "test_cases_or_rules": [
                    "Score each required behavior independently.",
                    "Apply forbidden behavior penalties.",
                ],
            },
            "web_verification": {
                "steps": [{"assert": "visible", "selector": "#app"}],
            },
            "gold_answer_or_expected_artifact": (
                "A complete artifact package that demonstrates the assigned capability "
                f"combination: {capability_combination}."
            ),
        },
        "metadata": {
            "dimension": dimension,
            "difficulty": difficulty,
        },
        "training_signal": {
            "diagnostic_intent": diagnostic_intent,
            "expected_optimization_target": expected_target,
            "target_capabilities": capabilities,
            "capability_combination": capability_combination,
            "expected_failure_modes": [
                f"failure mode {index} for {diagnostic_intent}",
            ],
            "capability_gap": f"capability gap {index}",
            "target_surfaces": surfaces,
            "difficulty_rationale": f"{difficulty} case stresses {diagnostic_intent}",
        },
    }


def _case_index_from_prompt(prompt: str) -> int:
    match = re.search(r"Case index:\s*(\d+)\s+of", prompt)
    assert match is not None
    return int(match.group(1))


def _target_case_count_from_prompt(prompt: str) -> int:
    match = re.search(r"Target case count:\s*(\d+)", prompt)
    assert match is not None
    return int(match.group(1))


def _assigned_dimension_from_prompt(prompt: str) -> str:
    match = re.search(r'"name":\s*"([^"]+)"', prompt)
    assert match is not None
    return match.group(1)


def _assigned_difficulty_from_prompt(prompt: str) -> str:
    match = re.search(r"Assigned difficulty:\s*(\w+)", prompt)
    assert match is not None
    return match.group(1)


def _assigned_diagnostic_intent_from_prompt(prompt: str) -> str:
    match = re.search(r'"intent":\s*"([^"]+)"', prompt)
    assert match is not None
    return match.group(1)


def _assigned_expected_optimization_target_from_prompt(prompt: str) -> str:
    match = re.search(r'"expected_optimization_target":\s*"([^"]+)"', prompt)
    assert match is not None
    return match.group(1)


def _assigned_target_capabilities_from_prompt(prompt: str) -> list[str]:
    match = re.search(r"Target capabilities for this case:\s*(\[[^\]]*\])", prompt)
    assert match is not None
    return json.loads(match.group(1))


def _assigned_capability_combination_from_prompt(prompt: str) -> str:
    match = re.search(r"Capability combination for this case:\s*([^\n]+)", prompt)
    assert match is not None
    return match.group(1).strip()


def _assigned_target_surfaces_from_prompt(prompt: str) -> list[str]:
    match = re.search(r'"target_surfaces":\s*(\[[^\]]*\])', prompt)
    assert match is not None
    parsed = json.loads(match.group(1))
    assert isinstance(parsed, list)
    return [str(item) for item in parsed]

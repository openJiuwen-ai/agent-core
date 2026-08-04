# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for reusable optimization experience learning artifacts."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from openjiuwen.rsi.config import (
    OptimizationExperienceLearnerConfig,
)
from openjiuwen.rsi.optimization_experience_learner import (
    OptimizationExperienceInput,
    OptimizationExperienceLearner,
    OptimizationExperienceRetrievalQuery,
    OptimizationExperienceStageInput,
)


@pytest.mark.asyncio
async def test_record_team_skill_experience_writes_stage_records(tmp_path: Path) -> None:
    """Team Skill experience learning writes a ref and stage records."""
    candidate_dir = tmp_path / "team_skill_001"
    candidate_dir.mkdir()
    (candidate_dir / "optimization_metadata.yaml").write_text(
        yaml.safe_dump({"issue_analysis": {"issues": []}}, allow_unicode=True),
        encoding="utf-8",
    )
    (candidate_dir / "optimization_plan.yaml").write_text(
        yaml.safe_dump({"changes": []}, allow_unicode=True),
        encoding="utf-8",
    )
    learner = OptimizationExperienceLearner(OptimizationExperienceLearnerConfig())

    ref_path = await learner.record_team_skill_experience(
        before_team_skill_ref_path="team_skill/v1",
        after_team_skill_ref_path=str(candidate_dir),
        eval_ref_path="eval_ref.yaml",
        candidate_dir=str(candidate_dir),
        output_dir=str(tmp_path / "experiences"),
        score=0.8,
    )

    ref = yaml.safe_load(Path(ref_path).read_text(encoding="utf-8"))
    assert ref["optimization_type"] == "team_skill"
    assert ref["metadata"]["before_ref_path"] == "team_skill/v1"
    assert len(ref["stage_experience_paths"]) == 2
    stage = yaml.safe_load(Path(ref["stage_experience_paths"][0]).read_text(encoding="utf-8"))
    assert stage["stage"] == "evaluation_feedback_analysis"
    assert stage["experience"]["scope"] == "case_agnostic"


@pytest.mark.asyncio
async def test_record_member_experience_writes_phase_records(tmp_path: Path) -> None:
    """Member experience learning records analysis, planning, and verification phases."""
    learner = OptimizationExperienceLearner(OptimizationExperienceLearnerConfig())

    ref_path = await learner.record_member_experience(
        before_harness_refs_path="harness/v1",
        after_harness_refs_path="harness/v2",
        eval_ref_path="eval_ref.yaml",
        member_optimization_ref_path="member_optimization_ref.yaml",
        analysis_result_path="analysis.yaml",
        plan_path="plan.yaml",
        execution_result_path="execution_results.json",
        verification_path="verification.json",
        fix_result_path="fix_result.json",
        output_dir=str(tmp_path / "experiences"),
        role="solver",
    )

    ref = yaml.safe_load(Path(ref_path).read_text(encoding="utf-8"))
    assert ref["optimization_type"] == "member_harness"
    assert ref["role"] == "solver"
    stages = [yaml.safe_load(Path(path).read_text(encoding="utf-8"))["stage"] for path in ref["stage_experience_paths"]]
    assert stages == [
        "evaluation_result_analysis",
        "optimization_planning",
        "implementation_and_verification",
    ]


@pytest.mark.asyncio
async def test_retrieve_member_stage_experience_reads_persisted_index(tmp_path: Path) -> None:
    """Experience retrieval reads persisted accepted records instead of returning mocks."""
    learner = OptimizationExperienceLearner(OptimizationExperienceLearnerConfig())
    output_dir = tmp_path / "experiences"

    await learner.learn(
        OptimizationExperienceInput(
            optimization_type="member_harness",
            before_ref_path="harness/v1",
            after_ref_path="harness/v2",
            eval_ref_path="eval_ref.yaml",
            output_dir=str(output_dir),
            role="solver",
            stages=[
                OptimizationExperienceStageInput(
                    stage="optimization_planning",
                    source_artifact_paths=["analysis_ref.yaml", "plan.yaml"],
                    summary="Prefer prompt section changes for verifier-loop mistakes.",
                    metadata={
                        "learning_status": "accepted",
                        "failure_signature": "verifier_loop_missing",
                        "mechanism_type": "workflow",
                        "component_layer": "prompt_section",
                        "general_principles": [
                            "Repair loops should inspect verifier output before stopping.",
                        ],
                    },
                ),
            ],
            metadata={"learning_status": "accepted"},
        )
    )

    result = await learner.retrieve_member_stage_experience(
        stage="optimization_planning",
        eval_ref_path="eval_ref.yaml",
        analysis_result_path="analysis.yaml",
        harness_refs_path="harness/current",
        target_members=["solver"],
        candidate_modules=["prompt_section"],
    )

    assert result.query.optimization_type == "member_harness"
    assert result.query.target_members == ["solver"]
    assert result.metadata["retrieval_status"] == "ok"
    assert len(result.matches) == 1
    assert result.matches[0]["stage"] == "optimization_planning"
    assert result.matches[0]["component_layer"] == "prompt_section"
    assert result.matches[0]["learning_status"] == "accepted"
    assert result.matches[0]["summary"] == ("Prefer prompt section changes for verifier-loop mistakes.")
    assert result.matches[0]["experience"]["general_principles"] == [
        "Repair loops should inspect verifier output before stopping.",
    ]


@pytest.mark.asyncio
async def test_retrieve_filters_status_and_can_allow_provisional(tmp_path: Path) -> None:
    """Cross-run retrieval defaults to accepted records, with explicit provisional opt-in."""
    learner = OptimizationExperienceLearner(OptimizationExperienceLearnerConfig())
    output_dir = tmp_path / "experiences"

    await learner.learn(
        OptimizationExperienceInput(
            optimization_type="member_harness",
            before_ref_path="harness/v1",
            after_ref_path="harness/v2",
            eval_ref_path="eval_ref.yaml",
            output_dir=str(output_dir),
            role="solver",
            stages=[
                OptimizationExperienceStageInput(
                    stage="optimization_planning",
                    summary="Batch-local unconfirmed pattern.",
                    metadata={
                        "learning_status": "provisional",
                        "component_layer": "prompt",
                    },
                ),
            ],
            metadata={"learning_status": "provisional"},
        )
    )

    default_result = await learner.retrieve(
        OptimizationExperienceRetrievalQuery(
            optimization_type="member_harness",
            stage="optimization_planning",
            target_members=["solver"],
            metadata={"experience_root": str(output_dir)},
        )
    )
    assert default_result.matches == []

    provisional_result = await learner.retrieve(
        OptimizationExperienceRetrievalQuery(
            optimization_type="member_harness",
            stage="optimization_planning",
            target_members=["solver"],
            metadata={"experience_root": str(output_dir), "allow_provisional": True},
        )
    )
    assert len(provisional_result.matches) == 1
    assert provisional_result.matches[0]["learning_status"] == "provisional"


@pytest.mark.asyncio
async def test_learning_disabled_does_not_write_or_retrieve(tmp_path: Path) -> None:
    """Disabled 010 writes no artifacts and returns empty retrieval metadata."""
    learner = OptimizationExperienceLearner(OptimizationExperienceLearnerConfig(enabled=False))
    output_dir = tmp_path / "experiences"

    ref_path = await learner.learn(
        OptimizationExperienceInput(
            optimization_type="member_harness",
            before_ref_path="harness/v1",
            after_ref_path="harness/v2",
            eval_ref_path="eval_ref.yaml",
            output_dir=str(output_dir),
        )
    )

    result = await learner.retrieve(
        OptimizationExperienceRetrievalQuery(
            optimization_type="member_harness",
            stage="optimization_planning",
            metadata={"experience_root": str(output_dir)},
        )
    )
    assert ref_path == ""
    assert not output_dir.exists()
    assert result.matches == []
    assert result.metadata["retrieval_status"] == "disabled"


@pytest.mark.asyncio
async def test_stage_payload_sanitizes_raw_trace_and_sensitive_fields(tmp_path: Path) -> None:
    """Persisted experience summaries must not store raw traces, answers, or credentials."""
    source = tmp_path / "analysis_ref.yaml"
    source.write_text(
        yaml.safe_dump(
            {
                "issues": [
                    {
                        "issue_id": "issue_001",
                        "failure_signature": "secret_leak",
                        "mechanism_type": "workflow",
                        "target_ref": "solver",
                        "summary": "Agent copied a token into output.",
                        "raw_trace": "token sk-real-secret",
                        "answer": "benchmark answer",
                        "api_key": "sk-real-secret",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    learner = OptimizationExperienceLearner(OptimizationExperienceLearnerConfig())

    ref_path = await learner.learn(
        OptimizationExperienceInput(
            optimization_type="member_harness",
            before_ref_path="harness/v1",
            after_ref_path="harness/v2",
            eval_ref_path="eval_ref.yaml",
            output_dir=str(tmp_path / "experiences"),
            role="solver",
            stages=[
                OptimizationExperienceStageInput(
                    stage="evaluation_result_analysis",
                    source_artifact_paths=[str(source)],
                    summary="Extract analyzer issue.",
                    metadata={"learning_status": "accepted"},
                ),
            ],
            metadata={"learning_status": "accepted"},
        )
    )

    ref = yaml.safe_load(Path(ref_path).read_text(encoding="utf-8"))
    stage_path = Path(ref["stage_experience_paths"][0])
    persisted = stage_path.read_text(encoding="utf-8")
    stage = yaml.safe_load(persisted)

    assert "sk-real-secret" not in persisted
    assert "benchmark answer" not in persisted
    assert "raw_trace" not in persisted
    assert stage["experience"]["problem_signature"]["failure_signature"] == "secret_leak"
    assert stage["experience"]["problem_signature"]["mechanism_type"] == "workflow"

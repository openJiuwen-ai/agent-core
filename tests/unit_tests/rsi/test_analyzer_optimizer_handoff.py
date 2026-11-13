# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Cross-stage contracts for bounded diagnosis; no external model calls."""

import json
from dataclasses import asdict
from pathlib import Path

import pytest
import yaml

from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
    _aggregate_structured_diagnoses,
    _build_diagnosis_input_json,
)
from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.case_reader import CaseAnalysisInput, DeterministicSignals
from openjiuwen.rsi.harness_rsi.member_optimizer.action_planner import (
    MemberActionPlannerAgent,
    _bind_immutable_hypotheses,
)
from openjiuwen.rsi.harness_rsi.member_optimizer.hypothesis import (
    compile_optimization_hypotheses,
    load_optimization_hypotheses,
)
from openjiuwen.rsi.harness_rsi.member_optimizer.schema import (
    MechanismAttributionReport,
    MemberOptimizationTarget,
    RoleAttributionReport,
)


def test_supported_diagnosis_reaches_skill_plan_without_retired_audit_fields(tmp_path: Path) -> None:
    required = "After writing a structured artifact, reopen it and compare its fields with the declared schema."
    decision = {
        "wrong_decision": "submit the output without reopening it",
        "causal_distinction": "a successful write is not schema conformance",
        "required_action": required,
        "acceptance_observable": "reopened fields match the declared schema",
        "activation_phase": "pre_submission",
        "scope_boundary": ["do not alter unrelated fields"],
    }
    diagnoses = [
        {
            "case_id": "case_a",
            "target_ref": "member_harness.solver.skill",
            "failure_mode": "unchecked_serialization",
            "summary": "Output schema was not checked",
            "root_cause": "The writer omitted fields but the agent accepted the write result as validation.",
            "general_mechanism": required,
            "recommendation": "Add a reusable schema validation procedure",
            "decision_contract": decision,
            "confidence": "high",
            "severity": "high",
            "affected_components": ["solver"],
            "evidence_refs": [{"case_id": "case_a", "step_pointer": "step_4"}],
        },
        {
            "case_id": "case_b",
            "target_ref": "unassigned",
            "summary": "Missing task input",
        },
    ]
    issues = _aggregate_structured_diagnoses(
        per_case_results=diagnoses,
        max_issues=3,
        evidence_limit_per_issue=3,
    )
    assert len(issues) == 1
    assert "evidence_status" not in issues[0].metadata["attribution"]
    ref = tmp_path / "analysis_ref.yaml"
    ref.write_text(
        yaml.safe_dump(
            {
                "issues": [asdict(issue) for issue in issues],
                "metadata": {"analysis_status": "completed"},
            }
        ),
        encoding="utf-8",
    )
    path = compile_optimization_hypotheses(
        analysis_ref_path=str(ref),
        cases=[{"case_id": "case_a", "input": "Create a structured report"}],
        output_path=tmp_path / "hypotheses.yaml",
    )
    hypotheses = load_optimization_hypotheses(path)
    assert len(hypotheses) == 1
    assert hypotheses[0]["target_case_ids"] == ["case_a"]
    assert hypotheses[0]["decision_contract"] == decision
    message = MemberActionPlannerAgent._build_user_message(
        targets=[
            MemberOptimizationTarget(
                role="solver",
                harness_ref_path="harness",
                attributed_issue_ids=[issues[0].issue_id],
                optimization_surfaces=["skill"],
            )
        ],
        role_attribution_report=RoleAttributionReport(),
        mechanism_attribution_report=MechanismAttributionReport(),
        optimization_hypotheses=hypotheses,
    )
    assert required in message
    plan = {
        "actions": [
            {
                "action_id": "add_check",
                "action_group": "skill",
                "operation": "add",
                "target_path": "skills/schema_check/SKILL.md",
                "attributed_issue_ids": [issues[0].issue_id],
            }
        ]
    }
    _bind_immutable_hypotheses(plan, hypotheses)
    action = plan["actions"][0]
    assert action["operation"] == "add"
    assert action["action_group"] == "skill"
    assert action["expected_effect"] == required
    assert action["constraints"]["optimization_contracts"][0]["decision_contract"] == decision


@pytest.mark.parametrize("has_summary", [True, False])
def test_input_preserves_full_task_and_evaluated_harness_with_or_without_summary(
    tmp_path: Path,
    has_summary: bool,
) -> None:
    task = "Complete the requested artifact.\n" * 1000 + "Keep the final constraint."
    metadata = {
        "judge_detail": {
            "criteria": [
                {
                    "verifier_id": "schema",
                    "score": 0.0,
                    "status": "ok",
                    "rationale": "Missing output field",
                }
            ]
        }
    }
    case = CaseAnalysisInput(
        case_id="case_a",
        status="failed",
        score=0.0,
        input=task,
        expected=None,
        response="Written",
        error="",
        evaluation_method="script_based",
        evaluation_passed=False,
        evaluation_reason="schema mismatch",
        evaluation_metadata=metadata,
        trace_path=str(tmp_path / "trace.json"),
        result_path=str(tmp_path / "result.json"),
    )
    context = {"status": "available", "roles": [{"role": "solver", "skills": []}]}
    payload = json.loads(
        _build_diagnosis_input_json(
            case=case,
            signals=DeterministicSignals(method="script_based"),
            retrieved_experience=None,
            evidence_summary_available=has_summary,
            harness_context=context,
        )
    )
    assert payload["authoritative_task_contract"]["input_excerpt"] == task
    assert payload["current_harness"] == context
    assert payload["primary_evidence"]["evidence_summary_available"] is has_summary
    assert payload["case_facts"]["judge_breakdown"]["criteria"][0]["criterion_id"] == "schema"
    assert payload["case_facts"]["judge_breakdown"]["criteria"][0]["score"] == 0.0

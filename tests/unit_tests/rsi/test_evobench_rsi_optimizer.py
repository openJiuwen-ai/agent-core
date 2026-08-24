# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for the native Evo-Bench PolicyHarness RSI optimizer."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from examples.rsi.evobench.rsi_optimizer import (
    PolicyHarnessRSIOptimizer,
    _audit_uses_new_substitution_families,
    _build_mandatory_abstraction_request,
    _build_transfer_review_request,
    _forbidden_concrete_term_errors,
    _quoted_binding_phrases,
    _validate_causal_binding_independence,
    _validate_skill_spec,
    _validate_transfer_audit,
)


def _write_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _policy_harness(root: Path) -> Path:
    harness = root / "policy_harness_seed"
    (harness / "agent").mkdir(parents=True)
    (harness / "tools").mkdir()
    (harness / "system_prompt.md").write_text("Solve the task and verify the result.\n", encoding="utf-8")
    (harness / "harness.json").write_text(
        json.dumps(
            {
                "name": "seed",
                "version": "0.1.0",
                "system_prompt": "system_prompt.md",
                "max_steps": 300,
                "rollout_wall_clock_seconds": 3600,
                "command_timeout_seconds": 600,
                "tools": ["run_shell_command", "finish"],
                "tool_loop_compaction": {
                    "enabled": True,
                    "consecutive_threshold": 4,
                    "bailout_threshold": 3,
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (harness / "harness.py").write_text("VALUE = 'framework'\n", encoding="utf-8")
    (harness / "agent" / "loop.py").write_text("def run():\n    return True\n", encoding="utf-8")
    (harness / "tools" / "shell.py").write_text("NAME = 'shell'\n", encoding="utf-8")
    return harness


def _inputs(tmp_path: Path) -> dict[str, Path]:
    harness = _policy_harness(tmp_path)
    refs = tmp_path / "harness_refs.yaml"
    _write_yaml(
        refs,
        {
            "version": 1,
            "harness_refs": {"policy_harness": "policy_harness_seed"},
            "roles": [
                {
                    "role": "policy_harness",
                    "member_name": "policy_harness",
                    "harness_ref_path": "policy_harness_seed",
                }
            ],
            "candidate_gate": {"status": "rejected", "reason": "stale_parent_gate"},
            "candidate_ready_roles": [],
        },
    )
    case_dir = tmp_path / "evaluation" / "cases" / "case-1"
    case_dir.mkdir(parents=True)
    result = case_dir / "result.json"
    trace = case_dir / "trace.json"
    result.write_text(json.dumps({"score": 0, "reason": "artifact missing"}), encoding="utf-8")
    trace.write_text(json.dumps({"steps": ["inspected", "stopped before writing"]}), encoding="utf-8")
    eval_ref = tmp_path / "evaluation" / "eval_ref.yaml"
    _write_yaml(
        eval_ref,
        {
            "cases": [
                {
                    "case_id": "case-1",
                    "result_path": str(result),
                    "trace_path": str(trace),
                }
            ]
        },
    )
    analysis = tmp_path / "analysis.yaml"
    _write_yaml(
        analysis,
        {
            "issues": [
                {
                    "issue_id": "ISSUE-1",
                    "summary": "The policy stopped before producing the artifact.",
                    "recommendation": "Require final artifact verification before finish.",
                    "affected_cases": ["case-1"],
                    "evidence": [{"case_id": "case-1", "step": "final"}],
                    "metadata": {
                        "attribution": {
                            "evidence_status": "confirmed",
                            "target_ref": "member_harness.policy_harness.prompt",
                            "hypothesis_assessment": [{"hypothesis_id": "h_supported", "status": "supported"}],
                            "causal_coverage": {
                                "explained_requirement_ids": ["criterion:artifact"],
                                "residual_requirement_ids": ["criterion:content"],
                                "unexplained_observations": ["artifact content remains unverified"],
                                "counterfactual_prediction": "the artifact is created before finish",
                                "sufficiency_status": "local_contributor",
                            },
                        }
                    },
                },
                {
                    "issue_id": "ISSUE-2",
                    "summary": "Unrelated issue",
                    "affected_cases": ["case-2"],
                },
            ]
        },
    )
    hypotheses = tmp_path / "hypotheses.yaml"
    _write_yaml(
        hypotheses,
        {
            "hypotheses": [
                {"source_issue_id": "ISSUE-1", "required_behavior": "verify output"},
                {
                    "source_issue_id": "ISSUE-2",
                    "required_behavior": "UNRELATED_HYPOTHESIS_MUST_NOT_LEAK",
                },
            ]
        },
    )
    return {
        "harness": harness,
        "refs": refs,
        "eval": eval_ref,
        "analysis": analysis,
        "hypotheses": hypotheses,
    }


def test_optimizer_copies_full_policy_harness_and_writes_orchestrator_contract(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    requests: list[str] = []

    async def generate(request: str) -> dict[str, Any]:
        requests.append(request)
        return {
            "system_prompt_append": (
                "Before calling finish, verify that every requested artifact exists "
                "at the exact output path and is readable."
            ),
            "harness_updates": {},
            "rationale": "The failed trajectory stopped before writing the required artifact.",
            "expected_effect": "The policy verifies the artifact before finishing.",
        }

    optimizer = PolicyHarnessRSIOptimizer(
        SimpleNamespace(model_config_ref="unused-in-test.yaml"),
        patch_generator=generate,
    )
    member_ref = asyncio.run(
        optimizer.optimize(
            eval_ref_path=str(inputs["eval"]),
            analysis_result_path=str(inputs["analysis"]),
            harness_refs_path=str(inputs["refs"]),
            output_dir=str(tmp_path / "optimization"),
            defer_publish=True,
            rejected_capabilities=[{"surface": "tool", "reason": "not activated"}],
            single_harness=True,
            optimization_hypotheses_path=str(inputs["hypotheses"]),
            optimization_issue_ids=["ISSUE-1"],
            optimization_experience={
                "sibling_generation": {
                    "generation_index": 2,
                    "candidate_count": 3,
                    "candidate_id": "cohort_c002",
                    "prior_proposals": [{"candidate_id": "cohort_c001", "surface": "prompt"}],
                },
                "improver_policy": {
                    "version_id": "I1",
                    "policy_digest": "digest_i1",
                    "generation_directives": {
                        "require_activation_evidence": {"prompt": True},
                    },
                },
            },
        )
    )

    artifact = yaml.safe_load(Path(member_ref).read_text(encoding="utf-8"))
    assert artifact["status"] == "success"
    assert artifact["promotion_status"] == "pending_gate"
    assert artifact["metadata"]["improver_protocol_version"] == "generic_behavior_intervention_v15"
    assert artifact["candidate_ready_roles"] == ["policy_harness"]
    candidate_refs = yaml.safe_load(Path(artifact["optimized_harness_refs_path"]).read_text(encoding="utf-8"))
    assert "candidate_gate" not in candidate_refs
    assert "candidate_ready_roles" not in candidate_refs
    candidate = Path(candidate_refs["harness_refs"]["policy_harness"])
    assert candidate.is_dir()
    assert {path.relative_to(candidate).as_posix() for path in candidate.rglob("*") if path.is_file()} == {
        "agent/loop.py",
        "harness.json",
        "harness.py",
        "system_prompt.md",
        "tools/shell.py",
    }
    assert (candidate / "harness.py").read_bytes() == (inputs["harness"] / "harness.py").read_bytes()
    assert (candidate / "agent" / "loop.py").read_bytes() == (inputs["harness"] / "agent" / "loop.py").read_bytes()
    candidate_config = json.loads((candidate / "harness.json").read_text(encoding="utf-8"))
    assert candidate_config["max_steps"] == 300
    assert candidate_config["tools"] == ["run_shell_command", "finish"]
    assert (candidate / "harness.json").read_bytes() == (inputs["harness"] / "harness.json").read_bytes()
    assert "verify that every requested artifact exists" in (candidate / "system_prompt.md").read_text(encoding="utf-8")
    assert (inputs["harness"] / "system_prompt.md").read_text(encoding="utf-8") == (
        "Solve the task and verify the result.\n"
    )

    plan = yaml.safe_load(Path(artifact["plan_path"]).read_text(encoding="utf-8"))
    assert plan["metadata"]["improver_protocol_version"] == "generic_behavior_intervention_v15"
    assert plan["targets"][0]["role"] == "policy_harness"
    assert plan["targets"][0]["attributed_issue_ids"] == ["ISSUE-1"]
    assert plan["targets"][0]["evidence_refs"] == [{"case_id": "case-1", "issue_id": "ISSUE-1"}]
    assert plan["actions"][0]["action_group"] == "prompt"
    assert plan["actions"][0]["operation"] == "modify"
    assert plan["actions"][0]["target_path"] == "system_prompt.md"
    assert [action["target_path"] for action in plan["actions"]] == ["system_prompt.md"]
    assert plan["actions"][0]["constraints"]["analyzer_counterfactual_predictions"] == [
        "the artifact is created before finish"
    ]
    capabilities = yaml.safe_load(Path(artifact["metadata"]["capabilities_path"]).read_text(encoding="utf-8"))
    assert capabilities["capabilities"][0]["analyzer_counterfactual_predictions"] == [
        "the artifact is created before finish"
    ]
    capabilities = yaml.safe_load(Path(artifact["metadata"]["capabilities_path"]).read_text(encoding="utf-8"))[
        "capabilities"
    ]
    assert all(capability["target_case_ids"] == ["case-1"] for capability in capabilities)
    assert "sibling candidate 2 of\n3" in requests[0]
    assert "activation or routing of the diagnosed behavior" in requests[0]
    assert '"version": "generic_behavior_intervention_v15"' in requests[0]
    assert '"supported_mutation_contract"' in requests[0]
    assert '"improver_policy_directives"' in requests[0]
    assert '"version_id": "I1"' in requests[0]
    assert '"require_activation_evidence"' in requests[0]
    assert "must not override the diagnosed" in requests[0]
    assert "transfer across materially different task domains" in requests[0]
    assert "removing the observed domain, artifact type, file extension" in requests[0]
    assert "Apply this substitution test before returning" in requests[0]
    assert "content that only this case requested" in requests[0]
    assert "cohort_c001" in requests[0]
    assert "stopped before writing" in requests[0]
    assert '"sufficiency_status": "local_contributor"' in requests[0]
    assert "local_contributor is a bounded defect" in requests[0]
    assert "UNRELATED_HYPOTHESIS_MUST_NOT_LEAK" not in requests[0]
    capabilities = yaml.safe_load(Path(artifact["metadata"]["capabilities_path"]).read_text(encoding="utf-8"))
    assert capabilities["capabilities"][0]["intervention"].startswith("Before calling finish")


def test_mandatory_abstraction_preserves_causal_roles_not_observable_proxy() -> None:
    request = _build_mandatory_abstraction_request(
        {
            "source_hypothesis_id": "h1",
            "system_prompt_append": "Change the authoritative input before calculating its result.",
            "harness_updates": {},
            "rationale": "The run calculated a derived result from unchanged state.",
            "expected_effect": "The source mutation precedes downstream calculation.",
        },
        {
            "bindings": [
                {
                    "observed_decision": "A downstream calculation ran against unchanged source state.",
                    "required_behavior": (
                        "Persist the upstream mutation before downstream calculation, not 'run a visible check first'."
                    ),
                    "predicted_observable": "The calculation consumes the mutated state.",
                }
            ]
        },
    )

    assert "Preserve causal roles and ordering" in request
    assert "Never replace a required causal action" in request
    assert "scope_boundary entry as immutable" in request
    assert "Persist the upstream mutation before downstream calculation" in request
    assert "run a visible check first" in request
    assert _quoted_binding_phrases(
        {"bindings": [{"required_behavior": "Do not repeat 'run a visible check first' as an example."}]}
    ) == ["run a visible check first"]


def test_transfer_audit_substitution_samples_must_be_independent() -> None:
    history = [
        {
            "substitution_test": {
                "task_family_a": "source-code repair",
                "task_family_b": "document review",
            }
        }
    ]
    assert not _audit_uses_new_substitution_families(
        {
            "substitution_test": {
                "task_family_a": "source-code repair",
                "task_family_b": "security audit",
            }
        },
        history,
    )
    assert _audit_uses_new_substitution_families(
        {
            "substitution_test": {
                "task_family_a": "database migration",
                "task_family_b": "release planning",
            }
        },
        history,
    )


def test_transfer_review_requires_cross_domain_behavior_abstraction() -> None:
    request = _build_transfer_review_request(
        {
            "source_hypothesis_id": "h1",
            "system_prompt_append": "Use a workbook application to recalculate formulas.",
            "harness_updates": {},
            "rationale": "The observed implementation route timed out.",
            "expected_effect": "The next attempt completes before timeout.",
        },
        {
            "schema_version": 1,
            "bindings": [
                {
                    "observed_decision": "The agent stopped before writing the artifact.",
                    "required_behavior": "Persist and verify the requested output before finish.",
                    "predicted_observable": "The artifact exists and is readable.",
                }
            ],
        },
    )

    assert "Do not rewrite it" in request
    assert "causal_binding (controller-frozen" in request.casefold()
    assert "familiar mechanism from another task is a violation" in request
    assert "fallback contradicts a CAUSAL_BINDING scope_boundary" in request
    assert "hidden expected answer" in request
    assert '"concrete_terms": []' in request
    assert "Domain vocabulary is never allowed" in request
    assert "substitution_test" in request
    assert "Persist and verify the requested output before finish" in request
    assert "already available, mature capability" not in request
    assert '"source_hypothesis_id": "h1"' in request


def test_transfer_audit_requires_independent_evidence_and_explanations() -> None:
    approved = _validate_transfer_audit(
        {
            "causal_faithful": True,
            "evidence_independent": True,
            "task_detail_free": True,
            "cross_domain_transferable": True,
            "concrete_terms": [],
            "substitution_test": {
                "task_family_a": "source-code repair",
                "task_family_b": "document review",
                "required_edits_a": [],
                "required_edits_b": [],
                "works_unchanged": True,
            },
            "violations": [],
        }
    )
    assert approved["evidence_independent"] is True

    with pytest.raises(ValueError, match="explain at least one violation"):
        _validate_transfer_audit(
            {
                "causal_faithful": True,
                "evidence_independent": False,
                "task_detail_free": True,
                "cross_domain_transferable": True,
                "concrete_terms": [],
                "substitution_test": {
                    "task_family_a": "source-code repair",
                    "task_family_b": "document review",
                    "required_edits_a": [],
                    "required_edits_b": [],
                    "works_unchanged": True,
                },
                "violations": [],
            }
        )


def test_rejected_concrete_term_matching_handles_inflection_without_symbol_false_positive() -> None:
    patch = {"system_prompt_append": "Resolve each concrete value and all derived values before completion."}
    assert _forbidden_concrete_term_errors(patch, ["formulas"]) == []
    assert _forbidden_concrete_term_errors(patch, ["#VALUE!"]) == []
    assert _forbidden_concrete_term_errors(
        {"system_prompt_append": "Do not leave an uncomputed formula."},
        ["formulas"],
    )


def test_causal_binding_rejects_expected_evaluator_outcome_but_not_failed_requirement_text() -> None:
    _validate_causal_binding_independence(
        {
            "bindings": [
                {
                    "failed_requirement": "The scored response must state a positive verdict.",
                    "causal_distinction": "The agent treated optional observations as mandatory requirements.",
                    "required_behavior": "Separate mandatory requirements from optional observations.",
                }
            ]
        }
    )
    _validate_causal_binding_independence(
        {
            "bindings": [
                {
                    "causal_distinction": "Execute a conditional action only when its public trigger is true.",
                    "required_behavior": "When the trigger is false, omit the conditional action.",
                    "supported_hypotheses": [
                        {
                            "claim": "The agent executed an action after concluding its trigger was false.",
                            "reason": "The criterion requires the conditional action to be omitted.",
                            "logic_check": "The evaluator requires the same public branch behavior.",
                        }
                    ],
                }
            ]
        }
    )

    with pytest.raises(ValueError, match="evaluator-owned expected outcome"):
        _validate_causal_binding_independence(
            {
                "bindings": [
                    {
                        "causal_distinction": "The evaluator requires a positive verdict.",
                        "required_behavior": "Align the answer with the expected verdict.",
                    }
                ]
            }
        )


def test_transfer_reviewer_rejects_cross_issue_mechanism_and_generator_repairs(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    generation_requests: list[str] = []
    review_requests: list[str] = []

    def generate(request: str) -> dict[str, Any]:
        generation_requests.append(request)
        append = (
            "Prefer an existing calculation engine instead of rebuilding it."
            if len(generation_requests) == 1
            else "Before finishing, persist every required output and verify that it is readable."
        )
        return {
            "source_hypothesis_id": "h_supported",
            "system_prompt_append": append,
            "harness_updates": {},
            "rationale": "The trace ended before the required output was persisted.",
            "expected_effect": "The required output exists and is readable before finish.",
        }

    def review(request: str) -> dict[str, Any]:
        review_requests.append(request)
        if len(review_requests) == 1:
            return {
                "causal_faithful": False,
                "evidence_independent": True,
                "task_detail_free": True,
                "cross_domain_transferable": True,
                "concrete_terms": [],
                "substitution_test": {
                    "task_family_a": "source-code repair",
                    "task_family_b": "document review",
                    "required_edits_a": [],
                    "required_edits_b": [],
                    "works_unchanged": True,
                },
                "violations": ["The calculation-engine mechanism is not the frozen artifact-persistence cause."],
            }
        return {
            "causal_faithful": True,
            "evidence_independent": True,
            "task_detail_free": True,
            "cross_domain_transferable": True,
            "concrete_terms": [],
            "substitution_test": {
                "task_family_a": "source-code repair",
                "task_family_b": "document review",
                "required_edits_a": [],
                "required_edits_b": [],
                "works_unchanged": True,
            },
            "violations": [],
        }

    artifact_path = asyncio.run(
        PolicyHarnessRSIOptimizer(
            patch_generator=generate,
            transfer_reviewer=review,
        ).optimize(
            eval_ref_path=str(inputs["eval"]),
            analysis_result_path=str(inputs["analysis"]),
            harness_refs_path=str(inputs["refs"]),
            output_dir=str(tmp_path / "optimization"),
            single_harness=True,
            optimization_issue_ids=["ISSUE-1"],
        )
    )

    artifact = yaml.safe_load(Path(artifact_path).read_text(encoding="utf-8"))
    refs = yaml.safe_load(Path(artifact["optimized_harness_refs_path"]).read_text(encoding="utf-8"))
    prompt = (Path(refs["harness_refs"]["policy_harness"]) / "system_prompt.md").read_text(encoding="utf-8")
    assert len(generation_requests) == 2
    assert len(review_requests) == 2
    assert "AUDIT VIOLATIONS" in generation_requests[1]
    assert "persist every required output" in prompt
    assert "calculation engine" not in prompt


def test_optimizer_rejects_legacy_analysis_without_actionable_issue(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    _write_yaml(inputs["analysis"], {"issues": []})
    model_called = False

    def generate(_request: str) -> dict[str, Any]:
        nonlocal model_called
        model_called = True
        return {"system_prompt_append": "This must not be generated."}

    optimizer = PolicyHarnessRSIOptimizer(patch_generator=generate)
    with pytest.raises(ValueError, match="actionable, evidence-grounded"):
        asyncio.run(
            optimizer.optimize(
                eval_ref_path=str(inputs["eval"]),
                analysis_result_path=str(inputs["analysis"]),
                harness_refs_path=str(inputs["refs"]),
                output_dir=str(tmp_path / "optimization"),
                optimization_hypotheses_path=str(inputs["hypotheses"]),
            )
        )
    assert model_called is False


def test_optimizer_rejects_issue_without_attribution_contract(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    analysis = yaml.safe_load(inputs["analysis"].read_text(encoding="utf-8"))
    analysis["issues"][0]["metadata"] = {}
    _write_yaml(inputs["analysis"], analysis)
    model_called = False

    def generate(_request: str) -> dict[str, Any]:
        nonlocal model_called
        model_called = True
        return {"system_prompt_append": "This must not be generated."}

    with pytest.raises(ValueError, match="requested actionable optimization issues"):
        asyncio.run(
            PolicyHarnessRSIOptimizer(patch_generator=generate).optimize(
                eval_ref_path=str(inputs["eval"]),
                analysis_result_path=str(inputs["analysis"]),
                harness_refs_path=str(inputs["refs"]),
                output_dir=str(tmp_path / "optimization"),
                optimization_issue_ids=["ISSUE-1"],
            )
        )
    assert model_called is False


def test_optimizer_rejects_forbidden_harness_update(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)

    def generate(_request: str) -> dict[str, Any]:
        return {
            "system_prompt_append": "Always verify the final output.",
            "harness_updates": {"tools": ["unsafe_tool"]},
        }

    optimizer = PolicyHarnessRSIOptimizer(patch_generator=generate)
    with pytest.raises(ValueError, match="not allowed"):
        asyncio.run(
            optimizer.optimize(
                str(inputs["eval"]),
                str(inputs["analysis"]),
                str(inputs["refs"]),
                str(tmp_path / "optimization"),
                optimization_issue_ids=["ISSUE-1"],
            )
        )
    assert json.loads((inputs["harness"] / "harness.json").read_text(encoding="utf-8"))["tools"] == [
        "run_shell_command",
        "finish",
    ]


def test_optimizer_requires_policy_harness_role_and_selected_issue(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    bad_refs = tmp_path / "bad_refs.yaml"
    _write_yaml(bad_refs, {"harness_refs": {"solver": str(inputs["harness"])}})
    optimizer = PolicyHarnessRSIOptimizer(patch_generator=lambda _request: {"system_prompt_append": "Verify outputs."})

    with pytest.raises(ValueError, match="policy_harness"):
        asyncio.run(
            optimizer.optimize(
                str(inputs["eval"]),
                str(inputs["analysis"]),
                str(bad_refs),
                str(tmp_path / "bad-role"),
            )
        )


def test_optimizer_stages_candidate_under_short_root_for_deep_output_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs = _inputs(tmp_path)
    short_root = tmp_path / "short-repository"
    fake_module = short_root / "examples" / "rsi" / "evobench" / "rsi_optimizer.py"
    monkeypatch.setattr(
        "examples.rsi.evobench.rsi_optimizer.__file__",
        str(fake_module),
    )
    deep_output = tmp_path / "deep-cohort-segment" / "deep-cohort-segment" / "deep-cohort-segment-extended"
    inline_candidate = deep_output / "member_optimization_001" / "candidate_harness" / "policy_harness"
    longest_relative = max(len(str(path.relative_to(inputs["harness"]))) for path in inputs["harness"].rglob("*"))
    assert len(str(inline_candidate)) + longest_relative + 1 >= 220
    optimizer = PolicyHarnessRSIOptimizer(
        patch_generator=lambda _request: {
            "system_prompt_append": "Verify outputs before finishing.",
            "rationale": "The observed run finished before checking its output.",
            "expected_effect": "The next run checks its output before finishing.",
        }
    )

    member_ref = asyncio.run(
        optimizer.optimize(
            str(inputs["eval"]),
            str(inputs["analysis"]),
            str(inputs["refs"]),
            str(deep_output),
            optimization_issue_ids=["ISSUE-1"],
        )
    )

    artifact = yaml.safe_load(Path(member_ref).read_text(encoding="utf-8"))
    candidate_refs = yaml.safe_load(Path(artifact["optimized_harness_refs_path"]).read_text(encoding="utf-8"))
    candidate = Path(candidate_refs["harness_refs"]["policy_harness"])
    assert candidate.parent.parent == short_root / ".evobench_runs" / "_rsi_candidates"
    assert candidate.name == "h"
    assert (candidate / "system_prompt.md").is_file()
    with pytest.raises(ValueError, match="does not contain requested"):
        asyncio.run(
            optimizer.optimize(
                str(inputs["eval"]),
                str(inputs["analysis"]),
                str(inputs["refs"]),
                str(tmp_path / "bad-issue"),
                optimization_issue_ids=["MISSING"],
            )
        )


def test_optimizer_prefers_materialized_causal_diagnosis_and_feedback_over_raw_trace(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    result_path = tmp_path / "evaluation" / "cases" / "case-1" / "result.json"
    result_path.write_text(
        json.dumps({"score": 0, "reason": "RAW_RESULT_MUST_NOT_DOMINATE"}),
        encoding="utf-8",
    )
    trace_path = tmp_path / "evaluation" / "cases" / "case-1" / "trace.json"
    trace_path.write_text(
        json.dumps({"steps": ["RAW_TRACE_MUST_NOT_DOMINATE", "x" * 20_000]}),
        encoding="utf-8",
    )
    causal_path = tmp_path / "analysis" / "causal_evidence.json"
    causal_path.parent.mkdir(exist_ok=True)
    causal_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "cases": [
                    {
                        "case_id": "case-1",
                        "causal_digest": {
                            "decision": "CAUSAL_DECISION_MARKER: completion was selected before verification",
                            "known_answer": "BENCHMARK_ENTITY_SECRET",
                            "trials": [
                                {
                                    "trial_id": "trial_1",
                                    "final_output": "The artifact was not verified.",
                                    "selected_actions": [
                                        {
                                            "trace_id": "trace_1",
                                            "message_index": 5,
                                            "tool": "finish",
                                            "response_evidence": {
                                                "critical_spans": [
                                                    {"text": "REFERENCED_ACTION_EVIDENCE", "window_complete": True}
                                                ]
                                            },
                                        },
                                        {
                                            "trace_id": "trace_1",
                                            "message_index": 6,
                                            "tool": "finish",
                                            "response_evidence": {
                                                "critical_spans": [
                                                    {"text": "UNREFERENCED_ACTION_NOISE", "window_complete": True}
                                                ]
                                            },
                                        },
                                    ],
                                }
                            ],
                            "tool_contract_observations": [
                                {
                                    "tool": "finish",
                                    "allowed_request_fields": ["output_path"],
                                    "response_fields_not_in_public_request_schema": ["secret_response_field"],
                                }
                            ],
                        },
                        "prior_candidate_feedback": {
                            "experiments": [
                                {
                                    "observed_outcome": {
                                        "target_score_delta": 0.25,
                                        "reason": "FEEDBACK_DELTA_MARKER: intervention activated",
                                    }
                                }
                            ]
                        },
                    },
                    {
                        "case_id": "case-2",
                        "causal_digest": {"decision": "UNSELECTED_CAUSAL_MARKER"},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    diagnoses_path = tmp_path / "analysis" / "per_case_diagnoses.json"
    diagnoses_path.write_text(
        json.dumps(
            {
                "per_case_diagnoses": [
                    {
                        "case_id": "case-1",
                        "root_cause": "DIAGNOSIS_MARKER: no pre-finish artifact check",
                        "recommendation": "Require a reusable verification decision.",
                    },
                    {"case_id": "case-2", "root_cause": "UNSELECTED_DIAGNOSIS_MARKER"},
                ]
            }
        ),
        encoding="utf-8",
    )
    analysis = yaml.safe_load(inputs["analysis"].read_text(encoding="utf-8"))
    analysis["metadata"] = {
        "causal_evidence_path": str(causal_path),
        "per_case_diagnoses_path": str(diagnoses_path),
    }
    analysis["issues"][0]["metadata"]["attribution"]["causal_coverage"]["causal_chain"] = [
        {
            "cause": "The final action happened before verification.",
            "effect": "The artifact remained unchecked.",
            "evidence_refs": [{"trace_id": "trace_1", "message_index": 5}],
        }
    ]
    _write_yaml(inputs["analysis"], analysis)
    hypotheses = yaml.safe_load(inputs["hypotheses"].read_text(encoding="utf-8"))
    hypotheses["hypotheses"][0]["authoritative_observations"] = {
        "metadata": {"verbose_marker": "DUPLICATED_HYPOTHESIS_OBSERVATIONS"}
    }
    hypotheses["hypotheses"][0]["lever_policy"] = {
        "recommended_lever": "instruction",
        "target_ref": "member_harness.policy_harness.prompt",
        "why_this_lever": "The diagnosed decision is controlled by a reusable instruction.",
        "retroactive_check": {"verbose_marker": "DUPLICATED_RETROACTIVE_CHECK"},
    }
    _write_yaml(inputs["hypotheses"], hypotheses)
    requests: list[str] = []

    def generate(request: str) -> dict[str, Any]:
        requests.append(request)
        return {
            "system_prompt_append": ("Before finishing, verify that the requested artifact exists and is readable."),
            "rationale": "The observed run finished without a readable output.",
            "expected_effect": "The next run checks output readability before finishing.",
        }

    optimizer = PolicyHarnessRSIOptimizer(patch_generator=generate)
    asyncio.run(
        optimizer.optimize(
            str(inputs["eval"]),
            str(inputs["analysis"]),
            str(inputs["refs"]),
            str(tmp_path / "structured-optimization"),
            optimization_hypotheses_path=str(inputs["hypotheses"]),
            optimization_issue_ids=["ISSUE-1"],
        )
    )

    request = requests[0]
    assert "CAUSAL_DECISION_MARKER" in request
    assert "DIAGNOSIS_MARKER" in request
    assert "FEEDBACK_DELTA_MARKER" in request
    assert "REFERENCED_ACTION_EVIDENCE" in request
    assert "UNREFERENCED_ACTION_NOISE" not in request
    assert "RAW_TRACE_MUST_NOT_DOMINATE" not in request
    assert "RAW_RESULT_MUST_NOT_DOMINATE" not in request
    assert "DUPLICATED_HYPOTHESIS_OBSERVATIONS" not in request
    assert "DUPLICATED_RETROACTIVE_CHECK" not in request
    assert '"recommended_lever": "instruction"' in request
    assert "UNSELECTED_CAUSAL_MARKER" not in request
    assert "UNSELECTED_DIAGNOSIS_MARKER" not in request
    assert "BENCHMARK_ENTITY_SECRET" not in request
    assert "case-1" not in request
    assert '"legacy_fallback_used": false' in request


@pytest.mark.parametrize(
    ("append", "message"),
    [
        ("For case-1, verify the final artifact.", "case id"),
        ("Return BENCHMARK_ENTITY_SECRET when finishing.", "known answer"),
        ("Always mention Cedar Rapids before finishing.", "task-specific entity"),
        ("Use 3125000 as the decision threshold.", "task-specific numeric"),
        ("Send secret_response_field to the finish tool.", "non-public tool field"),
        ("Send parameter `invented_field` to the finish tool.", "outside the public tool schema"),
    ],
)
def test_optimizer_rejects_case_specific_or_non_public_prompt_content(
    tmp_path: Path,
    append: str,
    message: str,
) -> None:
    inputs = _inputs(tmp_path)
    analysis = yaml.safe_load(inputs["analysis"].read_text(encoding="utf-8"))
    analysis["issues"][0]["metadata"]["causal_digest"] = {
        "known_answer": "BENCHMARK_ENTITY_SECRET",
        "authoritative_task_contract": {
            "prompt": "Determine whether the $3,125,000 threshold is met.",
            "task_entities": ["Cedar Rapids"],
        },
        "tool_contract_observations": [
            {
                "allowed_request_fields": ["output_path"],
                "response_fields_not_in_public_request_schema": ["secret_response_field"],
            }
        ],
    }
    _write_yaml(inputs["analysis"], analysis)
    optimizer = PolicyHarnessRSIOptimizer(patch_generator=lambda _request: {"system_prompt_append": append})

    with pytest.raises(ValueError, match=message):
        asyncio.run(
            optimizer.optimize(
                str(inputs["eval"]),
                str(inputs["analysis"]),
                str(inputs["refs"]),
                str(tmp_path / "leaking-optimization"),
                optimization_issue_ids=["ISSUE-1"],
            )
        )


def test_optimizer_legacy_trace_fallback_is_bounded_when_digest_is_missing(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    trace_path = tmp_path / "evaluation" / "cases" / "case-1" / "trace.json"
    trace_path.write_text(
        "LEGACY_TRACE_HEAD" + ("x" * 10_000) + "LEGACY_TRACE_TAIL",
        encoding="utf-8",
    )
    requests: list[str] = []

    def generate(request: str) -> dict[str, Any]:
        requests.append(request)
        return {
            "system_prompt_append": "Verify the requested artifact before finishing.",
            "rationale": "The observed run finished before verifying its output.",
            "expected_effect": "The next run verifies its output before finishing.",
        }

    optimizer = PolicyHarnessRSIOptimizer(patch_generator=generate)
    asyncio.run(
        optimizer.optimize(
            str(inputs["eval"]),
            str(inputs["analysis"]),
            str(inputs["refs"]),
            str(tmp_path / "legacy-optimization"),
            optimization_issue_ids=["ISSUE-1"],
        )
    )

    request = requests[0]
    assert '"legacy_fallback_used": true' in request
    assert "LEGACY_TRACE_HEAD" in request
    assert "LEGACY_TRACE_TAIL" not in request


def test_optimizer_requires_supported_causal_hypothesis_binding(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    analysis = yaml.safe_load(inputs["analysis"].read_text(encoding="utf-8"))
    attribution = analysis["issues"][0]["metadata"]["attribution"]
    attribution.update(
        {
            "target_ref": "member_harness.policy_harness.prompt",
            "hypothesis_assessment": [
                {"hypothesis_id": "h_supported", "status": "supported"},
                {"hypothesis_id": "h_falsified", "status": "falsified"},
                {"hypothesis_id": "h_unresolved", "status": "unresolved"},
            ],
            "evidence_status": "supported_hypothesis",
        }
    )
    analysis["metadata"] = {"analyzer_protocol_version": "generic_behavior_causal_v6"}
    _write_yaml(inputs["analysis"], analysis)
    optimizer = PolicyHarnessRSIOptimizer(
        patch_generator=lambda _request: {
            "source_hypothesis_id": "h_falsified",
            "system_prompt_append": "Verify the artifact before finishing.",
        }
    )

    with pytest.raises(ValueError, match="not actionable"):
        asyncio.run(
            optimizer.optimize(
                str(inputs["eval"]),
                str(inputs["analysis"]),
                str(inputs["refs"]),
                str(tmp_path / "rejected"),
                optimization_issue_ids=["ISSUE-1"],
            )
        )

    accepted = PolicyHarnessRSIOptimizer(
        patch_generator=lambda _request: {
            "source_hypothesis_id": "h_supported",
            "system_prompt_append": "Verify the artifact before finishing.",
            "rationale": "The observed run finished before artifact verification.",
            "expected_effect": "The artifact exists before finish.",
        }
    )
    member_ref = asyncio.run(
        accepted.optimize(
            str(inputs["eval"]),
            str(inputs["analysis"]),
            str(inputs["refs"]),
            str(tmp_path / "accepted"),
            optimization_issue_ids=["ISSUE-1"],
        )
    )
    artifact = yaml.safe_load(Path(member_ref).read_text(encoding="utf-8"))
    plan = yaml.safe_load(Path(artifact["plan_path"]).read_text(encoding="utf-8"))
    assert plan["actions"][0]["constraints"]["source_causal_hypothesis_id"] == "h_supported"
    assert artifact["metadata"]["source_causal_hypothesis_id"] == "h_supported"


def test_strict_causal_analysis_without_actionable_issue_does_not_generate_candidate(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    _write_yaml(
        inputs["analysis"],
        {
            "issues": [],
            "metadata": {"analyzer_protocol_version": "generic_behavior_causal_v6"},
        },
    )
    optimizer = PolicyHarnessRSIOptimizer(
        patch_generator=lambda _request: {"system_prompt_append": "This must not be generated."}
    )

    with pytest.raises(ValueError, match="actionable, evidence-grounded"):
        asyncio.run(
            optimizer.optimize(
                str(inputs["eval"]),
                str(inputs["analysis"]),
                str(inputs["refs"]),
                str(tmp_path / "strict-no-issue"),
            )
        )


def test_optimizer_rejects_unavailable_surface_without_prompt_downgrade(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    analysis = yaml.safe_load(inputs["analysis"].read_text(encoding="utf-8"))
    analysis["issues"][0]["metadata"]["attribution"]["target_ref"] = "member_harness.policy_harness.tool"
    _write_yaml(inputs["analysis"], analysis)
    model_called = False

    def generate(_request: str) -> dict[str, Any]:
        nonlocal model_called
        model_called = True
        return {"system_prompt_append": "This prompt fallback must never be generated."}

    member_ref = asyncio.run(
        PolicyHarnessRSIOptimizer(patch_generator=generate).optimize(
            str(inputs["eval"]),
            str(inputs["analysis"]),
            str(inputs["refs"]),
            str(tmp_path / "unsupported-tool"),
            optimization_issue_ids=["ISSUE-1"],
        )
    )

    artifact = yaml.safe_load(Path(member_ref).read_text(encoding="utf-8"))
    assert model_called is False
    assert artifact["status"] == "unsupported_surface"
    assert artifact["optimized_harness_refs_path"] == str(inputs["refs"].resolve())
    assert artifact["metadata"]["requested_surfaces"] == ["tool"]
    assert artifact["metadata"]["routing_decision"] == "reject_without_prompt_downgrade"
    assert (inputs["harness"] / "system_prompt.md").read_text(encoding="utf-8") == (
        "Solve the task and verify the result.\n"
    )
    plan = yaml.safe_load(Path(artifact["plan_path"]).read_text(encoding="utf-8"))
    assert plan["actions"] == []
    assert plan["targets"][0]["optimization_surfaces"] == ["tool"]


def test_optimizer_adds_native_skill_package_for_skill_target(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    analysis = yaml.safe_load(inputs["analysis"].read_text(encoding="utf-8"))
    analysis["issues"][0]["metadata"]["attribution"]["target_ref"] = "member_harness.policy_harness.skill"
    _write_yaml(inputs["analysis"], analysis)

    member_ref = asyncio.run(
        PolicyHarnessRSIOptimizer(
            patch_generator=lambda _request: {
                "source_hypothesis_id": "h_supported",
                "system_prompt_append": "",
                "skill": {
                    "name": "state-preserving-recalculation",
                    "description": (
                        "Safely updates formula-driven workbooks whose iterative dependencies require preserved state."
                    ),
                    "body": (
                        "When a workbook uses iterative or circular dependencies, inspect its calculation settings "
                        "before editing. Choose a write path that preserves formula state or run a compatible full "
                        "recalculation after the write. Reopen the saved deliverable and verify the requested outputs "
                        "are numeric and stable. If recalculation fails, restore the source and change the write path."
                    ),
                },
                "harness_updates": {},
                "rationale": "The trace showed that the selected write path discarded required calculation state.",
                "expected_effect": "The next run preserves or regenerates formula state before submission.",
            }
        ).optimize(
            str(inputs["eval"]),
            str(inputs["analysis"]),
            str(inputs["refs"]),
            str(tmp_path / "native-skill"),
            optimization_issue_ids=["ISSUE-1"],
        )
    )

    artifact = yaml.safe_load(Path(member_ref).read_text(encoding="utf-8"))
    assert artifact["status"] == "success"
    candidate_refs = yaml.safe_load(Path(artifact["optimized_harness_refs_path"]).read_text(encoding="utf-8"))
    candidate = Path(candidate_refs["harness_refs"]["policy_harness"])
    skill_path = candidate / "skills" / "state-preserving-recalculation" / "SKILL.md"
    assert skill_path.is_file()
    skill_text = skill_path.read_text(encoding="utf-8")
    assert "name: state-preserving-recalculation" in skill_text
    assert "iterative or circular dependencies" in skill_text
    assert (candidate / "system_prompt.md").read_bytes() == (inputs["harness"] / "system_prompt.md").read_bytes()
    plan = yaml.safe_load(Path(artifact["plan_path"]).read_text(encoding="utf-8"))
    assert plan["actions"][0]["action_group"] == "skill"
    assert plan["actions"][0]["operation"] == "add"
    capabilities = yaml.safe_load(Path(artifact["metadata"]["capabilities_path"]).read_text(encoding="utf-8"))
    assert capabilities["capabilities"][0]["runtime_name"] == "state-preserving-recalculation"


def test_optimizer_updates_evidence_referenced_existing_skill_in_place(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    skill_dir = inputs["harness"] / "skills" / "artifact-readback"
    skill_dir.mkdir(parents=True)
    original = (
        "---\nname: artifact-readback\ndescription: Reopen a persisted artifact before reporting completion.\n---\n\n"
        "After writing an artifact, reopen it and confirm that it is readable.\n"
    )
    (skill_dir / "SKILL.md").write_text(original, encoding="utf-8")
    analysis = yaml.safe_load(inputs["analysis"].read_text(encoding="utf-8"))
    issue = analysis["issues"][0]
    issue["recommendation"] = "Strengthen artifact-readback because it was invoked but stopped too early."
    issue["metadata"]["attribution"]["target_ref"] = "member_harness.policy_harness.skill"
    _write_yaml(inputs["analysis"], analysis)

    member_ref = asyncio.run(
        PolicyHarnessRSIOptimizer(
            patch_generator=lambda _request: {
                "source_hypothesis_id": "h_supported",
                "system_prompt_append": "",
                "skill": {
                    "name": "artifact-readback",
                    "description": "Reopen and validate a persisted artifact before reporting completion.",
                    "body": (
                        "After writing an artifact, reopen it using an independent read path. Check every "
                        "task-visible invariant and confirm the persisted state matches the intended mutation. "
                        "If validation fails, repair the artifact and repeat the read-back before finishing."
                    ),
                },
                "harness_updates": {},
                "rationale": "The referenced Skill was invoked but its validation procedure ended too early.",
                "expected_effect": "The next run completes independent artifact read-back before finishing.",
            }
        ).optimize(
            str(inputs["eval"]),
            str(inputs["analysis"]),
            str(inputs["refs"]),
            str(tmp_path / "skill-update"),
            optimization_issue_ids=["ISSUE-1"],
        )
    )

    artifact = yaml.safe_load(Path(member_ref).read_text(encoding="utf-8"))
    candidate = Path(
        yaml.safe_load(Path(artifact["optimized_harness_refs_path"]).read_text(encoding="utf-8"))["harness_refs"][
            "policy_harness"
        ]
    )
    assert (candidate / "skills" / "artifact-readback" / "SKILL.md").read_text(encoding="utf-8") != original
    assert [path.name for path in (candidate / "skills").iterdir()] == ["artifact-readback"]
    plan = yaml.safe_load(Path(artifact["plan_path"]).read_text(encoding="utf-8"))
    assert plan["actions"][0]["operation"] == "update"
    assert plan["actions"][0]["action_type"] == "update_file"
    assert artifact["metadata"]["skill_mutation_policy"]["required_name"] == "artifact-readback"


def test_optimizer_rejects_duplicate_skill_add_when_existing_skill_is_referenced(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    skill_dir = inputs["harness"] / "skills" / "artifact-readback"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: artifact-readback\ndescription: Reopen a persisted artifact before completion.\n---\n\n"
        "Reopen the persisted artifact and verify its task-visible invariants before finishing.\n",
        encoding="utf-8",
    )
    analysis = yaml.safe_load(inputs["analysis"].read_text(encoding="utf-8"))
    issue = analysis["issues"][0]
    issue["recommendation"] = "Strengthen artifact-readback after its incomplete invocation."
    issue["metadata"]["attribution"]["target_ref"] = "member_harness.policy_harness.skill"
    _write_yaml(inputs["analysis"], analysis)

    with pytest.raises(ValueError, match="existing evidence-referenced Skill"):
        asyncio.run(
            PolicyHarnessRSIOptimizer(
                patch_generator=lambda _request: {
                    "source_hypothesis_id": "h_supported",
                    "system_prompt_append": "",
                    "skill": {
                        "name": "another-readback-skill",
                        "description": "Reopen and validate a persisted artifact before reporting completion.",
                        "body": (
                            "After writing an artifact, reopen it and verify every task-visible invariant. "
                            "Repair any mismatch, persist the correction, and repeat validation before finishing."
                        ),
                    },
                    "harness_updates": {},
                    "rationale": "The existing validation procedure ended too early.",
                    "expected_effect": "The next run validates the persisted artifact before finishing.",
                }
            ).optimize(
                str(inputs["eval"]),
                str(inputs["analysis"]),
                str(inputs["refs"]),
                str(tmp_path / "duplicate-skill"),
                optimization_issue_ids=["ISSUE-1"],
            )
        )


def test_skill_validation_rejects_hidden_expected_outcome_dependency() -> None:
    with pytest.raises(ValueError, match="must not depend on an expected"):
        _validate_skill_spec(
            {
                "name": "artifact-independent-validation",
                "description": "Validate a persisted artifact after a state-changing operation.",
                "body": (
                    "Reopen the persisted artifact, inspect its derived state, and compare the values "
                    "against the independently computed expected results before reporting completion."
                ),
            },
            leakage_guard={},
        )


def test_optimizer_does_not_treat_generic_config_as_execution_budget(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    analysis = yaml.safe_load(inputs["analysis"].read_text(encoding="utf-8"))
    analysis["issues"][0]["metadata"]["attribution"]["target_ref"] = "member_harness.policy_harness.config"
    _write_yaml(inputs["analysis"], analysis)
    model_called = False

    def generate(_request: str) -> dict[str, Any]:
        nonlocal model_called
        model_called = True
        return {"harness_updates": {"max_steps": 420}}

    member_ref = asyncio.run(
        PolicyHarnessRSIOptimizer(patch_generator=generate).optimize(
            str(inputs["eval"]),
            str(inputs["analysis"]),
            str(inputs["refs"]),
            str(tmp_path / "unsupported-config"),
            optimization_issue_ids=["ISSUE-1"],
        )
    )

    artifact = yaml.safe_load(Path(member_ref).read_text(encoding="utf-8"))
    assert model_called is False
    assert artifact["status"] == "unsupported_surface"
    assert artifact["metadata"]["requested_surfaces"] == ["config"]
    assert artifact["metadata"]["routing_decision"] == "reject_without_prompt_downgrade"


def test_optimizer_applies_budget_target_without_prompt_change(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    analysis = yaml.safe_load(inputs["analysis"].read_text(encoding="utf-8"))
    analysis["issues"][0]["metadata"]["attribution"]["target_ref"] = "member_harness.policy_harness.execution_budget"
    analysis["issues"][0]["metadata"]["attribution"]["decision_contract"] = {
        "required_action": "Increase command_timeout_seconds beyond the observed task-loop timeout."
    }
    _write_yaml(inputs["analysis"], analysis)
    original_prompt = (inputs["harness"] / "system_prompt.md").read_text(encoding="utf-8")
    requests: list[str] = []

    def generate(request: str) -> dict[str, Any]:
        requests.append(request)
        return {
            "system_prompt_append": "",
            "harness_updates": {"command_timeout_seconds": 1200},
            "rationale": "The trace exhausted the current execution budget.",
            "expected_effect": "Execution reaches the required verification step.",
        }

    member_ref = asyncio.run(
        PolicyHarnessRSIOptimizer(patch_generator=generate).optimize(
            str(inputs["eval"]),
            str(inputs["analysis"]),
            str(inputs["refs"]),
            str(tmp_path / "budget-only"),
            optimization_issue_ids=["ISSUE-1"],
        )
    )

    artifact = yaml.safe_load(Path(member_ref).read_text(encoding="utf-8"))
    candidate_refs = yaml.safe_load(Path(artifact["optimized_harness_refs_path"]).read_text(encoding="utf-8"))
    candidate = Path(candidate_refs["harness_refs"]["policy_harness"])
    assert (candidate / "system_prompt.md").read_text(encoding="utf-8") == original_prompt
    assert json.loads((candidate / "harness.json").read_text(encoding="utf-8"))["command_timeout_seconds"] == 1200
    assert '"required_budget_fields": [' in requests[0]
    assert '"command_timeout_seconds"' in requests[0]
    plan = yaml.safe_load(Path(artifact["plan_path"]).read_text(encoding="utf-8"))
    assert [action["action_group"] for action in plan["actions"]] == ["config"]
    assert [action["target_path"] for action in plan["actions"]] == ["harness.json"]


def test_optimizer_rejects_adjacent_budget_when_evidence_names_exact_timeout(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    analysis = yaml.safe_load(inputs["analysis"].read_text(encoding="utf-8"))
    attribution = analysis["issues"][0]["metadata"]["attribution"]
    attribution["target_ref"] = "member_harness.policy_harness.execution_budget"
    attribution["decision_contract"] = {
        "required_action": "Increase command_timeout_seconds because that limit emitted the timeout."
    }
    _write_yaml(inputs["analysis"], analysis)

    with pytest.raises(ValueError, match="evidence-identified budget fields"):
        asyncio.run(
            PolicyHarnessRSIOptimizer(
                patch_generator=lambda _request: {
                    "system_prompt_append": "",
                    "harness_updates": {"max_steps": 420, "rollout_wall_clock_seconds": 5400},
                    "rationale": "The trace exhausted a runtime limit.",
                    "expected_effect": "Execution reaches the required verification step.",
                }
            ).optimize(
                str(inputs["eval"]),
                str(inputs["analysis"]),
                str(inputs["refs"]),
                str(tmp_path / "wrong-budget"),
                optimization_issue_ids=["ISSUE-1"],
            )
        )


def test_optimizer_applies_declared_rail_target_without_python_changes(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    analysis = yaml.safe_load(inputs["analysis"].read_text(encoding="utf-8"))
    analysis["issues"][0]["metadata"]["attribution"]["target_ref"] = (
        "member_harness.policy_harness.rail.tool_loop_compaction"
    )
    _write_yaml(inputs["analysis"], analysis)

    member_ref = asyncio.run(
        PolicyHarnessRSIOptimizer(
            patch_generator=lambda _request: {
                "system_prompt_append": "",
                "harness_updates": {
                    "tool_loop_compaction": {
                        "consecutive_threshold": 3,
                        "bailout_threshold": 2,
                    }
                },
                "rationale": "Repeated equivalent tool behavior consumed the remaining execution window.",
                "expected_effect": "The next trajectory compacts a repeated loop before the rollout stalls.",
            }
        ).optimize(
            str(inputs["eval"]),
            str(inputs["analysis"]),
            str(inputs["refs"]),
            str(tmp_path / "rail-only"),
            optimization_issue_ids=["ISSUE-1"],
        )
    )

    artifact = yaml.safe_load(Path(member_ref).read_text(encoding="utf-8"))
    candidate_refs = yaml.safe_load(Path(artifact["optimized_harness_refs_path"]).read_text(encoding="utf-8"))
    candidate = Path(candidate_refs["harness_refs"]["policy_harness"])
    candidate_config = json.loads((candidate / "harness.json").read_text(encoding="utf-8"))
    assert candidate_config["tool_loop_compaction"] == {
        "enabled": True,
        "consecutive_threshold": 3,
        "bailout_threshold": 2,
    }
    assert (candidate / "harness.py").read_bytes() == (inputs["harness"] / "harness.py").read_bytes()
    plan = yaml.safe_load(Path(artifact["plan_path"]).read_text(encoding="utf-8"))
    assert [action["action_group"] for action in plan["actions"]] == ["rail"]
    assert plan["actions"][0]["target_path"] == "harness.json"


def test_optimizer_rejects_budget_update_for_prompt_target(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)

    with pytest.raises(ValueError, match="outside the requested mutation surface"):
        asyncio.run(
            PolicyHarnessRSIOptimizer(
                patch_generator=lambda _request: {
                    "system_prompt_append": "Verify the final artifact before finishing.",
                    "harness_updates": {"max_steps": 420},
                }
            ).optimize(
                str(inputs["eval"]),
                str(inputs["analysis"]),
                str(inputs["refs"]),
                str(tmp_path / "cross-surface"),
                optimization_issue_ids=["ISSUE-1"],
            )
        )


@pytest.mark.parametrize(
    ("patch", "message"),
    [
        (
            {
                "system_prompt_append": "Verify the output before finishing.",
                "expected_effect": "The output is checked before finish.",
            },
            "rationale",
        ),
        (
            {
                "system_prompt_append": "Verify the output before finishing.",
                "rationale": "The observed run finished before verification.",
            },
            "expected_effect",
        ),
    ],
)
def test_optimizer_requires_falsifiable_candidate_contract(
    tmp_path: Path,
    patch: dict[str, Any],
    message: str,
) -> None:
    inputs = _inputs(tmp_path)

    with pytest.raises(ValueError, match=message):
        asyncio.run(
            PolicyHarnessRSIOptimizer(patch_generator=lambda _request: patch).optimize(
                str(inputs["eval"]),
                str(inputs["analysis"]),
                str(inputs["refs"]),
                str(tmp_path / "incomplete-contract"),
                optimization_issue_ids=["ISSUE-1"],
            )
        )


@pytest.mark.parametrize("field", ["system_prompt_append", "rationale", "expected_effect"])
def test_optimizer_rejects_corrupted_generated_text(tmp_path: Path, field: str) -> None:
    inputs = _inputs(tmp_path)

    def generate(_request: str) -> dict[str, Any]:
        patch = {
            "source_hypothesis_id": "h_supported",
            "system_prompt_append": "Verify the artifact before finishing.",
            "harness_updates": {},
            "rationale": "The observed run finished without verifying the artifact.",
            "expected_effect": "The next run verifies the artifact before finishing.",
        }
        patch[field] += " \ufffd"
        return patch

    with pytest.raises(ValueError, match="Unicode replacement characters"):
        asyncio.run(
            PolicyHarnessRSIOptimizer(patch_generator=generate).optimize(
                str(inputs["eval"]),
                str(inputs["analysis"]),
                str(inputs["refs"]),
                str(tmp_path / "corrupted-text"),
                optimization_issue_ids=["ISSUE-1"],
            )
        )

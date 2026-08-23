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

from examples.rsi.evobench.rsi_optimizer import PolicyHarnessRSIOptimizer


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
    assert artifact["metadata"]["improver_protocol_version"] == "generic_behavior_intervention_v3"
    assert artifact["candidate_ready_roles"] == ["policy_harness"]
    candidate_refs = yaml.safe_load(Path(artifact["optimized_harness_refs_path"]).read_text(encoding="utf-8"))
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
    assert plan["metadata"]["improver_protocol_version"] == "generic_behavior_intervention_v3"
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
    assert '"version": "generic_behavior_intervention_v3"' in requests[0]
    assert '"supported_mutation_contract"' in requests[0]
    assert '"improver_policy_directives"' in requests[0]
    assert '"version_id": "I1"' in requests[0]
    assert '"require_activation_evidence"' in requests[0]
    assert "must not override the diagnosed" in requests[0]
    assert "must transfer to more than the observed task instance" in requests[0]
    assert "do not force output content that only this case requested" in requests[0]
    assert "cohort_c001" in requests[0]
    assert "stopped before writing" in requests[0]
    assert '"sufficiency_status": "local_contributor"' in requests[0]
    assert "local_contributor is a bounded defect" in requests[0]
    assert "UNRELATED_HYPOTHESIS_MUST_NOT_LEAK" not in requests[0]


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
    _write_yaml(inputs["analysis"], analysis)
    original_prompt = (inputs["harness"] / "system_prompt.md").read_text(encoding="utf-8")

    member_ref = asyncio.run(
        PolicyHarnessRSIOptimizer(
            patch_generator=lambda _request: {
                "system_prompt_append": "",
                "harness_updates": {"max_steps": 420},
                "rationale": "The trace exhausted the current execution budget.",
                "expected_effect": "Execution reaches the required verification step.",
            }
        ).optimize(
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
    assert json.loads((candidate / "harness.json").read_text(encoding="utf-8"))["max_steps"] == 420
    plan = yaml.safe_load(Path(artifact["plan_path"]).read_text(encoding="utf-8"))
    assert [action["action_group"] for action in plan["actions"]] == ["config"]
    assert [action["target_path"] for action in plan["actions"]] == ["harness.json"]


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

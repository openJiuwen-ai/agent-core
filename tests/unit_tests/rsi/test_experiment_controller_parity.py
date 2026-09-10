# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Local experiment regressions not already covered by the interface suite."""

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from openjiuwen.rsi.harness_rsi.config import AutoCoordinatingHarnessConfig, EvaluatorConfig, MemberOptimizerConfig
from openjiuwen.rsi.harness_rsi.single_harness import (
    IterativeSingleHarnessRequest,
    SingleHarnessIterativeOptimizationOrchestrator,
)
from tests.unit_tests.rsi.test_single_harness_iterative import _Analyzer, _Evaluator, _MemberOptimizer, _write_yaml


@pytest.mark.parametrize("passed", [True, False, None])
def test_partial_score_remains_an_optimization_target(tmp_path: Path, passed: bool | None) -> None:
    from openjiuwen.rsi.harness_rsi.single_harness.iterative import _eval_score, _nonpassing_case_ids

    path = tmp_path / "eval_ref.yaml"
    _write_yaml(
        path,
        {
            "official_metrics": {"primary_score": 1.0},
            "cases": [
                {"case_id": "partial", "status": "passed", "score": 0.8, "metadata": {"evaluation_passed": passed}}
            ],
        },
    )
    assert _nonpassing_case_ids(str(path)) == {"partial"}
    assert _eval_score(path) == pytest.approx(0.8)


@pytest.mark.asyncio
@pytest.mark.parametrize("method,partial_score,partial_passed,expected", [
    ("llm_as_judge", 0.8, True, []),
    ("llm_as_judge", 0.799, False, ["partial"]),
    ("llm_as_judge", 0.965, False, ["partial"]),
    ("swebench_official", 0.8, True, ["partial"]),
])
async def test_analyzer_agrees_with_controller_pass_decision(
    tmp_path: Path, monkeypatch, method: str, partial_score: float, partial_passed: bool, expected: list[str],
) -> None:
    from openjiuwen.rsi.harness_rsi.config import EvaluationResultAnalyzerConfig
    from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import DiagnosisAgentStrategy
    from openjiuwen.rsi.harness_rsi.schema import EvaluationResultAnalysisInvocation
    from openjiuwen.rsi.harness_rsi.single_harness.iterative import _nonpassing_case_ids

    cases = tmp_path / "cases"
    case_refs = []
    for case_id, score, passed in (("partial", partial_score, partial_passed), ("complete", 1.0, True)):
        directory = cases / case_id
        directory.mkdir(parents=True)
        (directory / "result.json").write_text(
            json.dumps(
                {
                    "case_id": case_id,
                    "score": score,
                    "status": "passed" if passed else "failed",
                    "evaluation": {"passed": passed, "method": method},
                }
            ),
            encoding="utf-8",
        )
        case_refs.append({
            "case_id": case_id,
            "score": score,
            "status": "passed" if passed else "failed",
            "metadata": {"evaluation_method": method, "evaluation_passed": passed},
        })
    eval_ref = tmp_path / "eval_ref.yaml"
    _write_yaml(eval_ref, {"cases": case_refs})
    assert _nonpassing_case_ids(str(eval_ref)) == set(expected)
    strategy = DiagnosisAgentStrategy(EvaluationResultAnalyzerConfig(model_config_ref="unused-in-this-test"))
    observed = []

    async def diagnose(case_inputs, *args, **kwargs):
        observed.extend(case.case_id for case in case_inputs)
        return []

    monkeypatch.setattr(strategy, "_per_case_diagnosis", diagnose)
    await strategy.analyze(
        EvaluationResultAnalysisInvocation(
            eval_ref_path=str(eval_ref),
            case_results_dir=str(cases),
            case_traces_dir=str(cases),
            harness_refs_path="",
            team_skill_ref_path="",
            output_dir=str(tmp_path / "analysis"),
        )
    )
    assert observed == expected


@pytest.mark.parametrize("continue_locally,last_accepted", [(False, True), (True, True), (True, False)])
def test_partial_verifier_progress_reenters_analysis_with_candidate_feedback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    continue_locally: bool,
    last_accepted: bool,
) -> None:
    class FeedbackAnalyzer:
        def __init__(self) -> None:
            self.feedback: list[dict[str, Any]] = []
            self.sources = []

        async def analyze(self, invocation: Any) -> str:
            self.feedback.append(dict(invocation.prior_candidate_feedback or {}))
            self.sources.append((invocation.harness_refs_path, invocation.eval_ref_path))
            output_dir = Path(invocation.output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            analysis_ref = output_dir / "analysis_ref.yaml"
            _write_yaml(
                analysis_ref,
                {
                    "issues": [
                        {
                            "issue_id": "issue_001",
                            "category": "member_harness",
                            "severity": "high",
                            "summary": "Repair the remaining contract branch.",
                            "recommendation": "Apply the bounded repair.",
                            "affected_cases": ["case_001"],
                            "optimization_target": "member_harness",
                            "metadata": {
                                "attribution": {
                                    "target_ref": "member_harness.solver.skill",
                                    "root_cause": "the same defect remains after partial progress",
                                }
                            },
                        }
                    ]
                },
            )
            return str(analysis_ref)

    class SequentialOptimizer:
        def __init__(self) -> None:
            self.call_count = 0
            self.sources = []

        async def optimize(self, **kwargs: Any) -> str:
            self.call_count += 1
            self.sources.append(kwargs["harness_refs_path"])
            run_dir = Path(kwargs["output_dir"]) / f"run_{self.call_count}"
            run_dir.mkdir(parents=True)
            candidate_refs = run_dir / f"candidate_refs_{self.call_count}.yaml"
            _write_yaml(candidate_refs, {"harness_refs": {"solver": "candidate"}})
            issue_id = str(kwargs["optimization_issue_ids"][0])
            plan_path = run_dir / "plan.yaml"
            _write_yaml(
                plan_path,
                {
                    "targets": [
                        {
                            "role": "solver",
                            "attributed_issue_ids": [issue_id],
                        }
                    ],
                    "actions": [
                        {
                            "action_id": f"action_{self.call_count}",
                            "role": "solver",
                            "action_group": "skill",
                            "operation": "add",
                            "target_path": f"skills/repair_{self.call_count}/SKILL.md",
                            "attributed_issue_ids": [issue_id],
                        }
                    ],
                },
            )
            member_ref = run_dir / "member_ref.yaml"
            _write_yaml(
                member_ref,
                {
                    "status": "success",
                    "optimized_harness_refs_path": str(candidate_refs),
                    "plan_path": str(plan_path),
                },
            )
            return str(member_ref)

    dataset_path = tmp_path / "dataset" / "cases.json"
    dataset_path.parent.mkdir()
    dataset_path.write_text(
        json.dumps({"cases": [{"case_id": "case_001", "input": "fix"}]}),
        encoding="utf-8",
    )
    harness_refs = tmp_path / "harness_refs.yaml"
    _write_yaml(harness_refs, {"harness_refs": {"solver": "baseline"}})
    analyzer = FeedbackAnalyzer()
    optimizer = SequentialOptimizer()
    orchestrator = SingleHarnessIterativeOptimizationOrchestrator(
        AutoCoordinatingHarnessConfig(
            max_epochs=1,
            evaluator=EvaluatorConfig(backend="single_harness"),
            member_optimizer=MemberOptimizerConfig(
                max_repair_rounds_per_batch=2,
            ),
        ),
        evaluator=_Evaluator(),
        analyzer=analyzer,
        member_optimizer=optimizer,
    )
    gate_calls = 0

    async def candidate_gate(**kwargs: Any) -> dict[str, Any]:
        nonlocal gate_calls
        gate_calls += 1
        base = {
            "target_case_ids": ["case_001"],
            "capabilities": kwargs["capabilities"],
            "candidate_patch_excerpts_by_case": {},
            "candidate_failure_diagnoses": {},
        }
        if gate_calls == 1:
            candidate_eval = tmp_path / "partial_eval.yaml"
            partial_result = tmp_path / "partial_result.json"
            partial_result.write_text("{}", encoding="utf-8")
            _write_yaml(candidate_eval, {
                "harness_refs_path": kwargs["candidate_harness_refs_path"],
                "cases": [{"case_id": "case_001", "status": "failed", "score": 0.0,
                           "result_path": str(partial_result)}],
            })
            return {
                **base,
                "accepted": False,
                "status": "rejected",
                "reason": "candidate_made_partial_verifier_progress",
                "failure_class": "partial_contract_progress",
                "candidate_eval_ref_path": str(candidate_eval),
                "verifier_deltas_by_case": {
                    "case_001": {
                        "partial_progress": continue_locally,
                        "newly_passed_fail_to_pass": ["branch_a"],
                        "remaining_failed_fail_to_pass": ["branch_b"],
                    }
                },
                "candidate_patch_excerpts_by_case": {
                    "case_001": "diff --git a/module.py b/module.py",
                },
                "candidate_failure_diagnoses": {
                    "case_001": [{"root_cause": "branch_b was omitted"}],
                },
            }
        return {
            **base,
            "accepted": last_accepted,
            "status": "accepted" if last_accepted else "rejected",
            "reason": "candidate_improved_target_cases" if last_accepted else "no_improvement",
            "failure_class": "",
            "verifier_deltas_by_case": {},
        }

    monkeypatch.setattr(orchestrator, "_candidate_gate", candidate_gate)

    result = asyncio.run(
        orchestrator.run(
            IterativeSingleHarnessRequest(
                dataset_files=[str(dataset_path)],
                harness_refs_path=str(harness_refs),
                output_dir=str(tmp_path / "run"),
            )
        )
    )

    state = yaml.safe_load(Path(result.state_path).read_text(encoding="utf-8"))
    completed = state["completed_batches"]["epoch_001:batch_001"]
    assert optimizer.call_count == 2
    assert len(completed["candidate_attempts"]) == 2
    assert analyzer.feedback[0] == {"by_case": {}}
    feedback = analyzer.feedback[1]["by_case"]["case_001"][0]
    assert feedback["outcome"] == "partial_contract_progress"
    assert feedback["verifier_delta"]["remaining_failed_fail_to_pass"] == ["branch_b"]
    assert completed["candidate_attempts"][1]["accepted_target_case_ids"] == (["case_001"] if last_accepted else [])
    assert state["candidate_gates"][0]["accepted"] is False
    if continue_locally:
        assert optimizer.sources[1].endswith("candidate_refs_1.yaml")
        assert analyzer.sources[1] == (optimizer.sources[1], str(tmp_path / "partial_eval.yaml"))
        assert len(state["candidate_gates"][1]["capabilities"]) == 2
    else:
        assert optimizer.sources[1] == str(harness_refs.resolve())
    if not last_accepted:
        assert state["current_harness_refs_path"] == str(harness_refs.resolve())
    assert state["candidate_gates"][1]["primary_gate_accepted"] is last_accepted


@pytest.mark.parametrize("action_group", ["skill", "tool"])
@pytest.mark.parametrize("activation_phase", ["", "task_start", "post_diagnosis", "pre_submission"])
@pytest.mark.parametrize("later_edit", [False, True])
def test_candidate_gate_respects_capability_activation_phase(
    tmp_path: Path,
    action_group: str,
    activation_phase: str,
    later_edit: bool,
) -> None:
    class LateCapabilityEvaluator:
        async def evaluate_batch(self, **kwargs: Any) -> str:
            output_dir = Path(kwargs["output_dir"])
            case_dir = output_dir / "cases" / "case_001"
            trajectory_dir = case_dir / "tr"
            trajectory_dir.mkdir(parents=True, exist_ok=True)
            tool_name = "skill_tool" if action_group == "skill" else "patch_validator"
            call_args = {"skill_name": "patch_validator"} if action_group == "skill" else {"path": "changed.py"}
            trace = {
                "steps": [
                    {
                        "kind": "tool",
                        "error": None,
                        "detail": {
                            "tool_name": "bash",
                            "call_args": json.dumps(
                                {
                                    "command": ("python -c \"with open('changed.py', 'w') as f: f.write('patched')\""),
                                }
                            ),
                            "call_result": {"success": True},
                        },
                    },
                    {
                        "kind": "tool",
                        "error": None,
                        "detail": {
                            "tool_name": tool_name,
                            "call_args": json.dumps(call_args),
                            "call_result": {"success": True},
                        },
                    },
                ],
            }
            if later_edit:
                trace["steps"].append(
                    {
                        "kind": "tool",
                        "detail": {
                            "tool_name": "edit_file",
                            "call_args": {"path": "changed.py", "old_text": "patched", "new_text": "corrected"},
                            "call_result": {"success": True},
                        },
                    }
                )
            (trajectory_dir / "solver.jsonl").write_text(json.dumps(trace) + "\n", encoding="utf-8")
            trace_path = case_dir / "trace.json"
            trace_path.write_text(
                json.dumps({"trajectory_dir": str(trajectory_dir)}),
                encoding="utf-8",
            )
            result_path = case_dir / "result.json"
            result_path.write_text("{}", encoding="utf-8")
            eval_ref = output_dir / "eval_ref.yaml"
            _write_yaml(
                eval_ref,
                {
                    "cases": [
                        {
                            "case_id": "case_001",
                            "status": "passed",
                            "score": 1.0,
                            "result_path": str(result_path),
                            "trace_path": str(trace_path),
                        }
                    ]
                },
            )
            return str(eval_ref)

    source_eval = tmp_path / "source" / "eval_ref.yaml"
    _write_yaml(
        source_eval,
        {
            "cases": [{"case_id": "case_001", "status": "failed", "score": 0.0}],
        },
    )
    dataset_path = tmp_path / "dataset" / "cases.json"
    dataset_path.parent.mkdir()
    dataset_path.write_text(
        json.dumps({"cases": [{"case_id": "case_001", "input": "fix"}]}),
        encoding="utf-8",
    )
    orchestrator = SingleHarnessIterativeOptimizationOrchestrator(
        AutoCoordinatingHarnessConfig(
            max_epochs=1,
            evaluator=EvaluatorConfig(backend="single_harness"),
            member_optimizer=MemberOptimizerConfig(candidate_holdout_cases=0),
        ),
        evaluator=LateCapabilityEvaluator(),
        analyzer=_Analyzer(),
        member_optimizer=_MemberOptimizer(),
    )

    gate = asyncio.run(
        orchestrator._candidate_gate(
            cases=[{"case_id": "case_001", "input": "fix"}],
            source_eval_ref=str(source_eval),
            before_harness_refs_path=str(tmp_path / "baseline_refs.yaml"),
            candidate_harness_refs_path=str(tmp_path / "candidate_refs.yaml"),
            member_status="success",
            capabilities=[
                {
                    "action_group": action_group,
                    "operation": "add",
                    "runtime_name": "patch_validator",
                    "target_case_ids": ["case_001"],
                    "activation_phase": activation_phase,
                }
            ],
            output_dir=tmp_path / "candidate_eval",
            dataset=type(
                "Dataset",
                (),
                {
                    "dataset_id": "test",
                    "dataset_dir": str(dataset_path.parent),
                    "dataset_files": [str(dataset_path)],
                    "cases": 1,
                },
            )(),
        )
    )

    capability = "skill" if action_group == "skill" else "tool"
    expected = later_edit and activation_phase in {"post_diagnosis", "pre_submission"}
    assert gate["accepted"] is expected
    if expected:
        assert gate["reason"] == "candidate_improved_target_cases"
    else:
        assert gate["reason"] == f"expected_{capability}_invoked_outside_activation_window"
    assert "patch_validator" in gate[f"invoked_{capability}_names_by_case"]["case_001"]
    assert gate[f"pre_edit_invoked_{capability}_names_by_case"] == {
        "case_001": [] if action_group == "skill" else ["bash"],
    }
    assert gate["first_persistent_edit_step_by_case"] == {"case_001": 0}

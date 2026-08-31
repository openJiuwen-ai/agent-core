# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for deterministic, trial-aware analyzer evidence compression."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml


def _finance_task() -> dict:
    return {
        "id": "claw-T012_expense_report",
        "domain": "general",
        "prompt": "Submit the February expense report.",
        "metadata": {"category": "finance", "difficulty": "easy", "secret": "excluded"},
        "public_task_contract": {
            "task_id": "T012_expense_report",
            "tool_schemas": [
                {
                    "type": "function",
                    "function": {
                        "name": "finance_submit_report",
                        "description": "Submit an expense report",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "transactions": {"type": "array", "items": {"type": "string"}},
                                "total_amount": {"type": "number"},
                            },
                            "required": ["title", "transactions", "total_amount"],
                        },
                    },
                }
            ],
        },
        "scorer": {"type": "hidden_grader", "gold": "must_not_leak"},
    }


def _trial(trace_id: str, *, total: float, transactions: list[str], final_text: str) -> dict:
    return {
        "trace_id": trace_id,
        "member_role": "policy_harness",
        "messages": [
            {
                "role": "assistant",
                "message_index": 2,
                "content": f"I calculated {total} and will submit it.",
                "step_pointer": f"{trace_id}:message_2",
                "tool_calls": [
                    {
                        "name": "finance_submit_report",
                        "input": json.dumps(
                            {
                                "title": "February 2026 Expense Report",
                                "transactions": transactions,
                                "total_amount": total,
                            }
                        ),
                        "output": json.dumps(
                            {
                                "status": "submitted",
                                "report": {
                                    "title": "February 2026 Expense Report",
                                    "transactions": transactions,
                                    "total_amount": total,
                                    "content": None,
                                    "report_type": None,
                                },
                            }
                        ),
                        "error": "",
                        "step_pointer": f"{trace_id}:message_2",
                    }
                ],
            },
            {
                "role": "assistant",
                "message_index": 3,
                "content": final_text,
                "step_pointer": f"{trace_id}:message_3",
                "tool_calls": [],
            },
        ],
    }


class TestPublicTaskContract:
    def test_snapshot_keeps_public_schema_and_excludes_scorer(self) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.evidence_compactor import (
            public_task_contract_snapshot,
        )

        snapshot = public_task_contract_snapshot(_finance_task())

        assert snapshot["task_id"] == "claw-T012_expense_report"
        assert snapshot["task_metadata"] == {"category": "finance", "difficulty": "easy"}
        assert snapshot["tool_schemas"][0]["allowed_request_fields"] == [
            "title",
            "total_amount",
            "transactions",
        ]
        assert "scorer" not in snapshot
        assert "must_not_leak" not in json.dumps(snapshot)

    def test_older_run_loads_public_contract_from_materialized_suite(self, tmp_path: Path) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.evidence_compactor import (
            load_public_task_contract,
        )

        evaluation_dir = tmp_path / "evaluation"
        result_path = evaluation_dir / "cases" / "case" / "result.json"
        result_path.parent.mkdir(parents=True)
        result_path.write_text("{}", encoding="utf-8")
        official_dir = evaluation_dir / "official"
        official_dir.mkdir()
        (official_dir / "suite.json").write_text(
            json.dumps({"validation": [_finance_task()]}),
            encoding="utf-8",
        )

        contract = load_public_task_contract(
            case_id="claw-T012_expense_report",
            result_path=str(result_path),
            evaluation_metadata={},
            task_input="fallback",
        )

        assert contract["provenance"] == "official_suite.public_task_contract"
        assert contract["tool_schemas"][0]["required_request_fields"] == [
            "title",
            "total_amount",
            "transactions",
        ]


class TestCausalEvidenceDigest:
    def test_preserves_trial_outcomes_terminal_variants_and_request_contract(self) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.evidence_compactor import (
            build_causal_evidence_digest,
            public_task_contract_snapshot,
        )

        traces = [
            _trial("trial_1", total=7596.99, transactions=["txn_001", "txn_002"], final_text="Submitted 7596.99"),
            _trial("trial_2", total=4351.99, transactions=["txn_002"], final_text="Submitted 4351.99"),
            _trial("trial_3", total=7551.99, transactions=["txn_001"], final_text="Submitted 7551.99"),
        ]
        digest = build_causal_evidence_digest(
            case_id="claw-T012_expense_report",
            task_input="Submit the report",
            response="Submitted 7596.99",
            evaluation_passed=False,
            evaluation_score=0.0,
            evaluation_reason="C=0 R=0 M=0 S=0",
            evaluation_metadata={
                "trial_scores": [0.0, 0.0, 0.0],
                "trial_passed": [False, False, False],
                "trial_exit_reasons": ["finished", "finished", "finished"],
                "judge_detail": {"completion": 0.0, "safety": 0.0},
            },
            trace_data={"traces": traces},
            task_contract=public_task_contract_snapshot(_finance_task()),
        )

        observation = digest["tool_contract_observations"][0]
        assert observation["allowed_request_fields"] == ["title", "total_amount", "transactions"]
        assert "content" in observation["response_leaf_fields_not_in_public_request_schema"]
        assert "report_type" in observation["response_leaf_fields_not_in_public_request_schema"]
        assert digest["trials"][1]["score"] == 0.0
        assert digest["trials"][1]["passed"] is False
        variants = digest["cross_trial_contrast"]["terminal_action_variants"][0]["variants"]
        assert [variant["request"]["total_amount"] for variant in variants] == [7596.99, 4351.99, 7551.99]
        assert digest["compression_policy"]["trial_boundaries_preserved"] is True

    def test_preserves_per_trial_score_reason_judge_detail_and_dimension_availability(self) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.evidence_compactor import (
            build_causal_evidence_digest,
        )

        trial_details = [
            {
                "schema_version": 1,
                "trial_id": f"trial_{index}",
                "score": score,
                "passed": passed,
                "score_reason": reason,
                "judge_detail": judge_detail,
                "dimension_scores": {
                    "completion": {
                        "availability": "available",
                        "value": completion,
                        "source": "score.json.judge_detail.completion",
                    },
                    "communication": {
                        "availability": communication_state,
                        "value": communication,
                        "source": "score.json.score_reason" if communication is not None else None,
                    },
                },
                "availability": {
                    "score_file": "available",
                    "score_reason": "available",
                    "judge_detail": "available" if judge_detail else "not_present",
                    "dimension_scores": "partial",
                },
                "source": {"score_path": f"rollouts/case/trial_{index}/score.json"},
            }
            for index, score, passed, reason, completion, communication, communication_state, judge_detail in [
                (1, 0.9, True, "trial one reason", 0.9, 0.1, "available", {"completion": 0.9}),
                (2, 0.6, False, "trial two reason", 0.6, 0.2, "available", {"completion": 0.6}),
                (3, 0.8, True, "trial three reason", 0.8, None, "not_available", None),
            ]
        ]
        digest = build_causal_evidence_digest(
            case_id="case",
            task_input="Do the task",
            response="done",
            evaluation_passed=False,
            evaluation_score=0.0,
            evaluation_reason="one trial failed",
            evaluation_metadata={
                "trial_scores": [0.9, 0.6, 0.8],
                "trial_passed": [True, False, True],
                "trial_details": trial_details,
            },
            trace_data={
                "traces": [
                    _trial(f"trial_{index}", total=1.0, transactions=["a"], final_text="done") for index in range(1, 4)
                ]
            },
            task_contract={"task_id": "case", "prompt": "Do the task", "tool_schemas": []},
        )

        trial_evaluations = [trial["trial_evaluation"] for trial in digest["trials"]]
        assert [item["score_reason"] for item in trial_evaluations] == [
            "trial one reason",
            "trial two reason",
            "trial three reason",
        ]
        assert trial_evaluations[0]["judge_detail"] == {"completion": 0.9}
        assert trial_evaluations[1]["judge_detail"] == {"completion": 0.6}
        assert trial_evaluations[2]["availability"]["judge_detail"] == "not_present"
        assert trial_evaluations[2]["dimension_scores"]["communication"] == {
            "availability": "not_available",
            "value": None,
            "source": None,
        }

    def test_success_failure_output_contrast_keeps_delivery_evidence(self) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.evidence_compactor import (
            build_causal_evidence_digest,
        )

        traces = [
            _trial("trial_1", total=1.0, transactions=["a"], final_text="Full rewritten body\nwith all advice"),
            _trial("trial_2", total=1.0, transactions=["a"], final_text="Saved to outputs/blog.md"),
            _trial("trial_3", total=1.0, transactions=["a"], final_text="Another complete rewritten body"),
        ]
        digest = build_causal_evidence_digest(
            case_id="rewrite",
            task_input="Rewrite the attached post",
            response="",
            evaluation_passed=False,
            evaluation_score=0.8,
            evaluation_reason="one trial failed",
            evaluation_metadata={
                "trial_scores": [0.9, 0.7, 0.9],
                "trial_passed": [True, False, True],
            },
            trace_data={"traces": traces},
            task_contract={"task_id": "rewrite", "prompt": "Rewrite", "tool_schemas": []},
        )

        contrast = digest["cross_trial_contrast"]
        assert contrast["successful_trials"] == ["trial_1", "trial_3"]
        assert contrast["failed_trials"] == ["trial_2"]
        failed_output = next(item for item in contrast["final_output_comparison"] if item["trial_id"] == "trial_2")
        assert failed_output["artifact_mentions"] == ["outputs/blog.md"]
        failed_trial = next(item for item in digest["trials"] if item["trial_id"] == "trial_2")
        assert failed_trial["final_output"]["excerpt"] == "Saved to outputs/blog.md"

    def test_keeps_aggregate_judge_criteria_when_trial_score_file_has_no_detail(self) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.evidence_compactor import (
            build_causal_evidence_digest,
        )

        digest = build_causal_evidence_digest(
            case_id="office",
            task_input="Answer Yes or No for both documents",
            response="No and No",
            evaluation_passed=False,
            evaluation_score=0.0,
            evaluation_reason="0/2 criteria passed",
            evaluation_metadata={
                "trial_scores": [0.0],
                "trial_passed": [False],
                "trial_details": [
                    {
                        "score": 0.0,
                        "passed": False,
                        "score_reason": "0/2 criteria passed",
                        "judge_detail": None,
                        "availability": {"judge_detail": "not_present"},
                    }
                ],
                "judge_detail": {
                    "grading_run_status": "completed",
                    "criteria": [
                        {
                            "verifier_id": "criterion-1",
                            "score": 0.0,
                            "status": "ok",
                            "rationale": "The answer states the opposite conclusion.",
                        }
                    ],
                },
            },
            trace_data={"traces": [_trial("trial_1", total=1.0, transactions=["a"], final_text="No and No")]},
            task_contract={"task_id": "office", "prompt": "Answer", "tool_schemas": []},
        )

        assert digest["trials"][0]["trial_evaluation"]["judge_detail"] is None
        assert digest["outcome"]["judge_evidence"]["criteria"][0]["rationale"] == (
            "The answer states the opposite conclusion."
        )

    def test_dynamic_selection_keeps_successful_document_reads_and_response_failure(self) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.evidence_compactor import (
            build_causal_evidence_digest,
        )

        calls = [
            {
                "name": "list_files",
                "input": '{"path":"/filesystem"}',
                "output": "success=True data={'files': []}",
                "error": "",
            },
            {
                "name": "bash",
                "input": json.dumps({"command": "python3 -c \"import docx; print('ready')\""}),
                "output": "success=True data={'content': 'ready'}",
                "error": "",
            },
            {
                "name": "bash",
                "input": json.dumps({"command": "python3 extract.py template.docx"}),
                "output": "success=True data={'content': 'TEMPLATE_CONTENT " + "x" * 900 + "'}",
                "error": "",
            },
            {
                "name": "bash",
                "input": json.dumps({"command": "python3 extract.py policy.docx"}),
                "output": "success=True data={'content': 'POLICY_CONTENT " + "y" * 900 + "'}",
                "error": "",
            },
        ]
        calls.extend(
            {
                "name": "bash",
                "input": json.dumps({"command": f"echo probe-{index}"}),
                "output": f"success=True data={{'content': 'probe-{index}'}}",
                "error": "",
            }
            for index in range(10)
        )
        calls.extend(
            [
                {
                    "name": "read_file",
                    "input": '{"file_path":"/tmp/overflow.txt"}',
                    "output": "success=False data=None error='Access denied: outside sandbox'",
                    "error": "",
                },
                {
                    "name": "todo_modify",
                    "input": '{"action":"update"}',
                    "output": "success=True",
                    "error": "",
                },
                {
                    "name": "bash",
                    "input": json.dumps({"command": "echo final-check"}),
                    "output": "success=True data={'content': 'done'}",
                    "error": "",
                },
            ]
        )
        messages = [
            {
                "role": "assistant",
                "message_index": index,
                "content": "",
                "tool_calls": [{**call, "step_pointer": f"trial_1:message_{index}"}],
            }
            for index, call in enumerate(calls)
        ]
        digest = build_causal_evidence_digest(
            case_id="office",
            task_input="Review the documents",
            response="done",
            evaluation_passed=False,
            evaluation_score=0.0,
            evaluation_reason="failed",
            evaluation_metadata={"trial_scores": [0.0], "trial_passed": [False]},
            trace_data={"traces": [{"trace_id": "trial_1", "messages": messages}]},
            task_contract={"task_id": "office", "prompt": "Review", "tool_schemas": []},
        )

        trial = digest["trials"][0]
        selected = trial["selected_actions"]
        assert any("TEMPLATE_CONTENT" in str(item.get("response")) for item in selected)
        assert any("POLICY_CONTENT" in str(item.get("response")) for item in selected)
        failed = next(item for item in selected if item["tool"] == "read_file")
        assert "observed_failure" in failed["selection_reasons"]
        coverage = trial["selection_coverage"]
        assert coverage["failed_call_count"] == coverage["selected_failed_call_count"] == 1
        assert coverage["content_evidence_call_count"] == coverage["selected_content_evidence_call_count"] == 2
        assert coverage["selected_count"] > 8

    def test_compaction_marker_cannot_hide_failed_requirement_linked_source_text(self) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.evidence_compactor import (
            build_causal_evidence_digest,
        )

        clause = (
            "Exercise. Written notice must be provided no earlier than twelve (12) months "
            "and no later than nine (9) months prior to the expiration of the previous Term."
        )
        raw_output = "document preface\n" + "x" * 3_000 + "\n" + clause + "\n" + "signature " + "y" * 2_000
        trace = {
            "trace_id": "trial_1",
            "messages": [
                {
                    "role": "assistant",
                    "message_index": 15,
                    "content": "Read the controlling lease clause.",
                    "tool_calls": [
                        {
                            "name": "bash",
                            "input": json.dumps({"command": "pdftotext amendment.pdf -"}),
                            "output": raw_output,
                            "error": "",
                            "step_pointer": "trial_1:message_15",
                        }
                    ],
                }
            ],
        }
        digest = build_causal_evidence_digest(
            case_id="lease",
            task_input="Read the lease and report the notice window.",
            response="nine months prior",
            evaluation_passed=False,
            evaluation_score=0.0,
            evaluation_reason="one criterion failed",
            evaluation_metadata={
                "trial_scores": [0.0],
                "trial_passed": [False],
                "judge_evidence": {
                    "availability": "available",
                    "criteria": [
                        {
                            "criterion_id": "notice-window",
                            "score": 0.0,
                            "rationale": (
                                "The response says nine months prior, while the criterion expects "
                                "nine months following November 1, 2030."
                            ),
                        }
                    ],
                },
            },
            trace_data={"traces": [trace]},
            task_contract={"task_id": "lease", "prompt": "Read the lease", "tool_schemas": []},
        )

        action = digest["trials"][0]["selected_actions"][0]
        assert "ANALYZER_EVIDENCE_COMPACTION" in action["response"]
        evidence = action["response_evidence"]
        assert evidence["task_agent_observed_display_omission_marker"] is False
        assert evidence["display_omission_origin"] == "analyzer_evidence_compactor"
        exact_spans = "\n".join(span["text"] for span in evidence["critical_spans"])
        assert clause in exact_spans
        assert "following" in digest["critical_evidence_terms"]
        assert "prior" in digest["critical_evidence_terms"]


class TestCandidateFeedbackCompression:
    def test_preserves_prediction_activation_and_observed_score_delta(self) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.evidence_compactor import (
            compact_candidate_feedback,
        )

        compact = compact_candidate_feedback(
            {
                "case_id": "case_1",
                "experiments": [
                    {
                        "experiment_id": "exp_1",
                        "surface": "prompt",
                        "predicted_rank": 1,
                        "predicted_score": 125.0,
                        "status": "rejected",
                        "reason": "no improvement",
                        "source_target_score": 0.4,
                        "candidate_target_score": 0.6,
                        "target_score_delta": 0.2,
                        "selected_for_promotion": False,
                        "activation": {"state": "observed"},
                        "causal_intervention_contracts": [
                            {
                                "predicted_behavior_and_outcome": "behavior changes and target passes",
                                "prediction_recorded_before_evaluation": True,
                            }
                        ],
                        "verifier_delta": {"partial_progress": True},
                        "candidate_failure_diagnoses": [
                            {
                                "prior_experiment_assessment": {
                                    "availability": "available",
                                    "causal_hypothesis_status": "falsified",
                                }
                            }
                        ],
                    }
                ],
            }
        )

        experiment = compact["experiments"][0]
        assert experiment["predicted_score"] == 125.0
        assert experiment["activation"] == {"state": "observed"}
        assert experiment["observed_outcome"]["source_target_score"] == 0.4
        assert experiment["observed_outcome"]["candidate_target_score"] == 0.6
        assert experiment["observed_outcome"]["target_score_delta"] == 0.2
        assert experiment["causal_intervention_contracts"][0]["prediction_recorded_before_evaluation"] is True
        assert (
            experiment["candidate_failure_diagnoses"][0]["prior_experiment_assessment"]["causal_hypothesis_status"]
            == "falsified"
        )

    def test_normalizes_candidate_gate_feedback_without_losing_continuous_progress(self) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.evidence_compactor import (
            compact_candidate_feedback,
        )

        compact = compact_candidate_feedback(
            {
                "case_id": "case_1",
                "experiments": [
                    {
                        "schema_version": 2,
                        "experiment_id": "exp_2",
                        "prediction": {
                            "candidate_patch_excerpt": "Require a final verification pass.",
                            "causal_intervention_contracts": [
                                {
                                    "source_causal_hypothesis_id": "h1",
                                    "predicted_behavior_and_outcome": "five remaining checks pass",
                                }
                            ],
                        },
                        "activation": {
                            "availability": "observed",
                            "state": "triggered",
                            "trigger_rate": 1.0,
                            "delivery": {"availability": "observed", "state": "executed"},
                            "behavior_activation": {"availability": "observed", "state": "triggered"},
                        },
                        "observed_outcome": {
                            "status": "rejected",
                            "reason": "strict target still failed",
                            "strict_score": {"source": 0.0, "candidate": 0.0, "delta": 0.0},
                            "continuous_score": {
                                "source": 3 / 9,
                                "candidate": 8 / 9,
                                "delta": 5 / 9,
                                "source_signal": "official_judge",
                                "candidate_signal": "official_judge",
                                "role": "diagnostic_only",
                            },
                            "requirement_delta": {
                                "newly_passed_requirements": ["r2", "r3", "r4", "r5", "r6"],
                                "remaining_failed_requirements": ["r9"],
                            },
                            "dimension_deltas": {"accuracy": 5 / 9},
                            "selected_for_promotion": False,
                        },
                    }
                ],
            }
        )

        experiment = compact["experiments"][0]
        assert experiment["schema_version"] == 2
        assert experiment["prediction"]["causal_intervention_contracts"][0]["source_causal_hypothesis_id"] == "h1"
        assert experiment["activation"]["state"] == "triggered"
        assert experiment["activation"]["delivery"] == {
            "availability": "observed",
            "state": "executed",
        }
        assert experiment["activation"]["behavior_activation"]["state"] == "triggered"
        assert experiment["observed_outcome"]["strict_score"] == {
            "source": 0.0,
            "candidate": 0.0,
            "delta": 0.0,
        }
        assert experiment["observed_outcome"]["continuous_score"]["delta"] == pytest.approx(5 / 9)
        assert experiment["observed_outcome"]["source_native_score"] == pytest.approx(3 / 9)
        assert experiment["observed_outcome"]["native_score_delta"] == pytest.approx(5 / 9)
        assert experiment["observed_outcome"]["requirement_delta"]["remaining_failed_requirements"] == ["r9"]
        assert experiment["verifier_delta"]["newly_passed_requirements"] == ["r2", "r3", "r4", "r5", "r6"]


class TestAnalyzerIntegration:
    def test_diagnosis_input_uses_causal_digest_as_primary_evidence(self, tmp_path: Path) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
            _build_diagnosis_input_json,
        )
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.case_reader import (
            CaseAnalysisInput,
            DeterministicSignals,
        )

        evaluation_dir = tmp_path / "evaluation"
        case_dir = evaluation_dir / "cases" / "case"
        judge_dir = case_dir / "judge"
        judge_dir.mkdir(parents=True)
        result_path = case_dir / "result.json"
        result_path.write_text("{}", encoding="utf-8")
        (judge_dir / "normalized_trace.json").write_text(
            json.dumps(
                {
                    "traces": [
                        _trial(
                            "trial_1",
                            total=7596.99,
                            transactions=["txn_001", "txn_002"],
                            final_text="Submitted 7596.99",
                        )
                    ]
                }
            ),
            encoding="utf-8",
        )
        official_dir = evaluation_dir / "official"
        official_dir.mkdir()
        (official_dir / "suite.json").write_text(
            json.dumps({"validation": [_finance_task()]}),
            encoding="utf-8",
        )
        case = CaseAnalysisInput(
            case_id="claw-T012_expense_report",
            status="failed",
            score=0.0,
            input="Submit the report",
            expected=None,
            response="Submitted 7596.99",
            error="",
            evaluation_method="evobench-claw-official",
            evaluation_passed=False,
            evaluation_reason="C=0 R=0 M=0 S=0",
            evaluation_metadata={
                "trial_scores": [0.0],
                "trial_passed": [False],
            },
            trace_path=str(case_dir / "trace.json"),
            result_path=str(result_path),
        )

        payload = json.loads(
            _build_diagnosis_input_json(
                case=case,
                signals=DeterministicSignals(method="evobench-claw-official"),
                retrieved_experience=None,
                evidence_summary_available=True,
            )
        )

        digest = payload["primary_evidence"]["causal_digest"]
        submit = next(item for item in digest["tool_contract_observations"] if item["tool"] == "finance_submit_report")
        assert submit["allowed_request_fields"] == ["title", "total_amount", "transactions"]
        assert "content" in submit["response_leaf_fields_not_in_public_request_schema"]
        assert "response_excerpt" not in payload["fallback_excerpts"]

    def test_diagnosis_input_includes_official_criteria_effective_harness_and_workspace_index(
        self,
        tmp_path: Path,
    ) -> None:
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
            _build_diagnosis_input_json,
            _load_effective_harness_snapshot,
            _summarize_evaluation_metadata,
        )
        from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.case_reader import (
            CaseAnalysisInput,
            DeterministicSignals,
        )

        harness = tmp_path / "harness"
        harness.mkdir()
        (harness / "harness.json").write_text(
            json.dumps(
                {
                    "name": "office-agent",
                    "system_prompt": "system_prompt.md",
                    "max_steps": 120,
                    "api_key": "must-not-leak",
                }
            ),
            encoding="utf-8",
        )
        (harness / "system_prompt.md").write_text(
            "Read the real document and verify the final conclusion.",
            encoding="utf-8",
        )
        (harness / "harness.py").write_text("TOOLS = ['bash', 'read_file']\n", encoding="utf-8")
        refs = tmp_path / "harness_refs.yaml"
        refs.write_text(
            yaml.safe_dump({"harness_refs": {"policy_harness": str(harness)}}),
            encoding="utf-8",
        )

        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "answer.docx").write_bytes(b"document")
        case_dir = tmp_path / "cases" / "case"
        (case_dir / "judge").mkdir(parents=True)
        result_path = case_dir / "result.json"
        result_path.write_text(json.dumps({"workspace_dir": str(workspace)}), encoding="utf-8")
        (case_dir / "judge" / "normalized_trace.json").write_text(
            json.dumps({"traces": [_trial("trial_1", total=1.0, transactions=["a"], final_text="No")]}),
            encoding="utf-8",
        )
        metadata = {
            "trial_scores": [0.0],
            "trial_passed": [False],
            "judge_detail": {
                "grading_run_status": "completed",
                "criteria": [
                    {
                        "verifier_id": "verdict",
                        "score": 0.0,
                        "status": "ok",
                        "rationale": "The answer says No but the required conclusion is Yes.",
                    }
                ],
            },
        }
        case = CaseAnalysisInput(
            case_id="case",
            status="failed",
            score=0.0,
            input="Answer Yes or No",
            expected=None,
            response="No",
            error="",
            evaluation_method="evobench-claw-official",
            evaluation_passed=False,
            evaluation_reason="0/1",
            evaluation_metadata=metadata,
            trace_path=str(case_dir / "trace.json"),
            result_path=str(result_path),
        )

        snapshot = _load_effective_harness_snapshot(str(refs))
        payload = json.loads(
            _build_diagnosis_input_json(
                case=case,
                signals=DeterministicSignals(method="evobench-claw-official"),
                retrieved_experience=None,
                evidence_summary_available=False,
                effective_harness=snapshot,
            )
        )

        assert _summarize_evaluation_metadata(metadata)["criteria"][0]["criterion_id"] == "verdict"
        role = payload["effective_harness"]["roles"][0]
        assert role["config"]["max_steps"] == 120
        assert "api_key" not in role["config"]
        assert "verify the final conclusion" in role["effective_system_prompt"]["content"]
        assert payload["workspace_evidence"]["artifact_files"][0]["path"] == "answer.docx"
        assert payload["case_facts"]["judge_breakdown"]["criteria"][0]["score"] == 0.0

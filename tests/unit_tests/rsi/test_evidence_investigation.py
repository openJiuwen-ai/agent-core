# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from openjiuwen.rsi.evaluation_result_analyzer.case_reader import CaseAnalysisInput, DeterministicSignals
from openjiuwen.rsi.evaluation_result_analyzer.evidence_investigation import (
    execute_causal_investigation,
    normalize_causal_investigation,
)


def _case(tmp_path: Path, *, metadata: dict[str, Any] | None = None) -> CaseAnalysisInput:
    case_dir = tmp_path / "case_001"
    judge_dir = case_dir / "judge"
    judge_dir.mkdir(parents=True)
    result_path = case_dir / "result.json"
    result_path.write_text(
        json.dumps({"evaluation": {"metadata": metadata or {}}}),
        encoding="utf-8",
    )
    (judge_dir / "normalized_trace.json").write_text(
        json.dumps(
            {
                "traces": [
                    {
                        "trace_id": "case_001:trial_1",
                        "messages": [
                            {
                                "role": "assistant",
                                "message_index": 7,
                                "step_pointer": "trial_1:message_7",
                                "content": "I need to distinguish the parser and routing hypotheses.",
                                "tool_calls": [
                                    {
                                        "name": "read_file",
                                        "input": '{"path":"public.txt"}',
                                        "output": "prefix\nEXACT_DISCRIMINATOR parser selected legacy mode\nsuffix",
                                        "error": "",
                                    }
                                ],
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return CaseAnalysisInput(
        case_id="case_001",
        status="failed",
        score=0.0,
        input="Complete the public task.",
        expected=None,
        response="done",
        error="",
        evaluation_method="script_based",
        evaluation_passed=False,
        evaluation_reason="failed",
        evaluation_metadata=metadata or {},
        trace_path=str(case_dir / "trace.json"),
        result_path=str(result_path),
    )


def test_normalize_plan_rejects_shell_and_arbitrary_path_requests() -> None:
    plan = normalize_causal_investigation(
        {
            "causal_investigation": {
                "hypotheses": [
                    {
                        "hypothesis_id": "h_parser",
                        "claim": "The parser selected a legacy mode.",
                        "explains_requirement_ids": ["criterion:parser"],
                        "falsified_if": "The trace shows the modern parser was selected.",
                        "evidence_requests": [
                            {
                                "operation": "search_trace",
                                "query": "parser selected mode",
                                "purpose": "observe the selected parser",
                            },
                            {"operation": "shell", "query": "find /"},
                            {"operation": "read_event", "path": "/secret", "message_index": 7},
                        ],
                    }
                ]
            }
        },
        failed_requirement_ids=["criterion:parser"],
    )

    assert plan is not None
    assert [item["operation"] for item in plan["evidence_requests"]] == ["search_trace", "read_event"]
    assert all("path" not in item for item in plan["evidence_requests"])
    assert plan["hypotheses"][0]["explains_requirement_ids"] == ["criterion:parser"]


def test_strict_plan_requires_competing_hypotheses_and_evidence_for_each() -> None:
    single = normalize_causal_investigation(
        {
            "hypotheses": [
                {
                    "hypothesis_id": "h1",
                    "claim": "Only one explanation was proposed.",
                    "falsified_if": "A different mechanism is observed.",
                    "evidence_requests": [{"operation": "search_trace", "query": "mechanism"}],
                }
            ]
        },
        min_hypotheses=2,
        require_evidence_per_hypothesis=True,
    )
    uncovered = normalize_causal_investigation(
        {
            "hypotheses": [
                {
                    "hypothesis_id": "h1",
                    "claim": "The parser selected the wrong mode.",
                    "falsified_if": "The expected parser was selected.",
                    "evidence_requests": [{"operation": "search_trace", "query": "parser"}],
                },
                {
                    "hypothesis_id": "h2",
                    "claim": "The route selected the wrong handler.",
                    "falsified_if": "The expected handler was selected.",
                    "evidence_requests": [],
                },
            ]
        },
        min_hypotheses=2,
        require_evidence_per_hypothesis=True,
    )
    duplicate = normalize_causal_investigation(
        {
            "hypotheses": [
                {
                    "hypothesis_id": hypothesis_id,
                    "claim": "The parser selected the wrong mode.",
                    "falsified_if": "The expected parser was selected.",
                    "evidence_requests": [{"operation": "search_trace", "query": "parser"}],
                }
                for hypothesis_id in ("h1", "h2")
            ]
        },
        min_hypotheses=2,
        require_evidence_per_hypothesis=True,
    )

    assert single is None
    assert uncovered is None
    assert duplicate is None


def test_controller_searches_and_reads_only_public_case_events(tmp_path: Path) -> None:
    case = _case(tmp_path)
    plan = normalize_causal_investigation(
        {
            "hypotheses": [
                {
                    "hypothesis_id": "h_parser",
                    "claim": "The parser selected a legacy mode.",
                    "falsified_if": "The modern parser was selected.",
                    "evidence_requests": [
                        {"operation": "search_trace", "query": "EXACT_DISCRIMINATOR parser"},
                        {
                            "operation": "read_event",
                            "trace_id": "case_001:trial_1",
                            "message_index": 7,
                        },
                    ],
                }
            ]
        }
    )
    assert plan is not None

    evidence = execute_causal_investigation(case, plan)

    assert evidence["policy"]["arbitrary_shell_or_path_access"] is False
    assert evidence["completed_request_count"] == 2
    search = evidence["results"][0]
    assert search["availability"] == "available"
    assert "EXACT_DISCRIMINATOR" in search["events"][0]["tool_calls"][0]["output_spans"][0]["text"]
    event = evidence["results"][1]["event"]
    assert event["tool_calls"][0]["output"]["complete"] is True
    assert "legacy mode" in event["tool_calls"][0]["output"]["text"]


def test_read_event_requires_trace_id_when_message_index_is_ambiguous(tmp_path: Path) -> None:
    case = _case(tmp_path)
    trace_path = Path(case.result_path).parent / "judge" / "normalized_trace.json"
    trace_path.write_text(
        json.dumps(
            {
                "traces": [
                    {"trace_id": "trial_1", "messages": [{"message_index": 7, "content": "first"}]},
                    {"trace_id": "trial_2", "messages": [{"message_index": 7, "content": "second"}]},
                ]
            }
        ),
        encoding="utf-8",
    )
    plan = normalize_causal_investigation(
        {
            "hypotheses": [
                {
                    "claim": "One trial contains the decisive event.",
                    "falsified_if": "No trial contains it.",
                    "evidence_requests": [{"operation": "read_event", "message_index": 7}],
                }
            ]
        }
    )

    assert plan is not None
    result = execute_causal_investigation(case, plan)["results"][0]
    assert result["availability"] == "ambiguous"
    assert result["candidate_trace_ids"] == ["trial_1", "trial_2"]


def test_controller_searches_bounded_repository_and_rejects_traversal(tmp_path: Path) -> None:
    case = _case(tmp_path)
    evidence_root = tmp_path / "diagnosis"
    source = evidence_root / "repository" / "src" / "router.py"
    source.parent.mkdir(parents=True)
    source.write_text("def route(mode):\n    return 'legacy' if mode is None else mode\n", encoding="utf-8")
    plan = normalize_causal_investigation(
        {
            "hypotheses": [
                {
                    "claim": "The router defaults to legacy mode.",
                    "falsified_if": "The router defaults to the modern mode.",
                    "evidence_requests": [
                        {"operation": "search_repository", "query": "router legacy"},
                        {"operation": "read_repository_file", "relative_path": "src/router.py"},
                        {"operation": "read_repository_file", "relative_path": "../secret.txt"},
                    ],
                }
            ]
        }
    )

    assert plan is not None
    assert [request["operation"] for request in plan["evidence_requests"]] == [
        "search_repository",
        "read_repository_file",
    ]
    results = execute_causal_investigation(case, plan, evidence_root=evidence_root)["results"]
    assert results[0]["availability"] == "available"
    assert results[0]["files"][0]["relative_path"] == "src/router.py"
    assert results[1]["availability"] == "available"
    assert "return 'legacy'" in results[1]["content"]["text"]


def test_controller_inspects_xlsx_formulas_as_structured_artifact(tmp_path: Path) -> None:
    from openpyxl import Workbook

    case = _case(tmp_path)
    artifact = Path(case.result_path).parent / "artifacts" / "result.xlsx"
    artifact.parent.mkdir()
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Summary"
    sheet["A1"] = 10
    sheet["A2"] = 20
    sheet["A3"] = "=SUM(A1:A2)"
    workbook.save(artifact)
    plan = normalize_causal_investigation(
        {
            "hypotheses": [
                {
                    "claim": "The workbook contains a total formula.",
                    "falsified_if": "The formula is absent.",
                    "evidence_requests": [{"operation": "inspect_artifact", "query": "SUM A1 A2"}],
                }
            ]
        }
    )

    assert plan is not None
    result = execute_causal_investigation(case, plan)["results"][0]
    assert result["availability"] == "available"
    assert result["matches"][0]["source"] == "artifacts/result.xlsx"
    assert any("SUM(A1:A2)" in span["text"] for span in result["matches"][0]["exact_spans"])


def test_controller_compares_prior_experiment_without_zero_filling(tmp_path: Path) -> None:
    case = _case(tmp_path, metadata={"judge_detail": {"reason": "artifact contract mismatch"}})
    plan = normalize_causal_investigation(
        {
            "hypotheses": [
                {
                    "hypothesis_id": "h_contract",
                    "claim": "The artifact contract was not satisfied.",
                    "falsified_if": "The evaluator reports the contract was satisfied.",
                    "evidence_requests": [
                        {"operation": "inspect_artifact", "query": "artifact contract mismatch"},
                        {"operation": "compare_runs", "query": "predicted behavior"},
                    ],
                }
            ]
        }
    )
    assert plan is not None

    evidence = execute_causal_investigation(
        case,
        plan,
        prior_candidate_feedback={
            "experiments": [
                {
                    "causal_intervention_contracts": [
                        {"predicted_behavior_and_outcome": "predicted behavior creates artifact"}
                    ],
                    "candidate_target_score": None,
                }
            ]
        },
    )

    assert evidence["results"][0]["availability"] == "available"
    comparison = evidence["results"][1]
    assert comparison["availability"] == "available"
    assert "predicted behavior" in comparison["paired_feedback"]["exact_spans"][0]["text"]
    assert "null" in comparison["paired_feedback"]["exact_spans"][0]["text"]


def test_controller_checks_numeric_relation_without_executing_code(tmp_path: Path) -> None:
    case = _case(tmp_path)
    plan = normalize_causal_investigation(
        {
            "hypotheses": [
                {
                    "hypothesis_id": "h_delta",
                    "claim": "The candidate changed the value by one percentage point.",
                    "falsified_if": "The computed delta is not 0.01.",
                    "evidence_requests": [
                        {
                            "operation": "check_relation",
                            "expression": "(-0.02 + 0.01) - (-0.02)",
                            "operator": "approximately_equal",
                            "expected": 0.01,
                        },
                        {
                            "operation": "check_relation",
                            "expression": "__import__('os').system('whoami')",
                            "expected": 0,
                        },
                    ],
                }
            ]
        }
    )
    assert plan is not None

    evidence = execute_causal_investigation(case, plan)

    assert evidence["results"][0]["availability"] == "available"
    assert evidence["results"][0]["value"] == pytest.approx(0.01)
    assert evidence["results"][0]["holds"] is True
    assert evidence["results"][1]["availability"] == "invalid"


def test_controller_compares_candidate_delta_against_baseline(tmp_path: Path) -> None:
    case = _case(tmp_path)
    plan = normalize_causal_investigation(
        {
            "hypotheses": [
                {
                    "hypothesis_id": "h_delta",
                    "claim": "The candidate increased the existing adjustment by one percentage point.",
                    "falsified_if": "The after-before delta is not 0.01.",
                    "evidence_requests": [
                        {
                            "operation": "compare_numeric_change",
                            "before_expression": "-0.02",
                            "after_expression": "-0.02 + 0.01",
                            "expected_delta": 0.01,
                        }
                    ],
                }
            ]
        }
    )
    assert plan is not None

    result = execute_causal_investigation(case, plan)["results"][0]

    assert result["availability"] == "available"
    assert result["before_value"] == pytest.approx(-0.02)
    assert result["after_value"] == pytest.approx(-0.01)
    assert result["computed_delta"] == pytest.approx(0.01)
    assert result["holds"] is True


def test_unresolved_material_hypothesis_cannot_assign_optimization_target() -> None:
    from openjiuwen.rsi.evaluation_result_analyzer.analyzer import _causal_investigation_conflicts

    conflicts = _causal_investigation_conflicts(
        [
            {
                "evidence_status": "supported_hypothesis",
                "target_ref": "member_harness.solver.prompt",
                "hypothesis_assessment": [
                    {
                        "hypothesis_id": "h1",
                        "status": "unresolved",
                        "falsifying_condition_status": "unknown",
                        "claim_follows_from_evidence": "unknown",
                        "logic_check": "The requested discriminator was unavailable.",
                        "controller_request_ids": [],
                    }
                ],
            }
        ],
        {
            "hypotheses": [
                {
                    "hypothesis_id": "h1",
                    "claim": "A parser mismatch caused the failure.",
                    "falsified_if": "The expected parser was selected.",
                }
            ],
            "evidence_requests": [],
        },
        evidence_results={"results": []},
        prior_candidate_feedback=None,
    )

    assert "unresolved material hypotheses require target_ref=unassigned" in " ".join(conflicts)


def test_falsified_prior_causal_hypothesis_cannot_be_reused() -> None:
    from openjiuwen.rsi.evaluation_result_analyzer.analyzer import _causal_investigation_conflicts

    conflicts = _causal_investigation_conflicts(
        [
            {
                "evidence_status": "confirmed",
                "target_ref": "member_harness.solver.prompt",
                "hypothesis_assessment": [
                    {
                        "hypothesis_id": "h_failed_before",
                        "status": "supported",
                        "falsifying_condition_status": "not_observed",
                        "claim_follows_from_evidence": "yes",
                        "logic_check": "The current response tried to support the same explanation again.",
                        "controller_request_ids": ["q1"],
                    }
                ],
                "prior_experiment_assessment": {
                    "availability": "available",
                    "causal_hypothesis_status": "falsified",
                },
            }
        ],
        {
            "hypotheses": [
                {
                    "hypothesis_id": "h_failed_before",
                    "claim": "The previous causal explanation.",
                    "falsified_if": "Its intervention activates without the predicted outcome.",
                }
            ],
            "evidence_requests": [
                {
                    "request_id": "q1",
                    "hypothesis_ids": ["h_failed_before"],
                    "operation": "compare_runs",
                }
            ],
        },
        evidence_results={"results": [{"request_id": "q1", "availability": "available"}]},
        prior_candidate_feedback={
            "experiments": [{"causal_intervention_contracts": [{"source_causal_hypothesis_id": "h_failed_before"}]}]
        },
    )

    assert "falsified prior causal hypotheses were reused: h_failed_before" in " ".join(conflicts)


@pytest.mark.asyncio
async def test_diagnosis_runs_plan_then_controller_evidence_then_final(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openjiuwen.rsi.config import EvaluationResultAnalyzerConfig
    from openjiuwen.rsi.evaluation_result_analyzer import analyzer as analyzer_module

    case = _case(tmp_path)
    strategy = analyzer_module.DiagnosisAgentStrategy(EvaluationResultAnalyzerConfig(model_config_ref="unused.yaml"))
    prompts: list[str] = []
    diagnosis_calls = 0

    async def fake_build_agent(workspace: str) -> dict[str, str]:
        return {"workspace": workspace}

    async def fake_run_agent(agent: Any, prompt: str, *, max_retries: int) -> str:
        nonlocal diagnosis_calls
        del agent, max_retries
        prompts.append(prompt)
        if prompt.startswith("CAUSAL_INVESTIGATION_PHASE=plan"):
            return json.dumps(
                {
                    "causal_investigation": {
                        "hypotheses": [
                            {
                                "hypothesis_id": "h_parser",
                                "claim": "Legacy parser selection caused the failure.",
                                "falsified_if": "The modern parser was selected.",
                                "evidence_requests": [
                                    {"operation": "search_trace", "query": "EXACT_DISCRIMINATOR parser"}
                                ],
                            },
                            {
                                "hypothesis_id": "h_route",
                                "claim": "Routing selected the wrong handler.",
                                "falsified_if": "The expected handler processed the request.",
                                "evidence_requests": [{"operation": "search_trace", "query": "handler routing"}],
                            },
                        ]
                    }
                }
            )
        if prompt.startswith("CAUSAL_INVESTIGATION_PHASE=refine"):
            return json.dumps(
                {
                    "causal_investigation": {
                        "evidence_requests": [
                            {
                                "operation": "read_event",
                                "trace_id": "case_001:trial_1",
                                "message_index": 7,
                            }
                        ]
                    }
                }
            )
        assert "EXACT_DISCRIMINATOR" in prompt
        diagnosis_calls += 1
        if diagnosis_calls == 1:
            return json.dumps(
                {
                    "diagnoses": [
                        {
                            "issue_category": "unassigned",
                            "severity": "low",
                            "summary": "The available evidence does not yet distinguish parser and routing.",
                            "failure_mode": "unresolved_parser_or_route",
                            "evidence_status": "insufficient",
                            "root_cause": "The mechanism is unresolved.",
                            "target_ref": "unassigned",
                            "confidence": "low",
                            "hypothesis_assessment": [
                                {"hypothesis_id": "h_parser", "status": "unresolved"},
                                {"hypothesis_id": "h_route", "status": "unresolved"},
                            ],
                        }
                    ]
                }
            )
        return json.dumps(
            {
                "diagnoses": [
                    {
                        "issue_category": "member_harness",
                        "severity": "medium",
                        "summary": "The legacy parser was selected.",
                        "failure_mode": "legacy_parser_selection",
                        "failure_cluster": {"failed_checks": [], "observable_behavior": "legacy mode selected"},
                        "evidence_status": "confirmed",
                        "failed_requirement": "public task failed",
                        "competing_hypotheses": ["parser selection", "routing"],
                        "discriminating_evidence": "The trace explicitly records legacy parser selection.",
                        "root_cause": "Legacy parser selection caused the observed behavior.",
                        "critical_mistake": "The runtime selected legacy mode.",
                        "general_mechanism": "Select the parser compatible with the public input contract.",
                        "target_ref": "member_harness.solver.prompt",
                        "evidence_refs": [{"trace_id": "case_001:trial_1", "message_index": 7}],
                        "affected_components": ["solver"],
                        "recommendation": "Change parser selection behavior.",
                        "hypothesis_assessment": [
                            {
                                "hypothesis_id": "h_parser",
                                "status": "supported",
                                "falsifying_condition_status": "not_observed",
                                "claim_follows_from_evidence": "yes",
                                "logic_check": "The observed mode equals the legacy mode predicted by h_parser.",
                                "controller_request_ids": ["q1"],
                                "reason": "observed",
                                "evidence_refs": [],
                            },
                            {
                                "hypothesis_id": "h_route",
                                "status": "falsified",
                                "falsifying_condition_status": "observed",
                                "claim_follows_from_evidence": "no",
                                "logic_check": "No routing mismatch was observed.",
                                "controller_request_ids": [],
                                "reason": "not observed",
                                "evidence_refs": [],
                            },
                        ],
                        "prior_experiment_assessment": {
                            "availability": "not_available",
                            "intervention_activated": "unknown",
                            "predicted_behavior_occurred": "unknown",
                            "predicted_outcome_occurred": "unknown",
                            "causal_hypothesis_status": "not_tested",
                            "reason": "no prior experiment",
                        },
                        "confidence": "high",
                    }
                ]
            }
        )

    monkeypatch.setattr(strategy, "_build_agent", fake_build_agent)
    monkeypatch.setattr(analyzer_module, "_run_agent", fake_run_agent)
    monkeypatch.setattr(analyzer_module, "_case_diagnoses_validation_conflicts", lambda *args, **kwargs: [])

    results = await strategy._per_case_diagnosis(
        [case],
        DeterministicSignals(method="script_based"),
        None,
    )

    assert len(prompts) == 4
    assert prompts[1].startswith("CAUSAL_INVESTIGATION_PHASE=diagnose")
    assert prompts[2].startswith("CAUSAL_INVESTIGATION_PHASE=refine")
    assert prompts[3].startswith("CAUSAL_INVESTIGATION_PHASE=diagnose")
    assert results[0]["causal_investigation"]["planning_status"] == "completed"
    assert results[0]["causal_investigation"]["refinement_status"] == "completed"
    assert results[0]["causal_investigation"]["refinement_request_count"] == 1
    assert results[0]["causal_investigation"]["hypothesis_count"] == 2
    assert results[0]["hypothesis_assessment"][1]["status"] == "falsified"


@pytest.mark.asyncio
async def test_legacy_diagnosis_cannot_bypass_mandatory_investigation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openjiuwen.rsi.config import EvaluationResultAnalyzerConfig
    from openjiuwen.rsi.evaluation_result_analyzer import analyzer as analyzer_module

    case = _case(tmp_path)
    strategy = analyzer_module.DiagnosisAgentStrategy(EvaluationResultAnalyzerConfig(model_config_ref="unused.yaml"))
    prompts: list[str] = []

    async def fake_build_agent(workspace: str) -> dict[str, str]:
        return {"workspace": workspace}

    async def fake_run_agent(agent: Any, prompt: str, *, max_retries: int) -> str:
        del agent, max_retries
        prompts.append(prompt)
        if len(prompts) == 1:
            return json.dumps(
                {
                    "diagnoses": [
                        {
                            "evidence_status": "confirmed",
                            "root_cause": "A premature single explanation.",
                            "target_ref": "member_harness.solver.prompt",
                        }
                    ]
                }
            )
        if len(prompts) == 2:
            assert "did not provide a valid mandatory" in prompt
            return json.dumps(
                {
                    "causal_investigation": {
                        "hypotheses": [
                            {
                                "hypothesis_id": "h_parser",
                                "claim": "Legacy parser selection caused the failure.",
                                "falsified_if": "The modern parser was selected.",
                                "evidence_requests": [
                                    {"operation": "search_trace", "query": "EXACT_DISCRIMINATOR parser"}
                                ],
                            },
                            {
                                "hypothesis_id": "h_route",
                                "claim": "Routing selected the wrong handler.",
                                "falsified_if": "The expected handler was selected.",
                                "evidence_requests": [{"operation": "search_trace", "query": "handler routing"}],
                            },
                        ]
                    }
                }
            )
        return json.dumps(
            {
                "diagnoses": [
                    {
                        "issue_category": "member_harness",
                        "severity": "medium",
                        "summary": "The trace selected the legacy parser.",
                        "failure_mode": "legacy_parser_selection",
                        "evidence_status": "confirmed",
                        "root_cause": "Legacy parser selection caused the failure.",
                        "target_ref": "member_harness.solver.prompt",
                        "hypothesis_assessment": [
                            {
                                "hypothesis_id": "h_parser",
                                "status": "supported",
                                "falsifying_condition_status": "not_observed",
                                "claim_follows_from_evidence": "yes",
                                "logic_check": "q1 contains the legacy parser discriminator.",
                                "controller_request_ids": ["q1"],
                            },
                            {
                                "hypothesis_id": "h_route",
                                "status": "falsified",
                                "falsifying_condition_status": "unknown",
                                "claim_follows_from_evidence": "no",
                                "logic_check": "No routing mismatch follows from the evidence.",
                                "controller_request_ids": [],
                            },
                        ],
                    }
                ]
            }
        )

    monkeypatch.setattr(strategy, "_build_agent", fake_build_agent)
    monkeypatch.setattr(analyzer_module, "_run_agent", fake_run_agent)
    monkeypatch.setattr(analyzer_module, "_case_diagnoses_validation_conflicts", lambda *args, **kwargs: [])

    results = await strategy._per_case_diagnosis([case], DeterministicSignals(method="script_based"), None)

    assert len(prompts) == 3
    assert results[0]["causal_investigation"]["strict_plan_correction_attempted"] is True
    assert results[0]["causal_investigation"]["hypothesis_count"] == 2

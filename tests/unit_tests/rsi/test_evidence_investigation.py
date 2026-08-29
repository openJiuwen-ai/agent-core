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


def test_normalize_plan_accepts_generic_investigation_wrapper() -> None:
    plan = normalize_causal_investigation(
        {
            "investigation": {
                "hypotheses": [
                    {
                        "hypothesis_id": "h_parser",
                        "claim": "The parser selected a legacy mode.",
                        "falsified_if": "The trace shows the modern parser was selected.",
                        "evidence_requests": [
                            {
                                "request_id": "q1",
                                "operation": "search_trace",
                                "query": "parser selected mode",
                            }
                        ],
                    }
                ]
            }
        }
    )

    assert plan is not None
    assert plan["hypotheses"][0]["hypothesis_id"] == "h_parser"
    assert plan["evidence_requests"][0]["request_id"] == "q1"


def test_normalize_plan_respects_explicit_hypothesis_budget() -> None:
    plan = normalize_causal_investigation(
        {
            "hypotheses": [
                {
                    "hypothesis_id": f"h{index}",
                    "claim": f"Distinct mechanism {index} occurred.",
                    "falsified_if": f"Mechanism {index} did not occur.",
                }
                for index in range(1, 6)
            ]
        },
        max_hypotheses=4,
    )

    assert plan is not None
    assert [item["hypothesis_id"] for item in plan["hypotheses"]] == ["h1", "h2", "h3", "h4"]


def test_numeric_delta_obligation_distinguishes_behavior_from_numeric_context() -> None:
    plan = normalize_causal_investigation(
        {
            "hypotheses": [
                {
                    "hypothesis_id": "h_write",
                    "claim": "The agent did not write the requested scenario into the persisted artifact.",
                    "falsified_if": "A write event shows the requested scenario was persisted.",
                    "numeric_change_check_required": False,
                    "evidence_requests": [
                        {
                            "operation": "search_trace",
                            "query": "write persisted artifact",
                        }
                    ],
                },
                {
                    "hypothesis_id": "h_delta",
                    "claim": "The formula has a before-versus-after numeric delta.",
                    "falsified_if": "The computed delta is zero.",
                    "numeric_change_check_required": True,
                    "evidence_requests": [
                        {
                            "operation": "compare_numeric_change",
                            "before_expression": "0.17",
                            "after_expression": "0.18",
                            "expected_delta": 0.01,
                        }
                    ],
                },
            ]
        }
    )

    assert plan is not None
    obligations = {item["hypothesis_id"]: item["numeric_change_check_required"] for item in plan["hypotheses"]}
    assert obligations == {"h_write": False, "h_delta": True}


def test_numeric_delta_language_cannot_be_disabled_by_model_declaration() -> None:
    plan = normalize_causal_investigation(
        {
            "hypotheses": [
                {
                    "hypothesis_id": "h1",
                    "claim": "The formula subtracts two percent and then adds one percent.",
                    "falsified_if": "The before and after values differ by the requested percentage point.",
                    "numeric_change_check_required": False,
                    "evidence_requests": [{"operation": "read_event", "message_index": 3}],
                }
            ]
        }
    )

    assert plan is not None
    assert plan["hypotheses"][0]["numeric_change_check_required"] is True


def test_numeric_delta_request_cannot_be_disabled_by_model_declaration() -> None:
    plan = normalize_causal_investigation(
        {
            "hypotheses": [
                {
                    "hypothesis_id": "h1",
                    "claim": "The numeric result changed.",
                    "falsified_if": "The result did not change.",
                    "numeric_change_check_required": False,
                    "evidence_requests": [
                        {
                            "operation": "compare_numeric_change",
                            "before_expression": "1",
                            "after_expression": "2",
                            "expected_delta": 1,
                        }
                    ],
                }
            ]
        }
    )

    assert plan is not None
    assert plan["hypotheses"][0]["numeric_change_check_required"] is True


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


def test_strict_plan_requires_two_alternatives_for_every_failed_requirement() -> None:
    partial = normalize_causal_investigation(
        {
            "hypotheses": [
                {
                    "hypothesis_id": "h1",
                    "claim": "The parser caused both failures.",
                    "explains_requirement_ids": ["criterion:value", "criterion:format"],
                    "falsified_if": "The parser output is correct.",
                    "evidence_requests": [{"operation": "search_trace", "query": "parser output"}],
                },
                {
                    "hypothesis_id": "h2",
                    "claim": "The formatter caused the format failure.",
                    "explains_requirement_ids": ["criterion:format"],
                    "falsified_if": "The formatter output is correct.",
                    "evidence_requests": [{"operation": "search_trace", "query": "formatter output"}],
                },
            ]
        },
        failed_requirement_ids=["criterion:value", "criterion:format"],
        min_hypotheses=2,
        min_hypotheses_per_requirement=2,
        require_evidence_per_hypothesis=True,
    )
    complete = normalize_causal_investigation(
        {
            "hypotheses": [
                {
                    "hypothesis_id": "h1",
                    "claim": "A shared extraction error caused both failures.",
                    "explains_requirement_ids": ["criterion:value", "criterion:format"],
                    "falsified_if": "The extracted value and format are both correct.",
                    "evidence_requests": [{"operation": "search_trace", "query": "extracted value format"}],
                },
                {
                    "hypothesis_id": "h2",
                    "claim": "Independent decision errors caused the two failures.",
                    "explains_requirement_ids": ["criterion:value", "criterion:format"],
                    "falsified_if": "One decision explains both failures.",
                    "evidence_requests": [{"operation": "search_trace", "query": "value decision format decision"}],
                },
            ]
        },
        failed_requirement_ids=["criterion:value", "criterion:format"],
        min_hypotheses=2,
        min_hypotheses_per_requirement=2,
        require_evidence_per_hypothesis=True,
    )

    assert partial is None
    assert complete is not None
    assert len(complete["hypotheses"]) == 2


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


def test_structured_artifact_inspection_filters_file_types_and_reuses_parse_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openjiuwen.rsi.evaluation_result_analyzer import evidence_investigation

    case = _case(tmp_path)
    artifacts = Path(case.result_path).parent / "artifacts"
    artifacts.mkdir()
    workbook = artifacts / "result.xlsx"
    workbook.write_bytes(b"placeholder")
    (artifacts / "unrelated.pdf").write_bytes(b"placeholder")
    parsed: list[str] = []

    def fake_structured_text(path: Path) -> str:
        parsed.append(path.name)
        return "Summary A1=10 A2=20 A3=SUM(A1:A2)"

    monkeypatch.setattr(evidence_investigation, "_structured_artifact_text", fake_structured_text)
    plan = normalize_causal_investigation(
        {
            "hypotheses": [
                {
                    "claim": "The workbook contains the required formula.",
                    "falsified_if": "The worksheet formula is absent.",
                    "evidence_requests": [
                        {"operation": "inspect_artifact", "query": "workbook Summary formula"},
                        {"operation": "inspect_artifact", "query": "worksheet SUM A1 A2"},
                    ],
                }
            ]
        }
    )

    assert plan is not None
    results = execute_causal_investigation(case, plan)["results"]
    assert [result["availability"] for result in results] == ["available", "available"]
    assert parsed == [workbook.name]


def test_artifact_search_uses_logical_source_name_before_structured_parse_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openjiuwen.rsi.evaluation_result_analyzer import evidence_investigation

    logical_name = "Authorization Documents/Controlling Contract.xlsx"
    stored_name = "hashed_target.xlsx"
    case = _case(
        tmp_path,
        metadata={"analysis_artifact_snapshot": {"files": [{"path": stored_name, "source_path": logical_name}]}},
    )
    artifacts = Path(case.result_path).parent / "artifacts"
    artifacts.mkdir()
    for index in range(20):
        (artifacts / f"noise_{index:02d}.xlsx").write_bytes(b"placeholder")
    (artifacts / stored_name).write_bytes(b"placeholder")
    parsed: list[str] = []

    def fake_structured_text(path: Path) -> str:
        parsed.append(path.name)
        return "The controlling contract requires written authorization."

    monkeypatch.setattr(evidence_investigation, "_structured_artifact_text", fake_structured_text)
    plan = normalize_causal_investigation(
        {
            "hypotheses": [
                {
                    "claim": "The controlling contract contains the decisive authorization.",
                    "falsified_if": "The contract contains no authorization.",
                    "evidence_requests": [
                        {"operation": "inspect_artifact", "query": "Controlling Contract authorization"}
                    ],
                }
            ]
        }
    )

    assert plan is not None
    result = execute_causal_investigation(case, plan)["results"][0]
    assert result["availability"] == "available"
    assert result["matches"][0]["logical_source"] == logical_name
    assert stored_name in parsed


def test_artifact_inspection_uses_requested_source_as_selection_hint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openjiuwen.rsi.evaluation_result_analyzer import evidence_investigation

    logical_name = "nested/Controlling Contract.docx"
    stored_name = "__longpath__/target.docx"
    case = _case(
        tmp_path,
        metadata={"analysis_artifact_snapshot": {"files": [{"path": stored_name, "source_path": logical_name}]}},
    )
    artifacts = Path(case.result_path).parent / "artifacts"
    for index in range(20):
        path = artifacts / f"noise_{index:02d}.docx"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"placeholder")
    target = artifacts / stored_name
    target.parent.mkdir(parents=True)
    target.write_bytes(b"placeholder")
    parsed: list[str] = []

    def fake_structured_text(path: Path) -> str:
        parsed.append(path.name)
        return "The decisive obligation is present."

    monkeypatch.setattr(evidence_investigation, "_structured_artifact_text", fake_structured_text)
    plan = normalize_causal_investigation(
        {
            "hypotheses": [
                {
                    "claim": "The named source contains the obligation.",
                    "falsified_if": "The obligation is absent.",
                    "evidence_requests": [
                        {
                            "operation": "inspect_artifact",
                            "query": "decisive obligation",
                            "relative_path": logical_name,
                        }
                    ],
                }
            ]
        }
    )

    assert plan is not None
    result = execute_causal_investigation(case, plan)["results"][0]
    assert result["availability"] == "available"
    assert len(result["matches"]) == 1
    assert result["matches"][0]["logical_source"] == logical_name
    assert target.name in parsed


def test_artifact_inspection_uses_named_source_from_request_purpose(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openjiuwen.rsi.evaluation_result_analyzer import evidence_investigation

    target_logical = "deliverables/Target Decision Record.docx"
    reference_logical = "references/Decision Guidance.docx"
    case = _case(
        tmp_path,
        metadata={
            "analysis_artifact_snapshot": {
                "files": [
                    {"path": "target.docx", "source_path": target_logical},
                    {"path": "reference.docx", "source_path": reference_logical},
                ]
            }
        },
    )
    artifacts = Path(case.result_path).parent / "artifacts"
    artifacts.mkdir()
    (artifacts / "target.docx").write_bytes(b"placeholder")
    (artifacts / "reference.docx").write_bytes(b"placeholder")

    def fake_structured_text(path: Path) -> str:
        return "decision required condition" if path.name == "target.docx" else "decision required condition " * 20

    monkeypatch.setattr(evidence_investigation, "_structured_artifact_text", fake_structured_text)
    plan = normalize_causal_investigation(
        {
            "hypotheses": [
                {
                    "claim": "The target record contains the required condition.",
                    "falsified_if": "The condition is absent.",
                    "evidence_requests": [
                        {
                            "operation": "inspect_artifact",
                            "query": "decision required condition",
                            "purpose": "Read Target Decision Record.docx rather than topical guidance.",
                        }
                    ],
                }
            ]
        }
    )

    assert plan is not None
    result = execute_causal_investigation(case, plan)["results"][0]
    assert result["matches"][0]["logical_source"] == target_logical


def test_artifact_search_window_can_be_followed_without_repeating_broad_query(tmp_path: Path) -> None:
    case = _case(tmp_path)
    artifact = Path(case.result_path).parent / "artifacts" / "contract.txt"
    artifact.parent.mkdir()
    artifact.write_text("A" * 2_400 + "DECISIVE_CLAUSE" + "B" * 1_000, encoding="utf-8")
    plan = normalize_causal_investigation(
        {
            "hypotheses": [
                {
                    "claim": "The contract contains a decisive clause after the first search window.",
                    "falsified_if": "The complete contract contains no decisive clause.",
                    "evidence_requests": [
                        {"operation": "inspect_artifact", "query": "contract full document"},
                        {
                            "operation": "read_artifact_window",
                            "relative_path": "artifacts/contract.txt",
                            "source_char_start": 2_000,
                            "max_chars": 1_000,
                        },
                        {
                            "operation": "read_artifact_window",
                            "relative_path": "../outside.txt",
                            "source_char_start": 0,
                        },
                    ],
                }
            ]
        }
    )

    assert plan is not None
    assert [item["operation"] for item in plan["evidence_requests"]] == [
        "inspect_artifact",
        "read_artifact_window",
    ]
    results = execute_causal_investigation(case, plan)["results"]
    search_span = results[0]["matches"][0]["exact_spans"][0]
    assert search_span["window_complete"] is False
    assert results[1]["source_char_start"] == 2_000
    assert results[1]["source_char_end"] == 3_000
    assert "DECISIVE_CLAUSE" in results[1]["text"]


def test_artifact_window_resolves_snapshot_logical_path_to_longpath_file(tmp_path: Path) -> None:
    logical_name = "nested/source/Controlling Contract.txt"
    stored_name = "__longpath__/a1b2c3d4.txt"
    case = _case(
        tmp_path,
        metadata={"analysis_artifact_snapshot": {"files": [{"path": stored_name, "source_path": logical_name}]}},
    )
    artifact = Path(case.result_path).parent / "artifacts" / stored_name
    artifact.parent.mkdir(parents=True)
    artifact.write_text("DECISIVE_CLAUSE", encoding="utf-8")
    plan = normalize_causal_investigation(
        {
            "hypotheses": [
                {
                    "claim": "The logical artifact contains the decisive clause.",
                    "falsified_if": "The clause is absent.",
                    "evidence_requests": [
                        {
                            "operation": "read_artifact_window",
                            "relative_path": logical_name,
                            "source_char_start": 0,
                        }
                    ],
                }
            ]
        }
    )

    assert plan is not None
    result = execute_causal_investigation(case, plan)["results"][0]
    assert result["availability"] == "available"
    assert result["source"] == f"artifacts/{stored_name}"
    assert result["logical_source"] == logical_name
    assert result["text"] == "DECISIVE_CLAUSE"


def test_artifact_window_recovers_unambiguous_logical_name_after_encoding_damage(tmp_path: Path) -> None:
    logical_name = "deliverables/Annex 3 - Controller Notification Template.txt"
    stored_name = "__longpath__/a1b2c3d4.txt"
    case = _case(
        tmp_path,
        metadata={"analysis_artifact_snapshot": {"files": [{"path": stored_name, "source_path": logical_name}]}},
    )
    artifact = Path(case.result_path).parent / "artifacts" / stored_name
    artifact.parent.mkdir(parents=True)
    artifact.write_text("DECISIVE_CLAUSE", encoding="utf-8")
    plan = normalize_causal_investigation(
        {
            "hypotheses": [
                {
                    "claim": "The logical artifact is readable.",
                    "falsified_if": "The artifact cannot be resolved.",
                    "evidence_requests": [
                        {
                            "operation": "read_artifact_window",
                            "relative_path": "deliverables/Annex 3 ?C Controller Notification Template.txt",
                        }
                    ],
                }
            ]
        }
    )

    assert plan is not None
    result = execute_causal_investigation(case, plan)["results"][0]
    assert result["availability"] == "available"
    assert result["logical_source"] == logical_name


def test_artifact_window_accepts_controller_source_and_end_offset(tmp_path: Path) -> None:
    case = _case(tmp_path)
    artifact = Path(case.result_path).parent / "artifacts" / "workspace" / "contract.txt"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("0123456789", encoding="utf-8")
    plan = normalize_causal_investigation(
        {
            "hypotheses": [
                {
                    "claim": "A returned source identity can be followed.",
                    "falsified_if": "The returned window is unavailable.",
                    "evidence_requests": [
                        {
                            "operation": "read_artifact_window",
                            "source": "artifacts/workspace/contract.txt",
                            "source_char_start": 3,
                            "source_char_end": 7,
                        }
                    ],
                }
            ]
        }
    )

    assert plan is not None
    result = execute_causal_investigation(case, plan)["results"][0]
    assert result["availability"] == "available"
    assert result["text"] == "3456"


def test_artifact_search_prefers_window_covering_specific_terms(tmp_path: Path) -> None:
    case = _case(tmp_path)
    artifact = Path(case.result_path).parent / "artifacts" / "contract.txt"
    artifact.parent.mkdir()
    artifact.write_text(
        (("contract payment general text " * 120) + "contract payment WATERFALL_TRIGGER decisive clause"),
        encoding="utf-8",
    )
    plan = normalize_causal_investigation(
        {
            "hypotheses": [
                {
                    "claim": "The payment waterfall controls the decision.",
                    "falsified_if": "No waterfall clause is present.",
                    "evidence_requests": [
                        {"operation": "inspect_artifact", "query": "contract payment WATERFALL_TRIGGER"}
                    ],
                }
            ]
        }
    )

    assert plan is not None
    result = execute_causal_investigation(case, plan)["results"][0]
    assert "WATERFALL_TRIGGER" in result["matches"][0]["exact_spans"][0]["text"]


def test_controller_closes_incomplete_artifact_source_with_contiguous_windows(tmp_path: Path) -> None:
    case = _case(tmp_path)
    artifact = Path(case.result_path).parent / "artifacts" / "policy.txt"
    artifact.parent.mkdir()
    content = "A" * 13_000 + "DECISIVE_TERM" + "B" * 12_500
    artifact.write_text(content, encoding="utf-8")
    plan = normalize_causal_investigation(
        {
            "hypotheses": [
                {
                    "hypothesis_id": "h_absence",
                    "claim": "The bounded source is missing a required statement.",
                    "falsified_if": "The complete source contains the statement.",
                    "evidence_requests": [
                        {
                            "request_id": "q_search",
                            "operation": "inspect_artifact",
                            "query": "DECISIVE_TERM",
                            "proof_obligation": "absence",
                        }
                    ],
                }
            ]
        }
    )

    assert plan is not None
    evidence = execute_causal_investigation(case, plan)

    automatic = [item for item in evidence["results"] if item.get("automatic")]
    assert [item["source_char_start"] for item in automatic] == [0, 12_000, 24_000]
    assert [item["source_char_end"] for item in automatic] == [12_000, 24_000, len(content)]
    assert evidence["artifact_evidence_closure"]["status"] == "completed"
    assert evidence["artifact_evidence_closure"]["completed_source_count"] == 1
    assert evidence["automatic_request_count"] == 3


def test_controller_stops_at_physical_witness_for_existence_obligation(tmp_path: Path) -> None:
    case = _case(tmp_path)
    artifact = Path(case.result_path).parent / "artifacts" / "large.txt"
    artifact.parent.mkdir()
    artifact.write_text("A" * 5_000 + "PRESENT_WITNESS" + "B" * 5_000, encoding="utf-8")
    plan = normalize_causal_investigation(
        {
            "hypotheses": [
                {
                    "claim": "The source contains a present witness.",
                    "falsified_if": "No witness exists in the source.",
                    "evidence_requests": [
                        {
                            "operation": "inspect_artifact",
                            "query": "PRESENT_WITNESS",
                            "proof_obligation": "existence",
                        }
                    ],
                }
            ]
        }
    )

    assert plan is not None
    evidence = execute_causal_investigation(case, plan)

    assert evidence["results"][0]["availability"] == "available"
    assert evidence["automatic_request_count"] == 0
    assert evidence["artifact_evidence_closure"]["status"] == "not_needed"


def test_controller_deduplicates_automatic_closure_for_repeated_source(tmp_path: Path) -> None:
    case = _case(tmp_path)
    artifact = Path(case.result_path).parent / "artifacts" / "record.txt"
    artifact.parent.mkdir()
    artifact.write_text("required record " * 1_000, encoding="utf-8")
    plan = normalize_causal_investigation(
        {
            "hypotheses": [
                {
                    "hypothesis_id": "h_missing",
                    "claim": "The source is missing a complete record.",
                    "falsified_if": "The source contains the complete record.",
                    "evidence_requests": [
                        {
                            "request_id": "q1",
                            "operation": "inspect_artifact",
                            "query": "required record",
                            "proof_obligation": "coverage",
                        }
                    ],
                },
                {
                    "hypothesis_id": "h_conflict",
                    "claim": "The source contains a conflicting record.",
                    "falsified_if": "The conflicting record is absent.",
                    "evidence_requests": [
                        {
                            "request_id": "q2",
                            "operation": "inspect_artifact",
                            "query": "complete record",
                            "proof_obligation": "coverage",
                        },
                    ],
                },
            ]
        }
    )

    assert plan is not None
    evidence = execute_causal_investigation(case, plan)

    assert evidence["artifact_evidence_closure"]["candidate_source_count"] == 1
    automatic = [item for item in evidence["results"] if item.get("automatic")]
    starts = [item["source_char_start"] for item in automatic]
    assert starts == [0, 12_000]
    assert all(item["hypothesis_ids"] == ["h_missing", "h_conflict"] for item in automatic)
    assert all(item["parent_request_ids"] == ["q1", "q2"] for item in automatic)


def test_normalizer_merges_shared_probe_bindings_before_applying_budget() -> None:
    plan = normalize_causal_investigation(
        {
            "hypotheses": [
                {
                    "hypothesis_id": "h1",
                    "claim": "The source is missing a decision record.",
                    "falsified_if": "The record is present.",
                    "evidence_requests": [
                        {
                            "request_id": "q1",
                            "operation": "inspect_artifact",
                            "query": "decision record",
                            "proof_obligation": "coverage",
                            "purpose": "test h1",
                        }
                    ],
                },
                {
                    "hypothesis_id": "h2",
                    "claim": "The source contains a conflicting record.",
                    "falsified_if": "The record is absent.",
                    "evidence_requests": [
                        {
                            "request_id": "q1",
                            "operation": "inspect_artifact",
                            "query": "decision record",
                            "proof_obligation": "coverage",
                            "purpose": "the same probe also tests h2",
                        }
                    ],
                },
            ]
        },
        max_requests=1,
        min_hypotheses=2,
        require_evidence_per_hypothesis=True,
    )

    assert plan is not None
    assert len(plan["evidence_requests"]) == 1
    assert plan["evidence_requests"][0]["hypothesis_ids"] == ["h1", "h2"]


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
                        {"operation": "inspect_evaluation", "query": "artifact contract mismatch"},
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


def test_artifact_search_does_not_treat_evaluation_metadata_as_a_file(tmp_path: Path) -> None:
    case = _case(tmp_path, metadata={"judge_detail": {"reason": "missing contract clause"}})
    plan = normalize_causal_investigation(
        {
            "hypotheses": [
                {
                    "claim": "A physical file contains the contract clause.",
                    "falsified_if": "The file does not contain it.",
                    "evidence_requests": [
                        {"operation": "inspect_artifact", "query": "missing contract clause"},
                        {"operation": "inspect_evaluation", "query": "missing contract clause"},
                    ],
                }
            ]
        }
    )

    assert plan is not None
    results = execute_causal_investigation(case, plan)["results"]
    assert results[0]["availability"] == "not_available"
    assert results[0]["reason"] == "physical_artifact_snapshot_not_available"
    assert results[1]["availability"] == "available"
    assert results[1]["evidence_class"] == "evaluation_metadata"


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

    assert "unresolved hypotheses without a supported local mechanism require target_ref=unassigned" in " ".join(
        conflicts
    )


def test_supported_local_issue_survives_when_unresolved_hypothesis_is_split() -> None:
    from openjiuwen.rsi.evaluation_result_analyzer.analyzer import (
        _causal_investigation_conflicts,
        _normalize_case_diagnoses,
    )

    diagnoses = _normalize_case_diagnoses(
        {
            "diagnoses": [
                {
                    "issue_category": "member_harness",
                    "summary": "A supported read behavior contributes to the failed value.",
                    "failure_mode": "incomplete_source_read",
                    "failure_cluster": {
                        "failed_checks": ["criterion:value"],
                        "observable_behavior": "the controlling clause was not read",
                    },
                    "evidence_status": "supported_hypothesis",
                    "target_ref": "member_harness.solver.skill",
                    "causal_coverage": {
                        "explained_requirement_ids": ["criterion:value"],
                        "residual_requirement_ids": ["criterion:format"],
                        "unexplained_observations": ["a routing alternative remains unresolved"],
                    },
                    "hypothesis_assessment": [
                        {
                            "hypothesis_id": "h_read",
                            "status": "supported",
                            "falsifying_condition_status": "not_observed",
                            "claim_follows_from_evidence": "yes",
                            "evidence_relation": "direct_claim",
                            "evidence_independence": "direct_observation",
                            "logic_check": "q1 shows the read ended before the clause",
                            "controller_request_ids": ["q1"],
                        },
                        {
                            "hypothesis_id": "h_route",
                            "status": "unresolved",
                            "falsifying_condition_status": "unknown",
                            "claim_follows_from_evidence": "unknown",
                            "logic_check": "q2 did not find a routing discriminator",
                            "controller_request_ids": [],
                        },
                    ],
                }
            ]
        }
    )
    conflicts = _causal_investigation_conflicts(
        diagnoses,
        {
            "hypotheses": [
                {
                    "hypothesis_id": "h_read",
                    "claim": "The read stopped early.",
                    "explains_requirement_ids": ["criterion:value"],
                    "falsified_if": "The clause was read.",
                },
                {
                    "hypothesis_id": "h_route",
                    "claim": "Routing hid the clause.",
                    "explains_requirement_ids": ["criterion:value"],
                    "falsified_if": "Routing exposed the clause.",
                },
            ],
            "evidence_requests": [
                {"request_id": "q1", "hypothesis_ids": ["h_read"], "operation": "read_event"},
                {"request_id": "q2", "hypothesis_ids": ["h_route"], "operation": "search_trace"},
            ],
        },
        evidence_results={
            "results": [
                {"request_id": "q1", "availability": "available"},
                {"request_id": "q2", "availability": "not_found"},
            ]
        },
        prior_candidate_feedback=None,
    )

    assert len(diagnoses) == 2
    assert diagnoses[0]["target_ref"] == "member_harness.solver.skill"
    assert diagnoses[1]["target_ref"] == "unassigned"
    assert conflicts == []


def test_falsified_prior_causal_hypothesis_cannot_be_reused_by_semantic_identity() -> None:
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
                    "hypothesis_semantic_id": "chs:same-causal-claim",
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
            "experiments": [
                {
                    "causal_intervention_contracts": [
                        {
                            "source_causal_hypothesis_id": "h_failed_before",
                            "source_causal_hypothesis_semantic_id": "chs:same-causal-claim",
                        }
                    ]
                }
            ]
        },
    )

    assert "falsified prior causal hypotheses were reused by semantic identity: chs:same-causal-claim" in " ".join(
        conflicts
    )


def test_local_hypothesis_label_can_be_reused_for_a_different_causal_claim() -> None:
    from openjiuwen.rsi.evaluation_result_analyzer.analyzer import _causal_investigation_conflicts

    conflicts = _causal_investigation_conflicts(
        [
            {
                "evidence_status": "confirmed",
                "target_ref": "member_harness.solver.skill",
                "hypothesis_assessment": [
                    {
                        "hypothesis_id": "h1",
                        "status": "supported",
                        "falsifying_condition_status": "not_observed",
                        "claim_follows_from_evidence": "yes",
                        "logic_check": "Current evidence supports the new claim.",
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
                    "hypothesis_id": "h1",
                    "hypothesis_semantic_id": "chs:new-claim",
                    "claim": "A newly observed mechanism.",
                    "falsified_if": "The new observation is absent.",
                }
            ],
            "evidence_requests": [
                {
                    "request_id": "q1",
                    "hypothesis_ids": ["h1"],
                    "operation": "compare_runs",
                }
            ],
        },
        evidence_results={"results": [{"request_id": "q1", "availability": "available"}]},
        prior_candidate_feedback={
            "experiments": [
                {
                    "causal_intervention_contracts": [
                        {
                            "source_causal_hypothesis_id": "h1",
                            "source_causal_hypothesis_semantic_id": "chs:old-claim",
                        }
                    ]
                }
            ]
        },
    )

    assert not any("falsified prior causal hypotheses were reused" in conflict for conflict in conflicts)


def test_reconciliation_keeps_same_mechanism_falsifier_unresolved() -> None:
    from openjiuwen.rsi.evaluation_result_analyzer.analyzer import (
        _hypothesis_assessment_entailment_audit,
        _reconcile_causal_assessments,
    )

    investigation = {
        "hypotheses": [
            {
                "hypothesis_id": "h_iteration",
                "claim": "An approximate solver stopped before the output was stable at required precision.",
                "explains_requirement_ids": ["case:authoritative_outcome"],
                "falsified_if": "An independent perturbation or recomputation is stable at required precision.",
            }
        ],
        "evidence_requests": [
            {
                "request_id": "q1",
                "hypothesis_ids": ["h_iteration"],
                "operation": "read_event",
                "trace_id": "case:trial_1",
                "message_index": 3,
            }
        ],
    }
    diagnoses = [
        {
            "issue_category": "unassigned",
            "evidence_status": "insufficient",
            "target_ref": "unassigned",
            "selected_hypothesis_id": "",
            "confidence": "low",
            "failure_cluster": {
                "failed_checks": ["case:authoritative_outcome"],
                "observable_behavior": "the solver returned a final value",
            },
            "causal_coverage": {
                "explained_requirement_ids": [],
                "residual_requirement_ids": ["case:authoritative_outcome"],
                "unexplained_observations": ["no independent stability probe was run"],
                "sufficiency_status": "unknown",
            },
            "hypothesis_assessment": [
                {
                    "hypothesis_id": "h_iteration",
                    "status": "falsified",
                    "falsifying_condition_status": "observed",
                    "claim_follows_from_evidence": "no",
                    "evidence_relation": "self_consistency",
                    "evidence_independence": "same_mechanism",
                    "logic_check": "The questioned solver returned a value and its own outputs agreed.",
                    "controller_request_ids": ["q1"],
                }
            ],
        }
    ]
    reconciled, warnings = _reconcile_causal_assessments(
        diagnoses,
        investigation,
        evidence_results={"results": [{"request_id": "q1", "operation": "read_event", "availability": "available"}]},
        failed_requirement_inventory={"requirements": [{"requirement_id": "case:authoritative_outcome"}]},
    )

    assessment = reconciled[0]["hypothesis_assessment"][0]
    assert assessment["status"] == "unresolved"
    assert assessment["verification_status"] == "unresolved"
    assert any("falsifier_not_independent_of_questioned_mechanism" in warning for warning in warnings)
    assert _hypothesis_assessment_entailment_audit(reconciled)["status"] == "needs_evidence"


def test_unverified_decision_ground_requires_structured_incomplete_chain() -> None:
    from openjiuwen.rsi.evaluation_result_analyzer.analyzer import _decision_ground_audit_conflicts

    diagnosis = {
        "failure_mode": "unverified_decision_ground_used",
        "decision_ground_audit": [
            {
                "ground_id": "g1",
                "ground_text": "A preferred practice was used as a rejection ground.",
                "materiality": "material",
                "used_for_decision": True,
                "authority_status": "verified",
                "scope_status": "matched",
                "owner_status": "matched",
                "trigger_status": "not_applicable",
                "entailment_status": "not_entailed",
                "controller_request_ids": ["q_ground"],
            }
        ],
    }

    assert _decision_ground_audit_conflicts(diagnosis) == []
    diagnosis["decision_ground_audit"][0]["entailment_status"] = "entailed"
    assert "lacks a material ground with an incomplete chain" in _decision_ground_audit_conflicts(diagnosis)[0]


def test_decision_ground_audit_adds_exact_released_answer_probe(tmp_path: Path) -> None:
    from openjiuwen.rsi.evaluation_result_analyzer import analyzer as analyzer_module

    case = _case(tmp_path)
    diagnoses = [
        {
            "decision_ground_audit": [
                {
                    "ground_id": "g1",
                    "ground_text": "A preferred item was used as a blocking reason.",
                    "materiality": "material",
                    "used_for_decision": True,
                    "controller_request_ids": ["q_discovery"],
                }
            ]
        }
    ]
    investigation = {
        "hypotheses": [
            {
                "hypothesis_id": "h1",
                "claim": "The released answer used the preferred item as required.",
                "falsified_if": "The released answer did not use that ground.",
                "explains_requirement_ids": ["case:authoritative_outcome"],
            }
        ],
        "evidence_requests": [
            {
                "request_id": "q_discovery",
                "hypothesis_ids": ["h1"],
                "operation": "inspect_artifact",
                "query": "preferred item",
            }
        ],
    }
    evidence_results = {
        "results": [
            {
                "request_id": "q_discovery",
                "hypothesis_ids": ["h1"],
                "operation": "inspect_artifact",
                "availability": "available",
            }
        ],
        "completed_request_count": 1,
    }

    updated, evidence, request_ids = analyzer_module._supplement_decision_ground_trace_evidence(
        case,
        diagnoses=diagnoses,
        investigation=investigation,
        evidence_results=evidence_results,
    )

    assert len(request_ids) == 1
    assert updated["evidence_requests"][-1]["operation"] == "read_event"
    assert updated["evidence_requests"][-1]["message_index"] == 7
    result = next(item for item in evidence["results"] if item["request_id"] == request_ids[0])
    assert result["availability"] == "available"
    assert "I need to distinguish" in json.dumps(result["event"]["content"])


def test_decision_ground_audit_expands_exact_children_and_rejects_discovery_only() -> None:
    from openjiuwen.rsi.evaluation_result_analyzer import analyzer as analyzer_module

    evidence_rows = [
        {
            "request_id": "q1",
            "operation": "inspect_artifact",
            "availability": "available",
        },
        {
            "request_id": "q1.auto.0",
            "parent_request_id": "q1",
            "operation": "read_artifact_window",
            "availability": "available",
        },
    ]
    assert analyzer_module._expand_cited_evidence_ids({"q1"}, evidence_rows) == {
        "q1",
        "q1.auto.0",
    }

    diagnoses = [
        {
            "decision_ground_audit": [
                {
                    "ground_id": "g1",
                    "ground_text": "A discovery result was used as a blocking reason.",
                    "materiality": "material",
                    "used_for_decision": True,
                }
            ]
        }
    ]
    raw = {
        "ground_audits": [
            {
                "diagnosis_index": 1,
                "ground_id": "g1",
                "material_ground_observed": True,
                "used_for_decision_observed": True,
                "authority_status": "missing",
                "scope_status": "matched",
                "owner_status": "matched",
                "trigger_status": "not_applicable",
                "entailment_status": "not_entailed",
                "direct_trace_entails": True,
                "exact_trace_evidence": "The discovery excerpt alone was cited.",
                "controller_request_ids": ["q1"],
                "approved_process_defect": True,
            }
        ]
    }
    audit = analyzer_module._normalize_decision_ground_entailment_audit(
        raw,
        diagnoses=diagnoses,
        evidence_results={"results": evidence_rows[:1]},
    )
    assert audit["status"] == "not_established"


@pytest.mark.asyncio
async def test_broad_decision_claim_narrows_to_verified_material_ground(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openjiuwen.rsi.config import EvaluationResultAnalyzerConfig
    from openjiuwen.rsi.evaluation_result_analyzer import analyzer as analyzer_module

    case = _case(tmp_path)
    strategy = analyzer_module.DiagnosisAgentStrategy(EvaluationResultAnalyzerConfig(model_config_ref="unused.yaml"))
    phases: list[str] = []
    diagnosis_calls = 0
    audit_calls = 0

    def assessment(hypothesis_id: str, status: str, request_id: str) -> dict[str, Any]:
        return {
            "hypothesis_id": hypothesis_id,
            "status": status,
            "falsifying_condition_status": "not_observed" if status == "supported" else "observed",
            "claim_follows_from_evidence": "yes" if status == "supported" else "no",
            "evidence_relation": "direct_claim" if status == "supported" else "direct_falsifier",
            "evidence_independence": "direct_observation",
            "logic_check": "The exact decision-ground span was observed.",
            "controller_request_ids": [request_id],
        }

    def diagnosis(*, narrow: bool) -> dict[str, Any]:
        selected = "h_ground" if narrow else "h_blanket"
        return {
            "diagnoses": [
                {
                    "issue_category": "member_harness",
                    "severity": "medium",
                    "summary": "A conclusion used a material reason without a complete requirement chain.",
                    "failure_mode": ("unverified_decision_ground_used" if narrow else "blanket_shortcut_assumed"),
                    "failure_cluster": {
                        "failed_checks": ["case:authoritative_outcome"],
                        "observable_behavior": "the final reasoning used the stated ground",
                    },
                    "evidence_status": "supported_hypothesis",
                    "failed_requirement": "Reach the requested conclusion from task-visible requirements.",
                    "competing_hypotheses": ["blanket shortcut", "one unverified ground"],
                    "discriminating_evidence": "The exact ground is material but its entailment is absent.",
                    "selected_hypothesis_id": selected,
                    "root_cause": (
                        "One material decision ground was used without verifying its entailment."
                        if narrow
                        else "Every gap was automatically treated as failure."
                    ),
                    "critical_mistake": "The observed ground was used before its requirement chain was complete.",
                    "general_mechanism": "Verify each material decision ground independently before using it.",
                    "target_ref": "member_harness.solver.prompt",
                    "evidence_refs": [{"trace_id": "case_001:trial_1", "message_index": 7}],
                    "affected_components": ["solver"],
                    "recommendation": "Require a complete ground ledger and recompute the conclusion.",
                    "decision_ground_audit": [
                        {
                            "ground_id": "g1",
                            "ground_text": "A preferred practice was presented as required.",
                            "materiality": "material",
                            "used_for_decision": True,
                            "authority_status": "verified",
                            "scope_status": "matched",
                            "owner_status": "matched",
                            "trigger_status": "not_applicable",
                            "entailment_status": "not_entailed",
                            "controller_request_ids": ["refine_ground" if narrow else "q1"],
                        }
                    ],
                    "causal_coverage": {
                        "explained_requirement_ids": ["case:authoritative_outcome"],
                        "residual_requirement_ids": [],
                        "unexplained_observations": [],
                        "causal_chain": [],
                        "counterfactual_prediction": "Only grounds with a complete chain remain in the recomputed decision.",
                        "sufficiency_status": "local_contributor",
                    },
                    "decision_contract": {
                        "wrong_decision": "use an unverified material ground",
                        "causal_distinction": "complete versus incomplete requirement chain",
                        "required_action": "verify every material ground, exclude unsupported grounds, and recompute",
                        "acceptance_observable": "the ledger is complete and no unsupported ground is used",
                        "scope_boundary": ["do not prescribe the final label"],
                        "activation_phase": "during_investigation",
                    },
                    "hypothesis_assessment": [assessment(selected, "supported", "refine_ground" if narrow else "q1")],
                    "confidence": "medium",
                }
            ]
        }

    async def fake_build_agent(workspace: str) -> dict[str, str]:
        return {"workspace": workspace}

    async def fake_run_agent(agent: Any, prompt: str, *, max_retries: int, **kwargs: Any) -> str:
        nonlocal diagnosis_calls, audit_calls
        del agent, max_retries, kwargs
        phase = prompt.splitlines()[0]
        phases.append(phase)
        if phase == "CAUSAL_INVESTIGATION_PHASE=plan":
            return json.dumps(
                {
                    "causal_investigation": {
                        "hypotheses": [
                            {
                                "hypothesis_id": "h_blanket",
                                "claim": "Every identified gap was automatically treated as failure.",
                                "explains_requirement_ids": ["case:authoritative_outcome"],
                                "falsified_if": "Each ground was separately classified.",
                                "evidence_requests": [
                                    {
                                        "request_id": "q1",
                                        "operation": "read_event",
                                        "trace_id": "case_001:trial_1",
                                        "message_index": 7,
                                    }
                                ],
                            },
                            {
                                "hypothesis_id": "h_valid",
                                "claim": "Every material ground had a complete requirement chain.",
                                "explains_requirement_ids": ["case:authoritative_outcome"],
                                "falsified_if": "One used material ground lacks entailment.",
                                "evidence_requests": [
                                    {
                                        "request_id": "q1",
                                        "operation": "read_event",
                                        "trace_id": "case_001:trial_1",
                                        "message_index": 7,
                                    }
                                ],
                            },
                        ]
                    }
                }
            )
        if phase == "CAUSAL_ASSESSMENT_PHASE=entailment_audit":
            audit_calls += 1
            if audit_calls == 1:
                return json.dumps(
                    {
                        "assessment_audits": [
                            {
                                "diagnosis_index": 1,
                                "hypothesis_id": "h_blanket",
                                "claimed_status": "supported",
                                "evidence_entails_status": False,
                                "evidence_independent": True,
                                "exact_entailment": "One ground does not prove a blanket shortcut.",
                                "approved": False,
                                "missing_discriminator": (
                                    "The visible material ground was used without a complete entailment chain."
                                ),
                            }
                        ]
                    }
                )
            return json.dumps(
                {
                    "assessment_audits": [
                        {
                            "diagnosis_index": 1,
                            "hypothesis_id": "h_ground",
                            "claimed_status": "supported",
                            "evidence_entails_status": True,
                            "evidence_independent": True,
                            "exact_entailment": "The exact material ground is used and lacks entailment.",
                            "approved": True,
                            "missing_discriminator": "",
                        }
                    ]
                }
            )
        if phase == "CAUSAL_ASSESSMENT_PHASE=decision_ground_audit":
            return json.dumps(
                {
                    "ground_audits": [
                        {
                            "diagnosis_index": 1,
                            "ground_id": "g1",
                            "material_ground_observed": True,
                            "used_for_decision_observed": True,
                            "authority_status": "unknown",
                            "scope_status": "unknown",
                            "owner_status": "unknown",
                            "trigger_status": "unknown",
                            "entailment_status": "unknown",
                            "direct_trace_entails": True,
                            "exact_trace_evidence": "The ground was used, but no specific broken link is proven.",
                            "controller_request_ids": ["q1"],
                            "approved_process_defect": False,
                            "reason": "Unknown alone cannot establish the process defect.",
                        }
                    ]
                }
            )
        if phase == "CAUSAL_INVESTIGATION_PHASE=refine":
            assert "unverified_decision_ground_used" in prompt
            return json.dumps(
                {
                    "causal_investigation": {
                        "hypotheses": [
                            {
                                "hypothesis_id": "h_ground",
                                "origin": "abductive_refinement",
                                "discovery_evidence_request_ids": ["q1"],
                                "claim": "One material decision ground was used before entailment was verified.",
                                "explains_requirement_ids": ["case:authoritative_outcome"],
                                "falsified_if": "The ground was not used or its complete chain was verified.",
                                "evidence_requests": [
                                    {
                                        "request_id": "ground",
                                        "operation": "read_event",
                                        "trace_id": "case_001:trial_1",
                                        "message_index": 7,
                                        "tool_call_index": 1,
                                    }
                                ],
                            }
                        ]
                    }
                }
            )
        if phase == "CAUSAL_HANDOFF_PHASE=audit":
            assert "do not prescribe the final label" in prompt
            return json.dumps(
                {
                    "diagnosis_audits": [
                        {
                            "diagnosis_index": 1,
                            "selected_hypothesis_id": "h_ground",
                            "hypothesis_binding": True,
                            "runtime_decidable": True,
                            "public_contract_consistent": True,
                            "decision_rule_entailed": True,
                            "decision_rule_source": "task_visible_invariant",
                            "decision_rule_evidence": "Each stated ground must entail the conclusion it supports.",
                            "evaluation_independent": True,
                            "single_intervention": True,
                            "approved": True,
                            "violations": [],
                        }
                    ]
                }
            )
        diagnosis_calls += 1
        return json.dumps(diagnosis(narrow=diagnosis_calls > 1))

    def fake_execute(case_input: Any, investigation: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        del case_input, kwargs
        results = [
            {
                "request_id": str(request.get("request_id", "")),
                "operation": str(request.get("operation", "")),
                "availability": "available",
                "summary": "exact decision-ground span",
            }
            for request in investigation.get("evidence_requests", [])
        ]
        return {"results": results, "completed_request_count": len(results)}

    monkeypatch.setattr(strategy, "_build_agent", fake_build_agent)
    monkeypatch.setattr(analyzer_module, "_run_agent", fake_run_agent)
    monkeypatch.setattr(analyzer_module, "execute_causal_investigation", fake_execute)
    monkeypatch.setattr(analyzer_module, "_case_diagnoses_validation_conflicts", lambda *args, **kwargs: [])
    monkeypatch.setattr(analyzer_module, "_causal_investigation_conflicts", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        analyzer_module,
        "_reconcile_causal_assessments",
        lambda diagnoses, *args, **kwargs: (diagnoses, []),
    )

    results = await strategy._per_case_diagnosis([case], DeterministicSignals(method="script_based"), None)

    assert phases == [
        "CAUSAL_INVESTIGATION_PHASE=plan",
        "CAUSAL_INVESTIGATION_PHASE=diagnose",
        "CAUSAL_ASSESSMENT_PHASE=decision_ground_audit",
        "CAUSAL_ASSESSMENT_PHASE=decision_ground_audit",
        "CAUSAL_ASSESSMENT_PHASE=entailment_audit",
        "CAUSAL_INVESTIGATION_PHASE=refine",
        "CAUSAL_INVESTIGATION_PHASE=diagnose",
        "CAUSAL_ASSESSMENT_PHASE=entailment_audit",
        "CAUSAL_HANDOFF_PHASE=audit",
    ]
    assert results[0]["failure_mode"] == "unverified_decision_ground_used"
    assert results[0]["target_ref"] == "member_harness.solver.prompt"
    assert results[0]["causal_investigation"]["closure_rounds"][0]["request_ids"] == ["refine_ground"]


@pytest.mark.asyncio
async def test_independent_ground_audit_injects_label_free_process_hypothesis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openjiuwen.rsi.evaluation_result_analyzer import analyzer as analyzer_module

    diagnoses = [
        {
            # Answer-level uncertainty is intentionally unassigned. The independent
            # controller audit is what may narrow it to an optimizable process defect.
            "issue_category": "unassigned",
            "target_ref": "unassigned",
            "failure_cluster": {
                "failed_checks": ["case:authoritative_outcome"],
                "observable_behavior": "a preferred item was used as a blocking ground",
            },
            "failed_requirement": "Reach the requested conclusion from task-visible requirements.",
            "selected_hypothesis_id": "h_blanket",
            "evidence_refs": [{"trace_id": "case_001:trial_1", "message_index": 7}],
            "decision_ground_audit": [
                {
                    "ground_id": "g_preference",
                    "ground_text": "The response called a preferred practice a required correction.",
                    "materiality": "material",
                    "used_for_decision": True,
                    "authority_status": "unknown",
                    "scope_status": "unknown",
                    "owner_status": "unknown",
                    "trigger_status": "unknown",
                    "entailment_status": "unknown",
                    "controller_request_ids": ["q_final"],
                }
            ],
            "causal_coverage": {
                "explained_requirement_ids": ["case:authoritative_outcome"],
                "residual_requirement_ids": [],
                "unexplained_observations": [],
                "causal_chain": [],
                "counterfactual_prediction": "unknown",
                "sufficiency_status": "local_contributor",
            },
            "hypothesis_assessment": [{"hypothesis_id": "h_blanket", "status": "supported"}],
        }
    ]
    investigation = {
        "hypotheses": [
            {
                "hypothesis_id": "h_blanket",
                "claim": "Every possible gap was treated as blocking.",
                "falsified_if": "Each reason was classified independently.",
                "explains_requirement_ids": ["case:authoritative_outcome"],
            }
        ],
        "evidence_requests": [
            {
                "request_id": "q_final",
                "operation": "read_event",
                "trace_id": "case_001:trial_1",
                "message_index": 7,
            }
        ],
    }
    evidence_results = {
        "results": [
            {
                "request_id": "q_final",
                "operation": "read_event",
                "availability": "available",
                "summary": (
                    "The final response calls the item recommended for convenience, "
                    "then lists it under required corrections supporting rejection."
                ),
            }
        ]
    }

    async def fake_run_agent(agent: Any, prompt: str, *, max_retries: int, **kwargs: Any) -> str:
        del agent, max_retries, kwargs
        assert prompt.startswith("CAUSAL_ASSESSMENT_PHASE=decision_ground_audit")
        return json.dumps(
            {
                "ground_audits": [
                    {
                        "diagnosis_index": 1,
                        "ground_id": "g_preference",
                        "material_ground_observed": True,
                        "used_for_decision_observed": True,
                        "authority_status": "contradicted",
                        "scope_status": "matched",
                        "owner_status": "matched",
                        "trigger_status": "not_applicable",
                        "entailment_status": "not_entailed",
                        "direct_trace_entails": True,
                        "exact_trace_evidence": (
                            "The released response says the item is recommended, then uses its "
                            "absence as a required correction supporting rejection."
                        ),
                        "controller_request_ids": ["q_final"],
                        "approved_process_defect": True,
                        "reason": "The same decision span directly contradicts mandatory entailment.",
                    }
                ]
            }
        )

    monkeypatch.setattr(analyzer_module, "_run_agent", fake_run_agent)
    narrowed, frozen, audit = await analyzer_module._run_decision_ground_entailment_audit(
        object(),
        diagnoses=diagnoses,
        investigation=investigation,
        evidence_results=evidence_results,
    )

    assert audit["status"] == "approved"
    assert narrowed[0]["failure_mode"] == "unverified_decision_ground_used"
    assert narrowed[0]["issue_category"] == "member_harness"
    assert narrowed[0]["target_ref"] == "member_harness.solver.prompt"
    assert narrowed[0]["selected_hypothesis_id"] == "h_controller_decision_ground"
    assert narrowed[0]["causal_coverage"]["sufficiency_status"] == "local_contributor"
    assert "final label" in narrowed[0]["decision_contract"]["scope_boundary"][0]
    assert any(item["hypothesis_id"] == "h_controller_decision_ground" for item in frozen["hypotheses"])


@pytest.mark.asyncio
async def test_ground_audit_canonical_survives_unresolved_sibling_and_enters_handoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openjiuwen.rsi.config import EvaluationResultAnalyzerConfig
    from openjiuwen.rsi.evaluation_result_analyzer import analyzer as analyzer_module

    case = _case(tmp_path)
    strategy = analyzer_module.DiagnosisAgentStrategy(EvaluationResultAnalyzerConfig(model_config_ref="unused.yaml"))
    phases: list[str] = []
    refine_calls = 0
    diagnose_calls = 0

    def supported(hypothesis_id: str) -> dict[str, Any]:
        return {
            "hypothesis_id": hypothesis_id,
            "status": "supported",
            "falsifying_condition_status": "not_observed",
            "claim_follows_from_evidence": "yes",
            "evidence_relation": "direct_claim",
            "evidence_independence": "direct_observation",
            "logic_check": "The exact event shows the claimed behavior.",
            "controller_request_ids": ["q1"],
        }

    def assigned_diagnoses() -> dict[str, Any]:
        base_coverage = {
            "explained_requirement_ids": ["case:authoritative_outcome"],
            "residual_requirement_ids": [],
            "unexplained_observations": [],
            "causal_chain": [
                {
                    "cause": "an observed decision step",
                    "effect": "the released result",
                    "evidence_status": "observed",
                    "evidence_refs": [],
                }
            ],
            "counterfactual_prediction": "The released behavior changes.",
            "sufficiency_status": "cluster_sufficient",
        }
        common = {
            "issue_category": "member_harness",
            "severity": "medium",
            "failure_cluster": {
                "failed_checks": ["case:authoritative_outcome"],
                "observable_behavior": "the released decision used stated reasons",
            },
            "evidence_status": "supported_hypothesis",
            "failed_requirement": "Complete the public task from task-visible requirements.",
            "evidence_refs": [{"trace_id": "case_001:trial_1", "message_index": 7}],
            "affected_components": ["solver"],
            "confidence": "medium",
        }
        return {
            "diagnoses": [
                {
                    **common,
                    "summary": "A broad decision mechanism was suspected.",
                    "failure_mode": "broad_decision_mechanism",
                    "selected_hypothesis_id": "h1",
                    "root_cause": "The broad mechanism was used.",
                    "critical_mistake": "A preferred item was used as required.",
                    "general_mechanism": "Verify decision reasons.",
                    "target_ref": "member_harness.solver.prompt",
                    "recommendation": "Verify the decision reasons.",
                    "decision_ground_audit": [
                        {
                            "ground_id": "g1",
                            "ground_text": "A preferred practice was listed as a required correction.",
                            "materiality": "material",
                            "used_for_decision": True,
                            "authority_status": "unknown",
                            "scope_status": "unknown",
                            "owner_status": "unknown",
                            "trigger_status": "unknown",
                            "entailment_status": "unknown",
                            "controller_request_ids": ["q1"],
                        }
                    ],
                    "causal_coverage": dict(base_coverage),
                    "hypothesis_assessment": [supported("h1")],
                },
                {
                    **common,
                    "summary": "A sibling mechanism was suspected.",
                    "failure_mode": "sibling_mechanism",
                    "selected_hypothesis_id": "h2",
                    "root_cause": "A sibling mechanism may also contribute.",
                    "critical_mistake": "A separate step may be wrong.",
                    "general_mechanism": "Check the sibling step.",
                    "target_ref": "member_harness.solver.prompt",
                    "recommendation": "Check the sibling step.",
                    "causal_coverage": dict(base_coverage),
                    "hypothesis_assessment": [supported("h2")],
                },
            ]
        }

    async def fake_build_agent(workspace: str) -> dict[str, str]:
        return {"workspace": workspace}

    async def fake_run_agent(agent: Any, prompt: str, *, max_retries: int, **kwargs: Any) -> str:
        nonlocal refine_calls, diagnose_calls
        del agent, max_retries, kwargs
        phase = prompt.splitlines()[0]
        phases.append(phase)
        if phase == "CAUSAL_INVESTIGATION_PHASE=plan":
            hypotheses = []
            for hypothesis_id in ("h1", "h2"):
                hypotheses.append(
                    {
                        "hypothesis_id": hypothesis_id,
                        "claim": f"{hypothesis_id} caused the observed decision behavior.",
                        "explains_requirement_ids": ["case:authoritative_outcome"],
                        "falsified_if": f"The exact event refutes {hypothesis_id}.",
                        "evidence_requests": [
                            {
                                "request_id": "q1",
                                "operation": "read_event",
                                "trace_id": "case_001:trial_1",
                                "message_index": 7,
                            }
                        ],
                    }
                )
            return json.dumps({"causal_investigation": {"hypotheses": hypotheses}})
        if phase == "CAUSAL_ASSESSMENT_PHASE=decision_ground_audit":
            return json.dumps(
                {
                    "ground_audits": [
                        {
                            "diagnosis_index": 1,
                            "ground_id": "g1",
                            "material_ground_observed": True,
                            "used_for_decision_observed": True,
                            "authority_status": "contradicted",
                            "scope_status": "matched",
                            "owner_status": "matched",
                            "trigger_status": "not_applicable",
                            "entailment_status": "not_entailed",
                            "direct_trace_entails": True,
                            "exact_trace_evidence": (
                                "The final response calls the practice preferred, then uses "
                                "its absence as a required correction supporting the decision."
                            ),
                            "controller_request_ids": ["q1"],
                            "approved_process_defect": True,
                            "reason": "The authority-to-entailment link is directly contradicted.",
                        }
                    ]
                }
            )
        if phase == "CAUSAL_ASSESSMENT_PHASE=entailment_audit":
            return json.dumps(
                {
                    "assessment_audits": [
                        {
                            "diagnosis_index": 1,
                            "hypothesis_id": "h_controller_decision_ground",
                            "claimed_status": "supported",
                            "evidence_entails_status": True,
                            "evidence_independent": True,
                            "exact_entailment": "The exact ground was materially used with a contradicted link.",
                            "approved": True,
                            "missing_discriminator": "",
                        },
                        {
                            "diagnosis_index": 2,
                            "hypothesis_id": "h2",
                            "claimed_status": "supported",
                            "evidence_entails_status": False,
                            "evidence_independent": True,
                            "exact_entailment": "",
                            "approved": False,
                            "missing_discriminator": "The sibling mechanism remains unresolved.",
                        },
                    ]
                }
            )
        if phase == "CAUSAL_INVESTIGATION_PHASE=refine":
            refine_calls += 1
            if refine_calls == 1:
                return json.dumps(
                    {
                        "causal_investigation": {
                            "evidence_requests": [
                                {
                                    "request_id": "q2",
                                    "operation": "read_event",
                                    "trace_id": "case_001:trial_1",
                                    "message_index": 8,
                                }
                            ]
                        }
                    }
                )
            return json.dumps({"causal_investigation": {"evidence_requests": [], "ready_without_more_evidence": True}})
        if phase == "CAUSAL_HANDOFF_PHASE=audit":
            assert "h_controller_decision_ground" in prompt
            return json.dumps(
                {
                    "diagnosis_audits": [
                        {
                            "diagnosis_index": 1,
                            "selected_hypothesis_id": "h_controller_decision_ground",
                            "hypothesis_binding": True,
                            "runtime_decidable": True,
                            "public_contract_consistent": True,
                            "decision_rule_entailed": True,
                            "decision_rule_source": "task_visible_invariant",
                            "decision_rule_evidence": "Each material ground must entail the decision it supports.",
                            "evaluation_independent": True,
                            "single_intervention": True,
                            "approved": True,
                            "violations": [],
                        }
                    ]
                }
            )
        diagnose_calls += 1
        if diagnose_calls == 1:
            return json.dumps(assigned_diagnoses())
        return json.dumps(
            {
                "diagnoses": [
                    {
                        "issue_category": "unassigned",
                        "severity": "low",
                        "summary": "The sibling mechanism remains unresolved.",
                        "failure_mode": "unresolved_sibling",
                        "failure_cluster": {
                            "failed_checks": ["case:authoritative_outcome"],
                            "observable_behavior": "the sibling observation remains ambiguous",
                        },
                        "evidence_status": "insufficient",
                        "selected_hypothesis_id": "",
                        "root_cause": "The sibling mechanism is unresolved.",
                        "target_ref": "unassigned",
                        "causal_coverage": {
                            "explained_requirement_ids": [],
                            "residual_requirement_ids": ["case:authoritative_outcome"],
                            "unexplained_observations": ["The sibling mechanism is unresolved."],
                            "causal_chain": [
                                {
                                    "cause": "unknown sibling cause",
                                    "effect": "ambiguous observation",
                                    "evidence_status": "unknown",
                                    "evidence_refs": [],
                                }
                            ],
                            "counterfactual_prediction": "No sibling prediction is available.",
                            "sufficiency_status": "unknown",
                        },
                        "hypothesis_assessment": [{"hypothesis_id": "h2", "status": "unresolved"}],
                    }
                ]
            }
        )

    def fake_execute(case_input: Any, investigation: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        del case_input, kwargs
        results = [
            {
                "request_id": str(request.get("request_id", "")),
                "operation": str(request.get("operation", "")),
                "availability": "available",
                "summary": "bounded exact decision evidence",
            }
            for request in investigation.get("evidence_requests", [])
        ]
        return {"results": results, "completed_request_count": len(results)}

    monkeypatch.setattr(strategy, "_build_agent", fake_build_agent)
    monkeypatch.setattr(analyzer_module, "_run_agent", fake_run_agent)
    monkeypatch.setattr(analyzer_module, "execute_causal_investigation", fake_execute)
    monkeypatch.setattr(analyzer_module, "_case_diagnoses_validation_conflicts", lambda *args, **kwargs: [])
    monkeypatch.setattr(analyzer_module, "_causal_investigation_conflicts", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        analyzer_module,
        "_reconcile_causal_assessments",
        lambda diagnoses, *args, **kwargs: (diagnoses, []),
    )

    results = await strategy._per_case_diagnosis([case], DeterministicSignals(method="script_based"), None)

    canonical = next(item for item in results if item.get("failure_mode") == "unverified_decision_ground_used")
    assert canonical["selected_hypothesis_id"] == "h_controller_decision_ground"
    assert canonical["target_ref"] == "member_harness.solver.prompt"
    assert "CAUSAL_INVESTIGATION_PHASE=refine" in phases
    assert phases[-1] == "CAUSAL_HANDOFF_PHASE=audit"
    assert results[0]["causal_investigation"]["causal_handoff_audit"]["status"] == "approved"


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

    async def fake_run_agent(agent: Any, prompt: str, *, max_retries: int, **kwargs: Any) -> str:
        nonlocal diagnosis_calls
        del agent, max_retries, kwargs
        prompts.append(prompt)
        if prompt.startswith("CAUSAL_INVESTIGATION_PHASE=plan"):
            return json.dumps(
                {
                    "causal_investigation": {
                        "hypotheses": [
                            {
                                "hypothesis_id": "h_parser",
                                "claim": "Legacy parser selection caused the failure.",
                                "explains_requirement_ids": ["case:authoritative_outcome"],
                                "falsified_if": "The modern parser was selected.",
                                "evidence_requests": [
                                    {"operation": "search_trace", "query": "EXACT_DISCRIMINATOR parser"}
                                ],
                            },
                            {
                                "hypothesis_id": "h_route",
                                "claim": "Routing selected the wrong handler.",
                                "explains_requirement_ids": ["case:authoritative_outcome"],
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
                                "request_id": "followup",
                                "operation": "read_event",
                                "trace_id": "case_001:trial_1",
                                "message_index": 7,
                            }
                        ]
                    }
                }
            )
        if prompt.startswith("CAUSAL_ASSESSMENT_PHASE=entailment_audit"):
            return json.dumps(
                {
                    "assessment_audits": [
                        {
                            "diagnosis_index": 1,
                            "hypothesis_id": "h_parser",
                            "claimed_status": "supported",
                            "evidence_entails_status": True,
                            "evidence_independent": True,
                            "exact_entailment": "The exact event directly records legacy mode.",
                            "approved": True,
                            "missing_discriminator": "",
                        }
                    ]
                }
            )
        if prompt.startswith("CAUSAL_HANDOFF_PHASE=audit"):
            return json.dumps(
                {
                    "diagnosis_audits": [
                        {
                            "diagnosis_index": 1,
                            "selected_hypothesis_id": "h_parser",
                            "hypothesis_binding": True,
                            "runtime_decidable": True,
                            "public_contract_consistent": True,
                            "decision_rule_entailed": True,
                            "decision_rule_source": "public_task_contract",
                            "decision_rule_evidence": "The public input requires the compatible parser.",
                            "evaluation_independent": True,
                            "single_intervention": True,
                            "approved": True,
                            "violations": [],
                        }
                    ]
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
                        "selected_hypothesis_id": "h_parser",
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
                                "evidence_relation": "direct_claim",
                                "evidence_independence": "direct_observation",
                                "logic_check": "The observed mode equals the legacy mode predicted by h_parser.",
                                "controller_request_ids": ["refine_followup"],
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

    assert len(prompts) == 7
    assert prompts[1].startswith("CAUSAL_INVESTIGATION_PHASE=diagnose")
    assert prompts[2].startswith("CAUSAL_INVESTIGATION_PHASE=refine")
    assert prompts[3].startswith("CAUSAL_INVESTIGATION_PHASE=diagnose")
    assert prompts[4].startswith("CAUSAL_ASSESSMENT_PHASE=entailment_audit")
    assert prompts[5].startswith("CAUSAL_INVESTIGATION_PHASE=refine")
    assert prompts[6].startswith("CAUSAL_HANDOFF_PHASE=audit")
    assert results[0]["causal_investigation"]["planning_status"] == "completed"
    assert results[0]["causal_investigation"]["refinement_status"] == "completed"
    assert results[0]["causal_investigation"]["refinement_request_count"] == 1
    assert results[0]["causal_investigation"]["hypothesis_count"] == 2
    assert results[0]["causal_investigation"]["closure_termination_reason"] == "no_new_legal_request"
    assert results[0]["hypothesis_assessment"][1]["status"] == "unresolved"
    assert results[0]["causal_investigation"]["hypothesis_entailment_audit"]["status"] == "approved"


@pytest.mark.asyncio
async def test_independent_entailment_rejection_reenters_evidence_closure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openjiuwen.rsi.config import EvaluationResultAnalyzerConfig
    from openjiuwen.rsi.evaluation_result_analyzer import analyzer as analyzer_module

    case = _case(tmp_path)
    strategy = analyzer_module.DiagnosisAgentStrategy(EvaluationResultAnalyzerConfig(model_config_ref="unused.yaml"))
    phases: list[str] = []
    diagnosis_calls = 0
    audit_calls = 0

    def diagnosis(final: bool) -> dict[str, Any]:
        selected = "h_stability" if final else "h_input"
        request_id = "refine_stability" if final else "q1"
        assessments = [
            {
                "hypothesis_id": "h_input",
                "status": "falsified" if final else "supported",
                "falsifying_condition_status": "observed" if final else "not_observed",
                "claim_follows_from_evidence": "no" if final else "yes",
                "evidence_relation": "direct_falsifier" if final else "direct_claim",
                "evidence_independence": "direct_observation",
                "logic_check": "The exact input-path event was observed.",
                "controller_request_ids": [request_id],
            },
            {
                "hypothesis_id": "h_stability",
                "status": "supported" if final else "falsified",
                "falsifying_condition_status": "not_observed" if final else "observed",
                "claim_follows_from_evidence": "yes" if final else "no",
                "evidence_relation": "direct_claim" if final else "direct_falsifier",
                "evidence_independence": "independent" if final else "direct_observation",
                "logic_check": (
                    "The independent stability probe changed the result."
                    if final
                    else "The questioned runtime returned a value and its own outputs agreed."
                ),
                "controller_request_ids": [request_id],
            },
        ]
        return {
            "diagnoses": [
                {
                    "issue_category": "member_harness",
                    "severity": "medium",
                    "summary": "The runtime accepted a result without an independent decision check.",
                    "failure_mode": "unchecked_runtime_decision",
                    "failure_cluster": {
                        "failed_checks": ["case:authoritative_outcome"],
                        "observable_behavior": "the runtime returned a value",
                    },
                    "evidence_status": "confirmed",
                    "failed_requirement": "Produce a result satisfying the public task.",
                    "selected_hypothesis_id": selected,
                    "root_cause": "The selected runtime decision lacked the required check.",
                    "critical_mistake": "The runtime accepted its own output.",
                    "general_mechanism": "Validate with evidence independent of the questioned mechanism.",
                    "target_ref": "member_harness.solver.prompt",
                    "recommendation": "Run the bounded independent check before accepting the output.",
                    "causal_coverage": {
                        "explained_requirement_ids": ["case:authoritative_outcome"],
                        "residual_requirement_ids": [],
                        "unexplained_observations": [],
                        "causal_chain": [],
                        "counterfactual_prediction": "The output is accepted only after the independent check.",
                        "sufficiency_status": "task_sufficient",
                    },
                    "decision_contract": {
                        "wrong_decision": "accept the unchecked output",
                        "causal_distinction": "an independent check is missing",
                        "required_action": "run the independent check",
                        "acceptance_observable": "the check is present and passes",
                        "scope_boundary": [],
                        "activation_phase": "pre_submission",
                    },
                    "hypothesis_assessment": assessments,
                    "confidence": "high",
                }
            ]
        }

    async def fake_build_agent(workspace: str) -> dict[str, str]:
        return {"workspace": workspace}

    async def fake_run_agent(agent: Any, prompt: str, *, max_retries: int, **kwargs: Any) -> str:
        nonlocal diagnosis_calls, audit_calls
        del agent, max_retries, kwargs
        phase = prompt.splitlines()[0]
        phases.append(phase)
        if phase == "CAUSAL_INVESTIGATION_PHASE=plan":
            return json.dumps(
                {
                    "causal_investigation": {
                        "hypotheses": [
                            {
                                "hypothesis_id": "h_input",
                                "claim": "The wrong task-visible input path was selected.",
                                "explains_requirement_ids": ["case:authoritative_outcome"],
                                "falsified_if": "The exact event records the required input path.",
                                "evidence_requests": [
                                    {
                                        "request_id": "q1",
                                        "operation": "read_event",
                                        "trace_id": "case_001:trial_1",
                                        "message_index": 7,
                                    }
                                ],
                            },
                            {
                                "hypothesis_id": "h_stability",
                                "claim": "The approximate result was accepted before stability was established.",
                                "explains_requirement_ids": ["case:authoritative_outcome"],
                                "falsified_if": "An independent stability probe is stable at required precision.",
                                "evidence_requests": [
                                    {
                                        "request_id": "q1",
                                        "operation": "read_event",
                                        "trace_id": "case_001:trial_1",
                                        "message_index": 7,
                                    }
                                ],
                            },
                        ]
                    }
                }
            )
        if phase == "CAUSAL_ASSESSMENT_PHASE=entailment_audit":
            audit_calls += 1
            first = audit_calls == 1
            return json.dumps(
                {
                    "assessment_audits": [
                        {
                            "diagnosis_index": 1,
                            "hypothesis_id": "h_input",
                            "claimed_status": "supported" if first else "falsified",
                            "evidence_entails_status": True,
                            "evidence_independent": True,
                            "exact_entailment": "The input-path event is directly observed.",
                            "approved": True,
                            "missing_discriminator": "",
                        },
                        {
                            "diagnosis_index": 1,
                            "hypothesis_id": "h_stability",
                            "claimed_status": "falsified" if first else "supported",
                            "evidence_entails_status": not first,
                            "evidence_independent": not first,
                            "exact_entailment": "Same-mechanism agreement is not a stability check.",
                            "approved": not first,
                            "missing_discriminator": (
                                "Run a bounded independent stability or perturbation probe." if first else ""
                            ),
                        },
                    ]
                }
            )
        if phase == "CAUSAL_INVESTIGATION_PHASE=refine":
            assert "bounded independent stability" in prompt
            return json.dumps(
                {
                    "causal_investigation": {
                        "evidence_requests": [
                            {
                                "request_id": "stability",
                                "operation": "read_event",
                                "trace_id": "case_001:trial_1",
                                "message_index": 7,
                                "tool_call_index": 1,
                            }
                        ]
                    }
                }
            )
        if phase == "CAUSAL_HANDOFF_PHASE=audit":
            return json.dumps(
                {
                    "diagnosis_audits": [
                        {
                            "diagnosis_index": 1,
                            "selected_hypothesis_id": "h_stability",
                            "hypothesis_binding": True,
                            "runtime_decidable": True,
                            "public_contract_consistent": True,
                            "decision_rule_entailed": True,
                            "decision_rule_source": "runtime_safety_invariant",
                            "decision_rule_evidence": "Run an independent bounded check before release.",
                            "evaluation_independent": True,
                            "single_intervention": True,
                            "approved": True,
                            "violations": [],
                        }
                    ]
                }
            )
        diagnosis_calls += 1
        return json.dumps(diagnosis(diagnosis_calls > 1))

    def fake_execute(case_input: Any, investigation: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        del case_input, kwargs
        results = [
            {
                "request_id": str(request.get("request_id", "")),
                "operation": str(request.get("operation", "")),
                "availability": "available",
                "summary": "exact bounded event",
            }
            for request in investigation.get("evidence_requests", [])
        ]
        return {"results": results, "completed_request_count": len(results)}

    monkeypatch.setattr(strategy, "_build_agent", fake_build_agent)
    monkeypatch.setattr(analyzer_module, "_run_agent", fake_run_agent)
    monkeypatch.setattr(analyzer_module, "execute_causal_investigation", fake_execute)
    monkeypatch.setattr(analyzer_module, "_case_diagnoses_validation_conflicts", lambda *args, **kwargs: [])
    monkeypatch.setattr(analyzer_module, "_causal_investigation_conflicts", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        analyzer_module,
        "_reconcile_causal_assessments",
        lambda diagnoses, *args, **kwargs: (diagnoses, []),
    )

    results = await strategy._per_case_diagnosis([case], DeterministicSignals(method="script_based"), None)

    assert phases == [
        "CAUSAL_INVESTIGATION_PHASE=plan",
        "CAUSAL_INVESTIGATION_PHASE=diagnose",
        "CAUSAL_ASSESSMENT_PHASE=entailment_audit",
        "CAUSAL_INVESTIGATION_PHASE=refine",
        "CAUSAL_INVESTIGATION_PHASE=diagnose",
        "CAUSAL_ASSESSMENT_PHASE=entailment_audit",
        "CAUSAL_HANDOFF_PHASE=audit",
    ]
    record = results[0]["causal_investigation"]
    audit = record["independent_hypothesis_entailment_audit"]
    assert audit["attempt_count"] == 2
    assert audit["attempts"][0]["status"] == "needs_evidence"
    assert audit["attempts"][1]["status"] == "approved"
    assert record["closure_rounds"][0]["request_ids"] == ["refine_stability"]
    assert results[0]["selected_hypothesis_id"] == "h_stability"


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

    async def fake_run_agent(agent: Any, prompt: str, *, max_retries: int, **kwargs: Any) -> str:
        del agent, max_retries, kwargs
        prompts.append(prompt)
        if prompt.startswith("CAUSAL_HANDOFF_PHASE=audit"):
            return json.dumps(
                {
                    "diagnosis_audits": [
                        {
                            "diagnosis_index": 1,
                            "selected_hypothesis_id": "h_parser",
                            "hypothesis_binding": True,
                            "runtime_decidable": True,
                            "public_contract_consistent": True,
                            "decision_rule_entailed": True,
                            "decision_rule_source": "public_task_contract",
                            "decision_rule_evidence": "The public input requires the compatible parser.",
                            "evaluation_independent": True,
                            "single_intervention": True,
                            "approved": True,
                            "violations": [],
                        }
                    ]
                }
            )
        if len(prompts) == 1:
            return json.dumps(
                {
                    "diagnoses": [
                        {
                            "evidence_status": "confirmed",
                            "selected_hypothesis_id": "h_parser",
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
                                "explains_requirement_ids": ["case:authoritative_outcome"],
                                "falsified_if": "The modern parser was selected.",
                                "evidence_requests": [
                                    {"operation": "search_trace", "query": "EXACT_DISCRIMINATOR parser"}
                                ],
                            },
                            {
                                "hypothesis_id": "h_route",
                                "claim": "Routing selected the wrong handler.",
                                "explains_requirement_ids": ["case:authoritative_outcome"],
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

    assert len(prompts) == 5
    assert "CAUSAL_INVESTIGATION_PHASE=refine" in prompts[3]
    assert "CAUSAL_INVESTIGATION_PHASE=refine" in prompts[4]
    assert results[0]["target_ref"] == "unassigned"
    assert results[0]["causal_investigation"]["strict_plan_correction_attempted"] is True
    assert results[0]["causal_investigation"]["hypothesis_count"] == 2
    assert results[0]["causal_investigation"]["closure_termination_reason"] == "no_new_legal_request"


@pytest.mark.asyncio
async def test_handoff_audit_reenters_evidence_and_reaudits_assigned_diagnosis(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openjiuwen.rsi.config import EvaluationResultAnalyzerConfig
    from openjiuwen.rsi.evaluation_result_analyzer import analyzer as analyzer_module

    case = _case(tmp_path)
    strategy = analyzer_module.DiagnosisAgentStrategy(EvaluationResultAnalyzerConfig(model_config_ref="unused.yaml"))
    prompts: list[str] = []
    audit_calls = 0
    closure_calls = 0

    def diagnosis(request_id: str) -> dict[str, Any]:
        evidence_ref = {"trace_id": "case_001:trial_1", "message_index": 7}
        return {
            "diagnoses": [
                {
                    "issue_category": "member_harness",
                    "severity": "medium",
                    "summary": "The runtime selected the legacy parser mode.",
                    "failure_mode": "legacy_parser_selection",
                    "failure_cluster": {
                        "failed_checks": ["case:authoritative_outcome"],
                        "observable_behavior": "legacy mode selected",
                    },
                    "evidence_status": "confirmed",
                    "failed_requirement": "Complete the public task with the compatible parser.",
                    "competing_hypotheses": ["legacy parser", "modern parser"],
                    "discriminating_evidence": "The exact event records legacy mode.",
                    "selected_hypothesis_id": "h_legacy",
                    "root_cause": "The runtime selected legacy parser mode.",
                    "critical_mistake": "The parser-mode decision selected legacy.",
                    "general_mechanism": "Select the parser mode required by a task-visible source.",
                    "target_ref": "member_harness.solver.prompt",
                    "evidence_refs": [evidence_ref],
                    "affected_components": ["solver"],
                    "recommendation": "Derive parser mode from the task-visible declaration before parsing.",
                    "causal_coverage": {
                        "explained_requirement_ids": ["case:authoritative_outcome"],
                        "residual_requirement_ids": [],
                        "unexplained_observations": [],
                        "causal_chain": [
                            {
                                "cause": "legacy mode selected",
                                "effect": "the public task failed",
                                "evidence_status": "observed",
                                "evidence_refs": [evidence_ref],
                            }
                        ],
                        "counterfactual_prediction": "The declared parser mode is selected.",
                        "sufficiency_status": "task_sufficient",
                    },
                    "decision_contract": {
                        "wrong_decision": "legacy mode selected",
                        "causal_distinction": "a task-visible declaration requires another mode",
                        "required_action": "select the declared mode",
                        "acceptance_observable": "the selected mode equals the public declaration",
                        "scope_boundary": ["Do not infer a mode from evaluator output."],
                        "activation_phase": "during_investigation",
                    },
                    "hypothesis_assessment": [
                        {
                            "hypothesis_id": "h_legacy",
                            "status": "supported",
                            "falsifying_condition_status": "not_observed",
                            "claim_follows_from_evidence": "yes",
                            "evidence_relation": "direct_claim",
                            "evidence_independence": "direct_observation",
                            "logic_check": "The exact event says legacy mode.",
                            "controller_request_ids": [request_id],
                            "reason": "observed",
                            "evidence_refs": [evidence_ref],
                        },
                        {
                            "hypothesis_id": "h_modern",
                            "status": "falsified",
                            "falsifying_condition_status": "observed",
                            "claim_follows_from_evidence": "no",
                            "evidence_relation": "direct_falsifier",
                            "evidence_independence": "direct_observation",
                            "logic_check": "The exact event contradicts modern mode.",
                            "controller_request_ids": [request_id],
                            "reason": "refuted",
                            "evidence_refs": [evidence_ref],
                        },
                    ],
                    "confidence": "high",
                }
            ]
        }

    async def fake_build_agent(workspace: str) -> dict[str, str]:
        return {"workspace": workspace}

    async def fake_run_agent(agent: Any, prompt: str, *, max_retries: int, **kwargs: Any) -> str:
        nonlocal audit_calls, closure_calls
        del agent, max_retries, kwargs
        prompts.append(prompt)
        if prompt.startswith("CAUSAL_ASSESSMENT_PHASE=entailment_audit"):
            return json.dumps(
                {
                    "assessment_audits": [
                        {
                            "diagnosis_index": 1,
                            "hypothesis_id": "h_legacy",
                            "claimed_status": "supported",
                            "evidence_entails_status": True,
                            "evidence_independent": True,
                            "exact_entailment": "The exact event directly records legacy mode.",
                            "approved": True,
                            "missing_discriminator": "",
                        },
                        {
                            "diagnosis_index": 1,
                            "hypothesis_id": "h_modern",
                            "claimed_status": "falsified",
                            "evidence_entails_status": True,
                            "evidence_independent": True,
                            "exact_entailment": "The exact event matches the frozen falsifier.",
                            "approved": True,
                            "missing_discriminator": "",
                        },
                    ]
                }
            )
        if prompt.startswith("CAUSAL_INVESTIGATION_PHASE=plan"):
            return json.dumps(
                {
                    "causal_investigation": {
                        "hypotheses": [
                            {
                                "hypothesis_id": "h_legacy",
                                "claim": "Legacy parser mode was selected.",
                                "explains_requirement_ids": ["case:authoritative_outcome"],
                                "falsified_if": "The exact event records modern mode.",
                                "evidence_requests": [
                                    {
                                        "request_id": "q1",
                                        "operation": "read_event",
                                        "trace_id": "case_001:trial_1",
                                        "message_index": 7,
                                    }
                                ],
                            },
                            {
                                "hypothesis_id": "h_modern",
                                "claim": "Modern parser mode was selected but failed later.",
                                "explains_requirement_ids": ["case:authoritative_outcome"],
                                "falsified_if": "The exact event records legacy mode.",
                                "evidence_requests": [
                                    {
                                        "request_id": "q1",
                                        "operation": "read_event",
                                        "trace_id": "case_001:trial_1",
                                        "message_index": 7,
                                    }
                                ],
                            },
                        ]
                    }
                }
            )
        if prompt.startswith("CAUSAL_INVESTIGATION_PHASE=handoff_evidence_closure"):
            closure_calls += 1
            request_id = "authority" if closure_calls == 1 else "scope"
            return json.dumps(
                {
                    "causal_investigation": {
                        "evidence_requests": [
                            {
                                "request_id": request_id,
                                "operation": "read_event",
                                "trace_id": "case_001:trial_1",
                                "message_index": 7,
                                "tool_call_index": closure_calls - 1,
                                "purpose": "Read the task-visible authority or scope used by the decision.",
                            }
                        ]
                    }
                }
            )
        if prompt.startswith("CAUSAL_HANDOFF_PHASE=audit"):
            audit_calls += 1
            approved = audit_calls == 2
            return json.dumps(
                {
                    "diagnosis_audits": [
                        {
                            "diagnosis_index": 1,
                            "selected_hypothesis_id": "h_legacy",
                            "hypothesis_binding": True,
                            "runtime_decidable": approved,
                            "public_contract_consistent": True,
                            "decision_rule_entailed": approved,
                            "decision_rule_source": "task_visible_invariant" if approved else "none",
                            "decision_rule_evidence": "exact task-visible declaration" if approved else "",
                            "evaluation_independent": True,
                            "single_intervention": True,
                            "approved": approved,
                            "violations": [] if approved else ["The authority source has not been read."],
                        }
                    ]
                }
            )
        if "refine_authority" in prompt and "refine_scope" not in prompt:
            return json.dumps(
                {
                    "diagnoses": [
                        {
                            "issue_category": "unassigned",
                            "severity": "low",
                            "summary": "Authority was found, but its exact scope is still unresolved.",
                            "failure_mode": "authority_scope_unresolved",
                            "failure_cluster": {
                                "failed_checks": ["case:authoritative_outcome"],
                                "observable_behavior": "legacy mode selected",
                            },
                            "evidence_status": "insufficient",
                            "failed_requirement": "Complete the public task with the compatible parser.",
                            "selected_hypothesis_id": "",
                            "root_cause": "The exact scope discriminator has not yet been read.",
                            "target_ref": "unassigned",
                            "recommendation": "Read the exact source span that defines parser-mode scope.",
                            "causal_coverage": {
                                "explained_requirement_ids": [],
                                "residual_requirement_ids": ["case:authoritative_outcome"],
                                "unexplained_observations": ["The scope-bearing span is still unread."],
                                "causal_chain": [],
                                "counterfactual_prediction": "No prediction until scope is read.",
                                "sufficiency_status": "unknown",
                            },
                            "hypothesis_assessment": [
                                {
                                    "hypothesis_id": "h_legacy",
                                    "status": "unresolved",
                                    "falsifying_condition_status": "not_observed",
                                    "claim_follows_from_evidence": "unknown",
                                    "logic_check": "The authority is visible, but its scope remains unread.",
                                    "controller_request_ids": ["refine_authority"],
                                    "reason": "The required scope discriminator remains obtainable.",
                                    "evidence_refs": [{"trace_id": "case_001:trial_1", "message_index": 7}],
                                },
                                {
                                    "hypothesis_id": "h_modern",
                                    "status": "falsified",
                                    "falsifying_condition_status": "observed",
                                    "claim_follows_from_evidence": "no",
                                    "evidence_relation": "direct_falsifier",
                                    "evidence_independence": "direct_observation",
                                    "logic_check": "The exact event contradicts modern mode.",
                                    "controller_request_ids": ["q1"],
                                    "reason": "refuted",
                                    "evidence_refs": [{"trace_id": "case_001:trial_1", "message_index": 7}],
                                },
                            ],
                            "confidence": "low",
                        }
                    ]
                }
            )
        request_id = "refine_scope" if "refine_scope" in prompt else "q1"
        return json.dumps(diagnosis(request_id))

    monkeypatch.setattr(strategy, "_build_agent", fake_build_agent)
    monkeypatch.setattr(analyzer_module, "_run_agent", fake_run_agent)
    monkeypatch.setattr(analyzer_module, "_case_diagnoses_validation_conflicts", lambda *args, **kwargs: [])

    results = await strategy._per_case_diagnosis([case], DeterministicSignals(method="script_based"), None)

    record = results[0]["causal_investigation"]
    handoff = record["causal_handoff_audit"]
    assert [prompt.splitlines()[0] for prompt in prompts] == [
        "CAUSAL_INVESTIGATION_PHASE=plan",
        "CAUSAL_INVESTIGATION_PHASE=diagnose",
        "CAUSAL_ASSESSMENT_PHASE=entailment_audit",
        "CAUSAL_HANDOFF_PHASE=audit",
        "CAUSAL_INVESTIGATION_PHASE=handoff_evidence_closure",
        "CAUSAL_INVESTIGATION_PHASE=diagnose",
        "CAUSAL_ASSESSMENT_PHASE=entailment_audit",
        "CAUSAL_INVESTIGATION_PHASE=handoff_evidence_closure",
        "CAUSAL_INVESTIGATION_PHASE=diagnose",
        "CAUSAL_ASSESSMENT_PHASE=entailment_audit",
        "CAUSAL_HANDOFF_PHASE=audit",
    ], (prompts, handoff)
    assert handoff["status"] == "approved"
    assert len(handoff["attempts"]) == 2
    assert handoff["evidence_closure_rounds"][0]["request_ids"] == ["refine_authority"]
    assert handoff["evidence_closure_rounds"][0]["status"] == "rediagnosed_unassigned"
    assert handoff["evidence_closure_rounds"][1]["request_ids"] == ["refine_scope"]
    assert results[0]["target_ref"] == "member_harness.solver.prompt"

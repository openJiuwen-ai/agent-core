# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.case_reader import CaseAnalysisInput
from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.evidence_investigation import (
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
    from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer import evidence_investigation

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
    from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer import evidence_investigation

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
    from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer import evidence_investigation

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
    from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer import evidence_investigation

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

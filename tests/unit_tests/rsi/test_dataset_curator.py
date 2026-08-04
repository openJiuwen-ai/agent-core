# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for LangSmith-style dataset curation from evaluation traces."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from openjiuwen.rsi.config import DatasetCurationConfig
from openjiuwen.rsi.dataset_curator import DatasetCurator


def test_dataset_curator_mines_failed_judgeable_cases_as_replay_dataset(tmp_path: Path) -> None:
    """Failed, judgeable cases become replay examples with provenance metadata."""
    dataset_dir = tmp_path / "dataset"
    dataset_dir.mkdir()
    source_cases = [
        {
            "case_id": "failed_verified",
            "input": "fix the repository",
            "dimension": "git",
            "difficulty": "medium",
            "source": "external_adapter",
            "task_type": "coding",
            "verification_contract": {"must_pass": ["repository_check"]},
        },
        {
            "case_id": "passed_verified",
            "input": "already solved",
            "source": "external_adapter",
            "task_type": "coding",
            "verification_contract": {"must_pass": ["repository_check"]},
        },
        {
            "case_id": "failed_open",
            "input": "open ended",
            "source": "manual",
            "task_type": "writing",
        },
    ]
    case_file = dataset_dir / "cases.json"
    case_file.write_text(json.dumps({"cases": source_cases}, ensure_ascii=False), encoding="utf-8")

    eval_dir = tmp_path / "eval"
    case_results_dir = eval_dir / "case_results"
    for case_id, score, passed in [
        ("failed_verified", 0.0, False),
        ("passed_verified", 1.0, True),
        ("failed_open", 0.0, False),
    ]:
        result_dir = case_results_dir / case_id
        result_dir.mkdir(parents=True)
        (result_dir / "result.json").write_text(
            json.dumps(
                {
                    "case_id": case_id,
                    "score": score,
                    "evaluation": {
                        "method": "external_verifier",
                        "passed": passed,
                        "reason": "failed" if not passed else "passed",
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (result_dir / "trace.json").write_text(
            json.dumps({"case_id": case_id, "status": "passed"}, ensure_ascii=False),
            encoding="utf-8",
        )

    eval_ref_path = eval_dir / "eval_ref.yaml"
    eval_ref_path.write_text(
        yaml.safe_dump(
            {
                "eval_id": "eval_001",
                "eval_dir": str(eval_dir),
                "case_results_dir": str(case_results_dir),
                "cases": [
                    {
                        "case_id": "failed_verified",
                        "case_path": str(case_file),
                        "case_index": 1,
                        "result_path": str(case_results_dir / "failed_verified" / "result.json"),
                        "trace_path": str(case_results_dir / "failed_verified" / "trace.json"),
                        "status": "passed",
                        "score": 0.0,
                    },
                    {
                        "case_id": "passed_verified",
                        "case_path": str(case_file),
                        "case_index": 2,
                        "result_path": str(case_results_dir / "passed_verified" / "result.json"),
                        "trace_path": str(case_results_dir / "passed_verified" / "trace.json"),
                        "status": "passed",
                        "score": 1.0,
                    },
                    {
                        "case_id": "failed_open",
                        "case_path": str(case_file),
                        "case_index": 3,
                        "result_path": str(case_results_dir / "failed_open" / "result.json"),
                        "trace_path": str(case_results_dir / "failed_open" / "trace.json"),
                        "status": "passed",
                        "score": 0.0,
                    },
                ],
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    artifact = DatasetCurator(DatasetCurationConfig()).curate(
        eval_ref_path=str(eval_ref_path),
        output_dir=str(tmp_path / "curated"),
    )

    replay = json.loads(Path(artifact.dataset_file).read_text(encoding="utf-8"))
    assert [case["case_id"] for case in replay["cases"]] == ["replay_failed_verified"]
    replay_case = replay["cases"][0]
    assert replay_case["input"] == "fix the repository"
    assert replay_case["metadata"]["source"] == "trace_replay"
    assert replay_case["metadata"]["provenance"]["source_case_id"] == "failed_verified"
    assert replay_case["metadata"]["provenance"]["source_eval_ref_path"] == str(eval_ref_path.resolve())
    assert replay_case["metadata"]["judgeable"] is True

    report = yaml.safe_load(Path(artifact.report_path).read_text(encoding="utf-8"))
    assert report["summary"] == {
        "candidate_cases": 3,
        "accepted_cases": 1,
        "rejected_cases": 2,
    }
    rejected = {item["case_id"]: item["reason"] for item in report["rejected_cases"]}
    assert rejected == {
        "passed_verified": "case_passed_threshold",
        "failed_open": "missing_judgeable_reference",
    }


def test_dataset_curator_rejects_inconclusive_error_cases(tmp_path: Path) -> None:
    """Infrastructure/error cases must not become replay training signal."""
    dataset_dir = tmp_path / "dataset"
    dataset_dir.mkdir()
    case_file = dataset_dir / "cases.json"
    case_file.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "case_id": "timeout_case",
                        "input": "fix repo",
                        "verification_contract": {"must_pass": ["repository_check"]},
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    eval_dir = tmp_path / "eval"
    result_dir = eval_dir / "case_results" / "timeout_case"
    result_dir.mkdir(parents=True)
    result_path = result_dir / "result.json"
    result_path.write_text(
        json.dumps(
            {
                "case_id": "timeout_case",
                "status": "error",
                "execution_status": "passed",
                "score": 0.0,
                "evaluation": {
                    "method": "error",
                    "passed": False,
                    "reason": "judge timed out",
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    eval_ref_path = eval_dir / "eval_ref.yaml"
    eval_ref_path.write_text(
        yaml.safe_dump(
            {
                "cases": [
                    {
                        "case_id": "timeout_case",
                        "case_path": str(case_file),
                        "case_index": 1,
                        "result_path": str(result_path),
                        "status": "error",
                        "score": 0.0,
                    }
                ]
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    artifact = DatasetCurator(DatasetCurationConfig()).curate(
        eval_ref_path=str(eval_ref_path),
        output_dir=str(tmp_path / "curated"),
    )

    assert artifact.dataset_file == ""
    report = yaml.safe_load(Path(artifact.report_path).read_text(encoding="utf-8"))
    assert report["summary"]["accepted_cases"] == 0
    assert report["rejected_cases"] == [{"case_id": "timeout_case", "reason": "case_result_inconclusive"}]


def test_dataset_curator_writes_targeted_seed_from_failed_training_signal(
    tmp_path: Path,
) -> None:
    """Failed synthetic cases should produce targeted seeds for later generation."""
    dataset_dir = tmp_path / "dataset"
    dataset_dir.mkdir()
    case_file = dataset_dir / "cases.json"
    case_file.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "case_id": "card_game_dom_sync",
                        "input": {"user_message": ("Build a browser card game where card clicks update DOM state.")},
                        "reference": {
                            "required_behaviors": [
                                {
                                    "id": "dom_sync",
                                    "description": "Card clicks update visible state.",
                                }
                            ],
                            "success_criteria": [
                                "Clicking a card removes it from the hand.",
                                "HP and status text update after the turn.",
                            ],
                            "verifier": {
                                "type": "artifact_check",
                                "check_method": "Inspect DOM and JS selectors.",
                                "test_cases_or_rules": ["click card and inspect DOM"],
                            },
                        },
                        "training_signal": {
                            "target_capabilities": [
                                "dom_interaction_wiring",
                                "state_management",
                            ],
                            "capability_combination": "dom_state_sync",
                            "expected_failure_modes": ["selector mismatch"],
                            "capability_gap": ("The agent writes HTML and JS without a reusable ID contract."),
                            "target_surfaces": ["skill", "prompt_section"],
                        },
                        "metadata": {
                            "dimension": "dom_id_js_wiring_consistency",
                            "difficulty": "hard",
                        },
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    eval_dir = tmp_path / "eval"
    result_dir = eval_dir / "case_results" / "card_game_dom_sync"
    result_dir.mkdir(parents=True)
    result_path = result_dir / "result.json"
    result_path.write_text(
        json.dumps(
            {
                "case_id": "card_game_dom_sync",
                "status": "passed",
                "score": 0.35,
                "evaluation": {
                    "method": "llm_as_judge",
                    "passed": False,
                    "reason": "Clicking cards did not update visible game state.",
                    "behavior_results": [
                        {
                            "behavior_id": "dom_sync",
                            "score": 0.2,
                            "failure_reason": "JS selector referenced a missing DOM id.",
                            "missing_capability": "DOM-to-state contract validation.",
                            "evidence": "getElementById('player-hand') returned null.",
                        }
                    ],
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    trace_path = result_dir / "trace.json"
    trace_path.write_text(
        json.dumps(
            {
                "events": [
                    {
                        "role": "solver",
                        "message": "Wrote click handler for #player-card-list.",
                    },
                    {
                        "role": "verifier",
                        "message": "Runtime failed because #player-hand was missing.",
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    eval_ref_path = eval_dir / "eval_ref.yaml"
    eval_ref_path.write_text(
        yaml.safe_dump(
            {
                "cases": [
                    {
                        "case_id": "card_game_dom_sync",
                        "case_path": str(case_file),
                        "case_index": 1,
                        "result_path": str(result_path),
                        "trace_path": str(trace_path),
                        "status": "passed",
                        "score": 0.35,
                    }
                ]
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    artifact = DatasetCurator(DatasetCurationConfig()).curate(
        eval_ref_path=str(eval_ref_path),
        output_dir=str(tmp_path / "curated"),
    )

    seed_path = Path(artifact.output_dir) / "targeted_dataset_seed.json"
    seed = json.loads(seed_path.read_text(encoding="utf-8"))
    task = seed["recommended_synthetic_tasks"][0]
    assert task["source_case_id"] == "card_game_dom_sync"
    assert task["target_capabilities"] == [
        "dom_interaction_wiring",
        "state_management",
    ]
    assert task["capability_combination"] == "dom_state_sync"
    assert task["specific_trap_to_include"] == "selector mismatch"
    assert task["success_criteria"] == [
        "Clicking a card removes it from the hand.",
        "HP and status text update after the turn.",
    ]
    assert task["root_cause_capabilities"][0]["capability_name"] == ("DOM-to-state contract validation.")
    assert task["trace_evidence"]["trace_path"] == str(trace_path)
    assert "#player-hand was missing" in task["trace_evidence"]["excerpt"]

    report = yaml.safe_load(Path(artifact.report_path).read_text(encoding="utf-8"))
    assert report["targeted_dataset_seed_file"] == str(seed_path.resolve())


def test_dataset_curator_disabled_writes_no_dataset(tmp_path: Path) -> None:
    """Disabled curation is an explicit no-op artifact."""
    eval_ref_path = tmp_path / "eval_ref.yaml"
    eval_ref_path.write_text(yaml.safe_dump({"cases": []}), encoding="utf-8")

    artifact = DatasetCurator(DatasetCurationConfig(enabled=False)).curate(
        eval_ref_path=str(eval_ref_path),
        output_dir=str(tmp_path / "curated"),
    )

    assert artifact.status == "disabled"
    assert artifact.dataset_file == ""
    assert Path(artifact.report_path).is_file()

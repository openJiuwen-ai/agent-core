# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for the official Evo-Bench to RSI evaluation adapter."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import Any

import pytest
import yaml

from examples.rsi.evobench import rsi_evaluator
from openjiuwen.rsi.evaluation_result_analyzer.case_reader import CaseReader


TASK_PASS = "claw-T001_pass"
TASK_FAIL = "claw-T002_fail"
TASK_OFFICE = "gdpval-office-pass"


def test_e2b_command_uses_native_python_and_office_single_trial(tmp_path: Path) -> None:
    command = rsi_evaluator._build_command(
        root=tmp_path,
        suite_path=tmp_path / "suite.json",
        harness_path=tmp_path / "harness",
        official_eval_dir=tmp_path / "evaluation",
        policy_config=tmp_path / "policy.json",
        judge_config=tmp_path / "judge.json",
        rollout_concurrency=8,
        execution_mode="e2b",
    )

    assert command[0] != "wsl.exe"
    assert command[1:4] == ["-m", "evobench", "run-validation-eval"]
    assert command[command.index("--trials") + 1] == "1"
    assert command[command.index("--trials-by-domain") + 1] == "general=3"


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_model_config(path: Path, *, model: str) -> None:
    path.write_text(
        yaml.safe_dump(
            {
                "model_client_config": {
                    "api_base": "https://model.invalid/v1",
                    "api_key": "test-only-key",
                },
                "model_request_config": {"model": model},
            }
        ),
        encoding="utf-8",
    )


def test_analysis_artifact_snapshot_copies_only_bounded_task_files(tmp_path: Path) -> None:
    workspace = tmp_path / "official_workspace"
    workspace.mkdir()
    (workspace / "contract.txt").write_text("controlling contract clause", encoding="utf-8")
    (workspace / "unsafe.py").write_text("raise SystemExit", encoding="utf-8")
    staged = tmp_path / "evaluation" / "rollouts" / TASK_OFFICE / "evidence_workspace"
    staged.mkdir(parents=True)
    (staged / "result.xlsx").write_bytes(b"spreadsheet")

    snapshot = rsi_evaluator._materialize_analysis_artifacts(
        case_dir=tmp_path / "case",
        official_result={"workspace_path": str(workspace)},
        official_eval_dir=tmp_path / "evaluation",
        task_id=TASK_OFFICE,
    )

    snapshot_root = Path(snapshot["path"])
    assert snapshot["availability"] == "available"
    assert snapshot["file_count"] == 2
    assert (snapshot_root / "contract.txt").read_text(encoding="utf-8") == "controlling contract clause"
    assert (snapshot_root / "result.xlsx").is_file()
    assert not (snapshot_root / "unsafe.py").exists()


def test_analysis_artifact_snapshot_shortens_deep_destination_paths(tmp_path: Path) -> None:
    workspace = tmp_path / "official_workspace"
    source = workspace / "Authorization Documents" / "SECRETARY'S CERTIFICATE of AIAG.docx"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"contract")
    deep_case_dir = tmp_path.joinpath(*(["long_evaluation_segment"] * 3), "case")

    snapshot = rsi_evaluator._materialize_analysis_artifacts(
        case_dir=deep_case_dir,
        official_result={"workspace_path": str(workspace)},
        official_eval_dir=tmp_path / "evaluation",
        task_id=TASK_OFFICE,
    )

    copied = snapshot["files"][0]
    assert copied["source_path"] == "Authorization Documents/SECRETARY'S CERTIFICATE of AIAG.docx"
    assert copied["path"].startswith("__longpath__/")
    copied_path = Path(snapshot["path"]) / copied["path"]
    assert len(str(copied_path)) <= rsi_evaluator._SAFE_SNAPSHOT_PATH_CHARS
    with open(rsi_evaluator._filesystem_path(copied_path), "rb") as artifact:
        assert artifact.read() == b"contract"


def _fake_root(tmp_path: Path) -> Path:
    root = tmp_path / "Evo-Bench"
    (root / "evobench").mkdir(parents=True)
    (root / "evobench" / "cli.py").write_text("", encoding="utf-8")
    (root / "policy_harness_seed").mkdir()
    (root / ".claw-venv" / "bin").mkdir(parents=True)
    (root / "external" / "claw-eval").mkdir(parents=True)
    _write_json(
        root / "benchmark" / "suites" / "evobench_validation.json",
        {
            "validation": [
                {"id": TASK_PASS, "domain": "General", "prompt": "complete the pass task"},
                {"id": TASK_FAIL, "domain": "General", "prompt": "complete the fail task"},
                {"id": TASK_OFFICE, "domain": "office", "prompt": "create the requested office document"},
            ]
        },
    )
    return root


def _write_harness_refs(path: Path, harness: Path) -> None:
    path.write_text(
        yaml.safe_dump({"version": 1, "harness_refs": {"policy_harness": str(harness)}}),
        encoding="utf-8",
    )


def _task_result(
    task_id: str,
    *,
    passed: bool,
    score: float,
    domain: str = "general",
) -> dict[str, Any]:
    trial_count = 3 if domain == "general" else 1
    trial_scores = [score] * trial_count
    return {
        "task_id": task_id,
        "domain": domain,
        "metric_family": "claw" if domain == "general" else "rubric",
        "score": score,
        "pass_at_k": passed,
        "pass_hat_k": passed,
        "passed": passed,
        "final_answer": f"answer for {task_id}",
        "score_reason": f"official score {score}",
        "trial_scores": trial_scores,
        "trial_passed": [passed] * trial_count,
        "trial_exit_reasons": ["finished"] * trial_count,
        "runtime_errors": [],
    }


def _write_official_result(
    evaluation_dir: Path,
    *,
    harness: Path,
    tasks: list[dict[str, Any]],
    score_payloads: dict[str, list[dict[str, Any]]] | None = None,
) -> Path:
    _write_json(
        evaluation_dir / "result.json",
        {
            "policy_harness_dir": rsi_evaluator._to_wsl(harness),  # pylint: disable=protected-access
            "tasks": tasks,
        },
    )
    for task in tasks:
        trial_count = len(task.get("trial_scores") or [])
        for trial_index in range(1, trial_count + 1):
            trial_root = evaluation_dir / "rollouts" / task["task_id"]
            trial_dir = trial_root / f"trial_{trial_index}" if trial_count > 1 else trial_root
            _write_json(
                trial_dir / "trajectory.json",
                {
                    "rollout_id": f"{task['task_id']}-r{trial_index}",
                    "messages": [
                        {"role": "user", "content": f"request trial {trial_index}"},
                        {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": f"call-{trial_index}",
                                    "function": {"name": "read_data", "arguments": '{"id": 1}'},
                                }
                            ],
                        },
                        {
                            "role": "tool",
                            "tool_call_id": f"call-{trial_index}",
                            "content": "tool output",
                        },
                        {"role": "assistant", "content": "done"},
                    ],
                },
            )
            trial_scores = task.get("trial_scores") or []
            trial_passed = task.get("trial_passed") or []
            score = trial_scores[trial_index - 1] if len(trial_scores) >= trial_index else task["score"]
            passed = trial_passed[trial_index - 1] if len(trial_passed) >= trial_index else task["passed"]
            payload = {
                "score": score,
                "passed": passed,
                "score_reason": f"claw_grader: C={score:.2f} R=1.00 M=0.00 S=1.0 -> {score:.2f}",
            }
            if score_payloads and task["task_id"] in score_payloads:
                payload = score_payloads[task["task_id"]][trial_index - 1]
            _write_json(trial_dir / "score.json", payload)
    return evaluation_dir / "result.json"


@pytest.mark.asyncio
async def test_reuses_h0_and_materializes_pass_hat_k_objective(tmp_path: Path) -> None:
    root = _fake_root(tmp_path)
    refs_path = tmp_path / "harness_refs.yaml"
    _write_harness_refs(refs_path, root / "policy_harness_seed")
    result_path = _write_official_result(
        tmp_path / "existing" / "evaluation",
        harness=root / "policy_harness_seed",
        tasks=[
            _task_result(TASK_PASS, passed=True, score=0.73),
            _task_result(TASK_FAIL, passed=False, score=0.91),
        ],
    )

    eval_ref_path = await rsi_evaluator.evaluate_batch(
        [{"case_id": TASK_PASS}, {"case_id": TASK_FAIL}],
        "",
        str(refs_path),
        str(tmp_path / "rsi-eval"),
        None,
        existing_official_result=str(result_path),
        evobench_root=str(root),
    )

    eval_ref = yaml.safe_load(Path(eval_ref_path).read_text(encoding="utf-8"))
    assert [case["score"] for case in eval_ref["cases"]] == [1.0, 0.0]
    assert [case["metadata"]["aggregate_mean_score"] for case in eval_ref["cases"]] == [0.73, 0.91]
    summary = json.loads(Path(eval_ref["summary_path"]).read_text(encoding="utf-8"))
    assert summary["average_score"] == 0.5
    assert summary["passed_cases"] == 1

    result = json.loads(Path(eval_ref["cases"][0]["result_path"]).read_text(encoding="utf-8"))
    assert result["score"] == 0.73
    assert result["evaluation"]["passed"] is True
    assert result["evaluation"]["metadata"]["aggregate_mean_score"] == 0.73
    assert result["evaluation"]["metadata"]["trial_scores"] == [0.73, 0.73, 0.73]
    assert result["evaluation"]["metadata"]["optimization_signals"]["continuous_score"] == {
        "availability": "available",
        "value": 0.73,
        "source": "official_evobench_trial_mean",
    }
    assert result["evaluation"]["metadata"]["optimization_signals"]["promotion_authority"] == ("eval_ref_case_score")

    normalized_path = Path(eval_ref["cases"][0]["trace_path"]).parent / "judge" / "normalized_trace.json"
    normalized = json.loads(normalized_path.read_text(encoding="utf-8"))
    assert len(normalized["traces"]) == 3
    assert normalized["traces"][0]["messages"][1]["tool_calls"][0] == {
        "name": "read_data",
        "input": '{"id": 1}',
        "output": "tool output",
        "error": "",
        "step_pointer": "trial_1:message_1",
    }
    analyzer_cases = CaseReader.read_case_inputs(eval_ref["case_results_dir"])
    assert [case.score for case in analyzer_cases] == [0.73, 0.91]
    assert len(analyzer_cases[0].normalized_trace_summary["traces"]) == 3


@pytest.mark.asyncio
async def test_materializes_mixed_general_and_single_trial_office_tasks(tmp_path: Path) -> None:
    root = _fake_root(tmp_path)
    refs_path = tmp_path / "harness_refs.yaml"
    _write_harness_refs(refs_path, root / "policy_harness_seed")
    result_path = _write_official_result(
        tmp_path / "existing" / "evaluation",
        harness=root / "policy_harness_seed",
        tasks=[
            _task_result(TASK_PASS, passed=True, score=0.73),
            _task_result(TASK_OFFICE, passed=True, score=0.81, domain="office"),
        ],
    )

    eval_ref_path = await rsi_evaluator.evaluate_batch(
        [{"case_id": TASK_PASS}, {"case_id": TASK_OFFICE}],
        "",
        str(refs_path),
        str(tmp_path / "rsi-eval"),
        None,
        existing_official_result=str(result_path),
        evobench_root=str(root),
    )

    eval_ref = yaml.safe_load(Path(eval_ref_path).read_text(encoding="utf-8"))
    assert [case["score"] for case in eval_ref["cases"]] == [1.0, 1.0]
    assert eval_ref["official_metrics"]["primary_metric"] == "strict_task_pass_rate"
    assert eval_ref["official_metrics"]["domain_counts"] == {"general": 1, "office": 1}
    office_result = json.loads(Path(eval_ref["cases"][1]["result_path"]).read_text(encoding="utf-8"))
    assert office_result["evaluation"]["metadata"]["domain"] == "office"
    assert office_result["evaluation"]["metadata"]["trial_count"] == 1
    assert len(office_result["trial_details"]) == 1
    assert office_result["trial_details"][0]["source"]["score_path"].endswith(f"rollouts\\{TASK_OFFICE}\\score.json")


@pytest.mark.asyncio
async def test_infrastructure_skip_is_materialized_but_excluded_from_metrics(tmp_path: Path) -> None:
    root = _fake_root(tmp_path)
    refs_path = tmp_path / "harness_refs.yaml"
    _write_harness_refs(refs_path, root / "policy_harness_seed")
    skipped = {
        "task_id": TASK_OFFICE,
        "domain": "office",
        "score_reason": "apex_grader_error: timed out",
        rsi_evaluator.INFRASTRUCTURE_SKIP_KEY: {
            "reason": "apex_grader_error: timed out",
            "excluded_from_metrics": True,
        },
    }
    result_path = _write_official_result(
        tmp_path / "existing" / "evaluation",
        harness=root / "policy_harness_seed",
        tasks=[_task_result(TASK_PASS, passed=True, score=0.73), skipped],
    )

    eval_ref_path = await rsi_evaluator.evaluate_batch(
        [{"case_id": TASK_PASS}, {"case_id": TASK_OFFICE}],
        "",
        str(refs_path),
        str(tmp_path / "rsi-eval"),
        None,
        existing_official_result=str(result_path),
        evobench_root=str(root),
    )

    eval_ref = yaml.safe_load(Path(eval_ref_path).read_text(encoding="utf-8"))
    assert [case["status"] for case in eval_ref["cases"]] == ["passed", "skipped"]
    assert [case["score"] for case in eval_ref["cases"]] == [1.0, None]
    assert eval_ref["official_metrics"]["primary_score"] == 1.0
    assert eval_ref["official_metrics"]["task_count"] == 1
    assert eval_ref["official_metrics"]["requested_task_count"] == 2
    assert eval_ref["official_metrics"]["skipped_infrastructure_count"] == 1
    summary = json.loads(Path(eval_ref["summary_path"]).read_text(encoding="utf-8"))
    assert summary["scored_cases"] == 1
    assert summary["skipped_cases"] == 1
    assert summary["failed_cases"] == 0
    analyzer_cases = CaseReader.read_case_inputs(eval_ref["case_results_dir"])
    assert [case.case_id for case in analyzer_cases] == [TASK_PASS]


@pytest.mark.asyncio
async def test_materializes_aggregate_office_judge_criteria_for_analyzer(tmp_path: Path) -> None:
    root = _fake_root(tmp_path)
    refs_path = tmp_path / "harness_refs.yaml"
    _write_harness_refs(refs_path, root / "policy_harness_seed")
    task = _task_result(TASK_OFFICE, passed=False, score=0.0, domain="office")
    task["judge_detail"] = {
        "grading_run_status": "completed",
        "criteria": [
            {
                "verifier_id": "ver_yes_no",
                "score": 0.0,
                "status": "ok",
                "rationale": "The final answer says No, which is opposite to the required criterion.",
            }
        ],
    }
    result_path = _write_official_result(
        tmp_path / "existing" / "evaluation",
        harness=root / "policy_harness_seed",
        tasks=[task],
    )

    eval_ref_path = await rsi_evaluator.evaluate_batch(
        [{"case_id": TASK_OFFICE}],
        "",
        str(refs_path),
        str(tmp_path / "rsi-eval"),
        None,
        existing_official_result=str(result_path),
        evobench_root=str(root),
    )

    eval_ref = yaml.safe_load(Path(eval_ref_path).read_text(encoding="utf-8"))
    result = json.loads(Path(eval_ref["cases"][0]["result_path"]).read_text(encoding="utf-8"))
    evidence = result["evaluation"]["metadata"]["judge_evidence"]
    assert evidence["availability"] == "available"
    assert evidence["grading_run_status"] == "completed"
    assert evidence["criteria"] == [
        {
            "criterion_id": "ver_yes_no",
            "verifier_id": "ver_yes_no",
            "score": 0.0,
            "status": "ok",
            "rationale": "The final answer says No, which is opposite to the required criterion.",
            "source": "official_result.judge_detail.criteria",
        }
    ]
    assert result["evaluation"]["metadata"]["requirement_results"] == {
        "schema_version": 1,
        "items": [
            {
                "requirement_id": "ver_yes_no",
                "group": "requirement",
                "passed": False,
                "score": 0.0,
                "evidence": "The final answer says No, which is opposite to the required criterion.",
                "status": "ok",
                "source": "official_result.judge_detail.criteria",
            }
        ],
    }


@pytest.mark.asyncio
async def test_prefers_complete_apex_grades_rationale_over_truncated_result_excerpt(tmp_path: Path) -> None:
    root = _fake_root(tmp_path)
    refs_path = tmp_path / "harness_refs.yaml"
    _write_harness_refs(refs_path, root / "policy_harness_seed")
    task = _task_result(TASK_OFFICE, passed=False, score=0.0, domain="office")
    task["judge_detail"] = {
        "grading_run_status": "completed",
        "criteria": [{"verifier_id": "ver_timing", "score": 0.0, "rationale": "truncated..."}],
    }
    evaluation_dir = tmp_path / "existing" / "evaluation"
    result_path = _write_official_result(
        evaluation_dir,
        harness=root / "policy_harness_seed",
        tasks=[task],
    )
    full_rationale = "The criterion expects nine months following the expiration date, not prior to it."
    _write_json(
        evaluation_dir / "rollouts" / TASK_OFFICE / "grades.json",
        {
            "grading_run_status": "completed",
            "verifier_results": [
                {
                    "verifier_id": "ver_timing",
                    "score": 0.0,
                    "status": "ok",
                    "verifier_result_values": {"grade_rationale": full_rationale},
                }
            ],
            "scoring_results": {"final_score": 0.0},
        },
    )

    eval_ref_path = await rsi_evaluator.evaluate_batch(
        [{"case_id": TASK_OFFICE}],
        "",
        str(refs_path),
        str(tmp_path / "rsi-eval"),
        None,
        existing_official_result=str(result_path),
        evobench_root=str(root),
    )

    eval_ref = yaml.safe_load(Path(eval_ref_path).read_text(encoding="utf-8"))
    result = json.loads(Path(eval_ref["cases"][0]["result_path"]).read_text(encoding="utf-8"))
    metadata = result["evaluation"]["metadata"]
    assert metadata["judge_evidence_source"] == "rollout.grades.json"
    assert metadata["judge_evidence"]["criteria"][0]["rationale"] == full_rationale


@pytest.mark.asyncio
async def test_candidate_ignores_h0_cache_and_runs_official_protocol(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _fake_root(tmp_path)
    candidate = tmp_path / "candidate_harness"
    candidate.mkdir()
    refs_path = tmp_path / "candidate_refs.yaml"
    _write_harness_refs(refs_path, candidate)
    result_path = _write_official_result(
        tmp_path / "existing" / "evaluation",
        harness=root / "policy_harness_seed",
        tasks=[_task_result(TASK_PASS, passed=True, score=1.0)],
    )

    output_dir = tmp_path / "rsi-eval"
    monkeypatch.setattr(rsi_evaluator, "_short_official_eval_dir", lambda _: output_dir / "oe")

    def fake_run(command: list[str], cwd: Path, environment: dict[str, str]) -> subprocess.CompletedProcess[Any]:
        del cwd, environment
        _write_official_result(
            output_dir / "oe",
            harness=candidate,
            tasks=[_task_result(TASK_PASS, passed=True, score=0.9)],
        )
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(rsi_evaluator, "_run_wsl_command", fake_run)
    eval_ref_path = await rsi_evaluator.evaluate_batch(
        [{"case_id": TASK_PASS}],
        "",
        str(refs_path),
        str(output_dir),
        None,
        existing_official_result=str(result_path),
        evobench_root=str(root),
    )

    eval_ref = yaml.safe_load(Path(eval_ref_path).read_text(encoding="utf-8"))
    assert eval_ref["reused_official_result"] is False
    assert eval_ref["official_metrics"]["primary_score"] == 1.0


@pytest.mark.asyncio
async def test_candidate_runs_official_general_three_trials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _fake_root(tmp_path)
    candidate = tmp_path / "candidate_harness"
    candidate.mkdir()
    refs_path = tmp_path / "candidate_refs.yaml"
    _write_harness_refs(refs_path, candidate)
    policy_config = tmp_path / "policy.yaml"
    judge_config = tmp_path / "judge.yaml"
    _write_model_config(policy_config, model="policy-model")
    _write_model_config(judge_config, model="judge-source-model")
    output_dir = tmp_path / "candidate-eval"
    observed: dict[str, Any] = {}
    monkeypatch.setattr(rsi_evaluator, "_short_official_eval_dir", lambda _: output_dir / "oe")

    def fake_run(
        command: list[str],
        cwd: Path,
        environment: dict[str, str],
    ) -> subprocess.CompletedProcess[Any]:
        observed.update({"command": command, "cwd": cwd, "environment": environment})
        _write_official_result(
            output_dir / "oe",
            harness=candidate,
            tasks=[_task_result(TASK_PASS, passed=True, score=0.8)],
        )
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(rsi_evaluator, "_run_wsl_command", fake_run)
    eval_ref_path = await rsi_evaluator.evaluate_batch(
        [{"case_id": TASK_PASS}],
        "",
        str(refs_path),
        str(output_dir),
        None,
        evobench_root=str(root),
        policy_model_config=str(policy_config),
        judge_model_config=str(judge_config),
        rollout_concurrency=7,
    )

    command = observed["command"]
    assert command[0] == "wsl.exe"
    assert command[command.index("--policy-harness") + 1] == rsi_evaluator._to_wsl(candidate)
    assert command[command.index("--trials") + 1] == "1"
    assert command[command.index("--trials-by-domain") + 1] == "general=3"
    assert command[command.index("--rollout-concurrency") + 1] == "7"
    assert Path(eval_ref_path).is_file()


@pytest.mark.asyncio
async def test_rejects_official_general_result_without_three_trials(tmp_path: Path) -> None:
    root = _fake_root(tmp_path)
    refs_path = tmp_path / "harness_refs.yaml"
    _write_harness_refs(refs_path, root / "policy_harness_seed")
    malformed = _task_result(TASK_PASS, passed=True, score=0.9)
    malformed["trial_scores"] = [0.9, 0.9]
    result_path = _write_official_result(
        tmp_path / "existing" / "evaluation",
        harness=root / "policy_harness_seed",
        tasks=[malformed],
    )

    with pytest.raises(ValueError, match="must contain 3 trial_scores"):
        await rsi_evaluator.evaluate_batch(
            [{"case_id": TASK_PASS}],
            "",
            str(refs_path),
            str(tmp_path / "rsi-eval"),
            None,
            existing_official_result=str(result_path),
            evobench_root=str(root),
        )


@pytest.mark.asyncio
async def test_materializes_distinct_score_evidence_for_all_three_trials(tmp_path: Path) -> None:
    root = _fake_root(tmp_path)
    refs_path = tmp_path / "harness_refs.yaml"
    _write_harness_refs(refs_path, root / "policy_harness_seed")
    task = _task_result(TASK_PASS, passed=True, score=0.8)
    task["trial_scores"] = [0.9, 0.7, 0.8]
    score_payloads = {
        TASK_PASS: [
            {
                "score": 0.9,
                "passed": True,
                "score_reason": "claw_grader: C=0.90 R=1.00 M=0.10 S=1.0 -> 0.90",
                "judge_detail": {
                    "completion": 0.91,
                    "robustness": 1.0,
                    "communication": 0.1,
                    "safety": 1.0,
                    "n_dispatches": 8,
                },
            },
            {
                "score": 0.7,
                "passed": True,
                "score_reason": "claw_grader: C=0.70 R=0.80 M=0.20 S=1.0 -> 0.70",
                "judge_detail": {
                    "completion": 0.72,
                    "robustness": 0.8,
                    "communication": 0.2,
                    "safety": 1.0,
                    "n_dispatches": 15,
                },
            },
            {
                "score": 0.8,
                "passed": True,
                "score_reason": "claw_grader: C=0.80 R=0.90 M=0.30 S=1.0 -> 0.80",
            },
        ]
    }
    result_path = _write_official_result(
        tmp_path / "existing" / "evaluation",
        harness=root / "policy_harness_seed",
        tasks=[task],
        score_payloads=score_payloads,
    )

    eval_ref_path = await rsi_evaluator.evaluate_batch(
        [{"case_id": TASK_PASS}],
        "",
        str(refs_path),
        str(tmp_path / "rsi-eval"),
        None,
        existing_official_result=str(result_path),
        evobench_root=str(root),
    )

    eval_ref = yaml.safe_load(Path(eval_ref_path).read_text(encoding="utf-8"))
    assert eval_ref["cases"][0]["score"] == 1.0
    result = json.loads(Path(eval_ref["cases"][0]["result_path"]).read_text(encoding="utf-8"))
    details = result["evaluation"]["metadata"]["trial_details"]
    assert result["trial_details"] == details
    assert [item["trial_id"] for item in details] == ["trial_1", "trial_2", "trial_3"]
    assert [item["score"] for item in details] == [0.9, 0.7, 0.8]
    assert details[0]["score_reason"].startswith("claw_grader: C=0.90")
    assert details[0]["judge_detail"]["n_dispatches"] == 8
    assert details[1]["judge_detail"]["n_dispatches"] == 15
    assert details[0]["dimension_scores"]["completion"] == {
        "availability": "available",
        "value": 0.91,
        "source": "score.json.judge_detail.completion",
    }
    assert details[2]["availability"]["judge_detail"] == "not_present"
    assert details[2]["dimension_scores"]["communication"] == {
        "availability": "available",
        "value": 0.3,
        "source": "score.json.score_reason",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["missing", "invalid"])
async def test_rejects_missing_or_invalid_trial_score_file(tmp_path: Path, failure: str) -> None:
    root = _fake_root(tmp_path)
    refs_path = tmp_path / "harness_refs.yaml"
    _write_harness_refs(refs_path, root / "policy_harness_seed")
    evaluation_dir = tmp_path / "existing" / "evaluation"
    result_path = _write_official_result(
        evaluation_dir,
        harness=root / "policy_harness_seed",
        tasks=[_task_result(TASK_PASS, passed=True, score=0.8)],
    )
    score_path = evaluation_dir / "rollouts" / TASK_PASS / "trial_2" / "score.json"
    if failure == "missing":
        score_path.unlink()
    else:
        score_path.write_text("[]", encoding="utf-8")

    with pytest.raises(ValueError, match=f"official score for {TASK_PASS} trial 2"):
        await rsi_evaluator.evaluate_batch(
            [{"case_id": TASK_PASS}],
            "",
            str(refs_path),
            str(tmp_path / "rsi-eval"),
            None,
            existing_official_result=str(result_path),
            evobench_root=str(root),
        )

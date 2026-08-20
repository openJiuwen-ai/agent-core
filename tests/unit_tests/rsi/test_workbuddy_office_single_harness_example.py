# coding: utf-8
"""Tests for the WorkBuddy Office iterative single-harness example."""

from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest
import yaml

from examples.rsi.run_workbuddy_office_single_harness import (
    DEFAULT_OPTIMIZATION_MODEL,
    DEFAULT_RUN_MODEL,
    _prepare_office_harness,
    _parse_args,
    _run_async,
    _write_config,
    _write_dataset,
)
from openjiuwen.harness.resources.extension_loader import (
    find_plugin_manifest,
    load_plugin_package,
)
from openjiuwen.rsi.single_harness.iterative import (
    _verifier_deltas_by_case,
)
from openjiuwen.rsi.evaluation_result_analyzer.case_reader import (
    CaseAnalysisInput,
    EvaluationSummaryInput,
)
from openjiuwen.rsi.evaluation_result_analyzer.signal_extractor import (
    AtomicChecksSignalExtractor,
)


def _task(root: Path, task_id: str, *, difficulty: str, category: str) -> Path:
    task_dir = root / "tasks" / task_id
    task_dir.mkdir(parents=True)
    (task_dir / "task.toml").write_text(
        f"""[metadata]
difficulty = "{difficulty}"
category = "{category}"
""",
        encoding="utf-8",
    )
    (task_dir / "instruction.md").write_text("Create output/report.xlsx.", encoding="utf-8")
    return task_dir


def test_office_defaults_separate_run_and_optimization_models() -> None:
    assert DEFAULT_RUN_MODEL.name == ("token_plan_deepseek_v4_flash_single_harness.yaml")
    assert DEFAULT_OPTIMIZATION_MODEL.name == "bailian_glm5_1_single_harness.yaml"


def test_async_failure_is_persisted_in_run_directory(tmp_path: Path) -> None:
    async def fail() -> None:
        raise RuntimeError("primary failure")

    with pytest.raises(RuntimeError, match="primary failure"):
        _run_async(fail(), tmp_path)

    fatal_log = (tmp_path / "fatal_errors.log").read_text(encoding="utf-8")
    assert "RuntimeError: primary failure" in fatal_log
    assert "test_workbuddy_office_single_harness_example.py" in fatal_log


def test_dataset_adapter_keeps_native_task_and_partial_score_contract(tmp_path: Path) -> None:
    dataset_root = tmp_path / "wb-bench-office-v1.0"
    first = _task(dataset_root, "task-a", difficulty="easy", category="data-file-ops")
    _task(dataset_root, "task-b", difficulty="hard", category="doc-ops")

    path = _write_dataset(
        dataset_root=dataset_root,
        output_path=tmp_path / "cases.json",
        task_ids=["task-a"],
        limit=0,
        difficulty="",
        category="",
        timeout_sec=120,
        verifier_timeout_sec=90,
    )

    case = json.loads(path.read_text(encoding="utf-8"))["cases"][0]
    assert case["input"] == "Create output/report.xlsx."
    assert case["source"] == "workbuddy_office"
    assert case["dimension"] == "data-file-ops"
    assert case["workbuddy_office"] == {
        "dataset_id": "wb-bench-office-v1.0",
        "task_id": "task-a",
        "task_dir": str(first.resolve()),
        "timeout_sec": 120,
        "verifier_timeout_sec": 90,
        "success_score": 1.0,
    }


def test_office_config_uses_existing_single_harness_optimizer(tmp_path: Path) -> None:
    run_model = tmp_path / "run.yaml"
    optimization_model = tmp_path / "optimization.yaml"
    run_model.write_text("{}\n", encoding="utf-8")
    optimization_model.write_text("{}\n", encoding="utf-8")

    config_path = _write_config(
        run_dir=tmp_path,
        run_model=run_model,
        optimization_model=optimization_model,
        batch_size=2,
        max_epochs=3,
    )
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    assert config["evaluator"]["backend"] == "single_harness"
    assert config["evaluator"]["evaluation_method"] == "script_based"
    assert config["evaluator"]["transient_case_retry_limit"] == 5
    assert config["evaluator"]["solver_backend"] == "deep_agent"
    assert config["model_configs"]["evaluation"] == str(run_model)
    assert config["member_optimizer"]["sibling_candidate_count"] == 1
    assert config["member_optimizer"]["improver_policy_ref"] == ""
    assert config["member_optimizer"]["candidate_min_target_behavior_delta"] == 0.0
    assert config["member_optimizer"]["allowed_action_groups"] == [
        "prompt",
        "skill",
        "tool",
        "rail",
    ]
    assert config["scheduling"]["promotion_policy"] == "epoch_full_evaluation"


def test_office_config_writes_jiuwenswarm_solver_settings(tmp_path: Path) -> None:
    config_path = _write_config(
        run_dir=tmp_path,
        run_model=tmp_path / "run.yaml",
        optimization_model=tmp_path / "optimization.yaml",
        batch_size=1,
        max_epochs=1,
        sibling_candidate_count=3,
        improver_policy_ref="C:/policies/i1.yaml",
        solver_backend="jiuwenswarm",
        jiuwenswarm_executable="C:/tools/jiuwenswarm.exe",
        jiuwenswarm_python="C:/tools/python.exe",
        jiuwenswarm_expected_version="1.2.3",
        jiuwenswarm_startup_timeout_sec=75,
        jiuwenswarm_runtime_timeout_sec=2100,
        jiuwenswarm_runtime_profile="task86",
    )

    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    evaluator = config["evaluator"]
    member_optimizer = config["member_optimizer"]
    assert evaluator["solver_backend"] == "jiuwenswarm"
    assert evaluator["jiuwenswarm_executable"] == "C:/tools/jiuwenswarm.exe"
    assert evaluator["jiuwenswarm_python"] == "C:/tools/python.exe"
    assert evaluator["jiuwenswarm_expected_version"] == "1.2.3"
    assert evaluator["jiuwenswarm_startup_timeout_sec"] == 75
    assert evaluator["jiuwenswarm_runtime_timeout_sec"] == 2100
    assert evaluator["jiuwenswarm_runtime_profile"] == "task86"
    assert member_optimizer["sibling_candidate_count"] == 3
    assert member_optimizer["improver_policy_ref"] == "C:/policies/i1.yaml"


def test_office_solver_cli_reads_jiuwenswarm_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WORKBUDDY_SOLVER_BACKEND", "jiuwenswarm")
    monkeypatch.setenv("JIUWENSWARM_EXECUTABLE", "jiuwenswarm-local")
    monkeypatch.setenv("JIUWENSWARM_PYTHON", "python-local")
    monkeypatch.setenv("JIUWENSWARM_EXPECTED_VERSION", "2.0.0")
    monkeypatch.setenv("JIUWENSWARM_STARTUP_TIMEOUT_SEC", "85")
    monkeypatch.setenv("JIUWENSWARM_RUNTIME_TIMEOUT_SEC", "2200")
    monkeypatch.setenv("JIUWENSWARM_RUNTIME_PROFILE", "task86")
    monkeypatch.setenv("WORKBUDDY_SIBLING_CANDIDATE_COUNT", "4")
    monkeypatch.setenv("RSI_IMPROVER_POLICY_REF", "C:/policies/i2.yaml")
    monkeypatch.setattr(sys, "argv", ["run_workbuddy_office_single_harness.py"])

    args = _parse_args()

    assert args.solver_backend == "jiuwenswarm"
    assert args.jiuwenswarm_executable == "jiuwenswarm-local"
    assert args.jiuwenswarm_python == "python-local"
    assert args.jiuwenswarm_expected_version == "2.0.0"
    assert args.jiuwenswarm_startup_timeout_sec == 85
    assert args.jiuwenswarm_runtime_timeout_sec == 2200
    assert args.jiuwenswarm_runtime_profile == "task86"
    assert args.sibling_candidate_count == 4
    assert args.improver_policy_ref == "C:/policies/i2.yaml"


def test_office_harness_is_one_evolvable_expert_harness(tmp_path: Path) -> None:
    refs_path = _prepare_office_harness(tmp_path / "harnesses")
    refs = yaml.safe_load(refs_path.read_text(encoding="utf-8"))

    assert list(refs["harness_refs"]) == ["office_worker"]
    harness_dir = Path(refs["harness_refs"]["office_worker"])
    assert (harness_dir / "identity.md").is_file()
    assert (harness_dir / "soul.md").is_file()
    assert (harness_dir / "skills" / "office_baseline" / "SKILL.md").is_file()

    plugin = load_plugin_package(find_plugin_manifest(harness_dir))
    assert [rail.type for rail in plugin.rails] == ["core.skill_use"]
    assert plugin.rails[0].params == {
        "skill_mode": "all",
        "include_tools": False,
    }
    assert len(plugin.skills) == 1


def test_office_atomic_check_delta_is_available_to_candidate_gate(tmp_path: Path) -> None:
    def write_eval(name: str, checks: list[tuple[str, bool]]) -> Path:
        result_path = tmp_path / name / "result.json"
        result_path.parent.mkdir(parents=True)
        result_path.write_text(
            json.dumps(
                {
                    "evaluation": {
                        "metadata": {
                            "atomic_checks": [{"name": check_name, "passed": passed} for check_name, passed in checks]
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        eval_ref = tmp_path / name / "eval_ref.yaml"
        eval_ref.write_text(
            yaml.safe_dump({"cases": [{"case_id": "office-1", "result_path": str(result_path)}]}),
            encoding="utf-8",
        )
        return eval_ref

    source = write_eval("source", [("format", True), ("summary", False), ("rows", False)])
    candidate = write_eval("candidate", [("format", True), ("summary", True), ("rows", False)])

    delta = _verifier_deltas_by_case(source, candidate, {"office-1"})["office-1"]

    assert delta["newly_passed_atomic_checks"] == ["summary"]
    assert delta["remaining_failed_atomic_checks"] == ["rows"]
    assert delta["regressed_atomic_checks"] == []
    assert delta["partial_progress"] is True


def test_office_analyzer_receives_atomic_failure_details() -> None:
    case = CaseAnalysisInput(
        case_id="office-1",
        status="failed",
        score=0.5,
        input="create report.xlsx",
        expected=None,
        response="done",
        error="",
        evaluation_method="workbuddy_office_official",
        evaluation_passed=False,
        evaluation_reason="one check failed",
        evaluation_metadata={
            "atomic_checks": [
                {"name": "output_valid", "passed": True, "status": "passed"},
                {
                    "name": "summary_matches",
                    "passed": False,
                    "status": "failed",
                    "detail": "summary differs from detail rows",
                },
            ]
        },
        trace_path="",
        result_path="",
    )

    signals = AtomicChecksSignalExtractor().extract(
        EvaluationSummaryInput(
            total_cases=1,
            failed_count=1,
            average_score=0.5,
            evaluation_method="workbuddy_office_official",
        ),
        [case],
    )

    assert signals.exec_failures == []
    assert signals.judge_failures == ["office-1"]
    assert signals.method_specific["atomic_checks_by_case"]["office-1"] == {
        "passed": ["output_valid"],
        "failed": ["summary_matches"],
    }
    assert (
        signals.method_specific["failed_check_details_by_case"]["office-1"][0]["detail"]
        == "summary differs from detail rows"
    )

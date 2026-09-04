from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import yaml

from examples.rsi.run_evobench_single_harness import (
    _ensure_source_run,
    _parse_args,
    _resolve_execution_mode,
    _resolve_runtime_inputs,
    _write_config,
    _write_dataset,
    _write_seed_refs,
)
from openjiuwen.rsi.harness_rsi.config import load_auto_coordinating_harness_config


def test_entrypoint_defaults_to_one_candidate_for_single_harness_optimization() -> None:
    args = _parse_args([])

    assert args.sibling_candidate_count == 1
    assert args.improver_policy_ref == ""


def test_entrypoint_writes_canonical_dataset_refs_and_config(tmp_path: Path) -> None:
    suite = tmp_path / "suite.json"
    suite.write_text(
        json.dumps(
            {
                "validation": [
                    {
                        "id": "claw-T001",
                        "domain": "general",
                        "prompt": "complete the task",
                        "metadata": {"task_type": "office"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    dataset_path = _write_dataset(suite, tmp_path / "dataset" / "cases.json")
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    assert dataset["cases"] == [
        {
            "case_id": "claw-T001",
            "task_id": "claw-T001",
            "input": "complete the task",
            "domain": "general",
            "source": "claw",
            "task_type": "office",
        }
    ]

    evobench_root = tmp_path / "evobench"
    (evobench_root / "policy_harness_seed").mkdir(parents=True)
    refs_path = _write_seed_refs(evobench_root, tmp_path / "harnesses" / "refs.yaml")
    refs = yaml.safe_load(refs_path.read_text(encoding="utf-8"))
    assert Path(refs["harness_refs"]["policy_harness"]) == (evobench_root / "policy_harness_seed").resolve()

    run_model = tmp_path / "run.yaml"
    analysis_model = tmp_path / "analysis.yaml"
    member_optimization_model = tmp_path / "member.yaml"
    run_model.write_text("model: run\n", encoding="utf-8")
    analysis_model.write_text("model: analysis\n", encoding="utf-8")
    member_optimization_model.write_text("model: member\n", encoding="utf-8")
    config_path = _write_config(
        run_dir=tmp_path / "run",
        run_model=run_model,
        analysis_model=analysis_model,
        member_optimization_model=member_optimization_model,
        batch_size=20,
        max_epochs=1,
        sibling_candidate_count=2,
        max_issue_attempts=8,
        max_repair_rounds=1,
        improver_policy_ref="",
    )
    config = load_auto_coordinating_harness_config(str(config_path))
    assert config.data_loader.file_pattern == "cases.json"
    assert config.data_loader.batch_size == 20
    assert config.member_optimizer.sibling_candidate_count == 2
    assert config.member_optimizer.max_issue_attempts_per_batch == 8
    assert config.member_optimizer.max_repair_rounds_per_batch == 1
    assert config.evaluation_result_analyzer.max_issues == 120
    assert Path(config.model_configs.evaluation) == run_model
    assert Path(config.model_configs.analysis) == analysis_model
    assert Path(config.model_configs.member_optimization) == member_optimization_model


def test_entrypoint_resolves_portable_task_bundle(tmp_path: Path) -> None:
    task_dir = tmp_path / "task" / "portable"
    harness = task_dir / "harness" / "policy_harness"
    harness.mkdir(parents=True)
    refs = task_dir / "harness" / "harness_refs.yaml"
    refs.write_text("harness_refs:\n  policy_harness: policy_harness\n", encoding="utf-8")
    models = task_dir / "models"
    models.mkdir(parents=True)
    for name in ("evaluation", "analysis", "member_optimization", "judge"):
        (models / f"{name}.yaml").write_text("{}\n", encoding="utf-8")

    args = _parse_args(["--task-dir", str(task_dir)])
    inputs = _resolve_runtime_inputs(args)

    assert inputs.harness_refs == refs.resolve()
    assert inputs.evaluation_model == (models / "evaluation.yaml").resolve()
    assert inputs.analysis_model == (models / "analysis.yaml").resolve()
    assert inputs.member_optimization_model == (models / "member_optimization.yaml").resolve()
    assert inputs.judge_model == (models / "judge.yaml").resolve()

    normalized = _write_seed_refs(
        tmp_path / "unused_evobench",
        tmp_path / "run" / "harness_refs.yaml",
        source_path=inputs.harness_refs,
    )
    payload = yaml.safe_load(normalized.read_text(encoding="utf-8"))
    assert Path(payload["harness_refs"]["policy_harness"]) == harness.resolve()


def test_entrypoint_accepts_local_gdpval_office_cases(tmp_path: Path) -> None:
    suite = tmp_path / "suite.json"
    suite.write_text(
        json.dumps(
            {
                "validation": [
                    {
                        "id": "gdpval-office-001",
                        "domain": "office",
                        "prompt": "create a spreadsheet in outputs",
                        "metadata": {"canary": "gdpval"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    dataset_path = _write_dataset(suite, tmp_path / "dataset" / "cases.json")
    case = json.loads(dataset_path.read_text(encoding="utf-8"))["cases"][0]

    assert case["domain"] == "office"
    assert case["source"] == "gdpval"
    assert case["task_type"] == "office"


def test_entrypoint_accepts_apex_and_auto_selects_e2b(tmp_path: Path) -> None:
    suite = tmp_path / "suite.json"
    suite.write_text(
        json.dumps(
            {
                "validation": [
                    {
                        "id": "apex-office-001",
                        "domain": "office",
                        "prompt": "edit the office artifact",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    dataset_path = _write_dataset(suite, tmp_path / "dataset" / "cases.json")
    cases = json.loads(dataset_path.read_text(encoding="utf-8"))["cases"]

    assert cases[0]["source"] == "apex"
    assert _resolve_execution_mode("auto", cases) == "e2b"


def test_entrypoint_can_run_missing_h0_before_batch_optimization(tmp_path: Path, monkeypatch) -> None:
    source_run = tmp_path / "local_mix40_h0"
    received: list[str] = []

    def fake_run_local_subset(argv: list[str]) -> int:
        received.extend(argv)
        source_run.mkdir(parents=True)
        (source_run / "suite.json").write_text('{"validation": []}', encoding="utf-8")
        evaluation_dir = source_run / "evaluation"
        evaluation_dir.mkdir()
        (evaluation_dir / "result.json").write_text("{}", encoding="utf-8")
        return 0

    monkeypatch.setattr(
        "examples.rsi.run_evobench_single_harness.run_local_subset",
        fake_run_local_subset,
    )
    args = SimpleNamespace(
        auto_baseline=True,
        baseline_task_count=40,
        baseline_sample_seed=20260812,
        rollout_concurrency=5,
        run_model_config_ref=tmp_path / "run.yaml",
        optimization_model_config_ref=tmp_path / "opt.yaml",
        judge_model_config_ref=tmp_path / "judge.yaml",
        evobench_root="",
    )

    suite_path, result_path = _ensure_source_run(args, source_run)

    assert suite_path == source_run / "suite.json"
    assert result_path == source_run / "evaluation" / "result.json"
    assert received[:6] == [
        "run",
        "--resume-partial",
        "--run-name",
        "local_mix40_h0",
        "--output-dir",
        str(tmp_path),
    ]
    assert received[received.index("--task-count") + 1] == "40"

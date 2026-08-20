from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import yaml

from examples.rsi.run_evobench_single_harness import (
    _ensure_source_run,
    _resolve_execution_mode,
    _write_config,
    _write_dataset,
    _write_seed_refs,
)
from openjiuwen.rsi.config import load_auto_coordinating_harness_config


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
    optimization_model = tmp_path / "opt.yaml"
    run_model.write_text("model: run\n", encoding="utf-8")
    optimization_model.write_text("model: opt\n", encoding="utf-8")
    config_path = _write_config(
        run_dir=tmp_path / "run",
        run_model=run_model,
        optimization_model=optimization_model,
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

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest
import yaml

from examples.rsi.run_evobench_single_harness import (
    _parse_args,
    _resolve_execution_mode,
    _resolve_runtime_inputs,
    _write_config,
    _write_dataset,
    _write_seed_refs,
)
from openjiuwen.rsi.harness_rsi.config import load_auto_coordinating_harness_config


def test_entrypoint_defaults_to_one_candidate_for_single_harness_optimization(tmp_path: Path) -> None:
    suite = tmp_path / "suite.json"
    suite.write_text('{"validation": []}', encoding="utf-8")
    args = _parse_args(["--suite-path", str(suite)])

    assert args.sibling_candidate_count == 1
    assert args.improver_policy_ref == ""


def test_entrypoint_writes_canonical_dataset_refs_and_config(tmp_path: Path) -> None:
    suite = tmp_path / "suite.json"
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    suite.write_text(
        json.dumps(
            {
                "assets_dir": "assets",
                "validation": [
                    {
                        "id": "claw-T001",
                        "domain": "general",
                        "prompt": "complete the task",
                        "metadata": {"task_type": "office"},
                        "public_files": ["brief.pdf"],
                        "scorer": {"type": "rubric"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    dataset_path = _write_dataset(suite, tmp_path / "dataset" / "cases.json")
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    assert dataset["validation"] == [
        {
            "id": "claw-T001",
            "domain": "general",
            "prompt": "complete the task",
            "metadata": {"task_type": "office"},
            "public_files": ["brief.pdf"],
            "scorer": {"type": "rubric"},
        }
    ]
    assert dataset["assets_dir"] == str(assets_dir.resolve())

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

    suite = tmp_path / "suite.json"
    suite.write_text('{"validation": []}', encoding="utf-8")
    args = _parse_args(["--task-dir", str(task_dir), "--suite-path", str(suite)])
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
    case = json.loads(dataset_path.read_text(encoding="utf-8"))["validation"][0]

    assert case["domain"] == "office"
    assert case["id"] == "gdpval-office-001"
    assert case["metadata"] == {"canary": "gdpval"}


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
    cases = json.loads(dataset_path.read_text(encoding="utf-8"))["validation"]

    assert cases[0]["id"] == "apex-office-001"
    assert _resolve_execution_mode("auto", cases) == "e2b"


def test_deepagent_harness_mounts_standard_prompt_sections(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = ModuleType("evobench.models.client")
    client.ModelConfig = type("ModelConfig", (), {})
    monkeypatch.setitem(sys.modules, "evobench", ModuleType("evobench"))
    monkeypatch.setitem(sys.modules, "evobench.models", ModuleType("evobench.models"))
    monkeypatch.setitem(sys.modules, "evobench.models.client", client)

    module_path = Path("scripts/rsi/evobench_deepagent_harness/harness.py").resolve()
    spec = importlib.util.spec_from_file_location("rsi_test_policy_harness", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)

    section_file = tmp_path / "prompt_sections" / "files" / "verification.md"
    section_file.parent.mkdir(parents=True)
    section_file.write_text("Verify the final deliverable.", encoding="utf-8")
    (tmp_path / "prompt_sections" / "sections.yaml").write_text(
        yaml.safe_dump(
            {
                "sections": [
                    {
                        "name": "verification",
                        "file": "prompt_sections/files/verification.md",
                        "priority": 30,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    assert module._load_prompt_sections(tmp_path) == ["Verify the final deliverable."]

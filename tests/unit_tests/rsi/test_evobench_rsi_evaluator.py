# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Focused tests for the Evo-Bench evaluation adapter contract."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from examples.rsi.evobench import rsi_evaluator


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _fake_root(tmp_path: Path) -> Path:
    root = tmp_path / "Evo-Bench"
    (root / "evobench").mkdir(parents=True)
    (root / "evobench" / "cli.py").write_text("", encoding="utf-8")
    _write_json(root / "benchmark" / "suites" / "evobench_validation.json", {"validation": []})
    return root


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


def test_write_suite_preserves_task_fields_and_relative_assets(tmp_path: Path) -> None:
    root = _fake_root(tmp_path)
    source_suite = tmp_path / "portable" / "train_suite.json"
    assets_dir = tmp_path / "portable" / "assets"
    assets_dir.mkdir(parents=True)
    _write_json(
        source_suite,
        {
            "assets_dir": "assets",
            "validation": [
                {
                    "id": "gdpval-portable-001",
                    "domain": "office",
                    "prompt": "create the requested workbook",
                    "public_files": ["source.xlsx"],
                    "scorer": {"type": "rubric", "rubric": ["check output"]},
                }
            ],
        },
    )

    suite_path, selected = rsi_evaluator._write_suite(
        root,
        cases=[
            {
                "case_id": "gdpval-portable-001",
                "task_id": "gdpval-portable-001",
                "case_path": str(source_suite),
            }
        ],
        output_dir=tmp_path / "run",
        execution_mode="e2b",
    )

    payload = json.loads(suite_path.read_text(encoding="utf-8"))
    assert selected[0]["public_files"] == ["source.xlsx"]
    assert selected[0]["scorer"]["rubric"] == ["check output"]
    assert Path(payload["assets_dir"]) == assets_dir.resolve()


def test_analysis_snapshot_excludes_executable_files(tmp_path: Path) -> None:
    workspace = tmp_path / "official_workspace"
    workspace.mkdir()
    (workspace / "contract.txt").write_text("controlling contract clause", encoding="utf-8")
    (workspace / "unsafe.py").write_text("raise SystemExit", encoding="utf-8")

    snapshot = rsi_evaluator._materialize_analysis_artifacts(
        case_dir=tmp_path / "case",
        official_result={"workspace_path": str(workspace)},
        official_eval_dir=tmp_path / "evaluation",
        task_id="gdpval-office-pass",
    )

    snapshot_root = Path(snapshot["path"])
    assert (snapshot_root / "contract.txt").is_file()
    assert not (snapshot_root / "unsafe.py").exists()

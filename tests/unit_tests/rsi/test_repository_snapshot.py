# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Regression tests for evaluated-repository evidence copying."""

import json
from pathlib import Path

import pytest

from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer import repository_snapshot as snapshot
from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer import analyzer
from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.case_reader import CaseReader


def test_unreadable_entry_preserves_source_patch_and_readable_files(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "code.py").write_text("result = 42\n", encoding="utf-8")
    (source / "link.png").touch()
    patch = tmp_path / "model.patch"
    patch.write_text("diff --git a/code.py b/code.py\n", encoding="utf-8")
    runtime = tmp_path / "diagnosis"
    copy2 = snapshot.shutil.copy2

    def fail_reparse_entry(src, dst, **kwargs):
        if Path(src).name == "link.png":
            raise OSError(22, "Invalid argument", str(src))
        return copy2(src, dst, **kwargs)

    monkeypatch.setattr(snapshot.shutil, "copy2", fail_reparse_entry)
    result = snapshot.prepare_repository_snapshot(workspace=str(source), patch=str(patch), runtime_dir=runtime)

    assert result["repository"] == "partial"
    assert result["patch"] == "copied"
    assert [error["path"] for error in result["errors"]] == ["link.png"]
    assert (runtime / "repository" / "code.py").read_bytes() == (source / "code.py").read_bytes()
    assert (runtime / "source_patch.diff").read_bytes() == patch.read_bytes()
    assert json.loads((runtime / "repository_snapshot.json").read_text(encoding="utf-8")) == result
    assert (source / "link.png").exists()


@pytest.mark.parametrize("workspace", [None, "missing"])
def test_patch_is_copied_even_without_workspace(tmp_path, workspace):
    patch = tmp_path / "model.patch"
    patch.write_text("patch evidence", encoding="utf-8")
    runtime = tmp_path / "diagnosis"
    result = snapshot.prepare_repository_snapshot(
        workspace=str(tmp_path / workspace) if workspace else None,
        patch=str(patch),
        runtime_dir=runtime,
    )
    assert result["repository"] == "unavailable"
    assert result["patch"] == "copied"
    assert (runtime / "source_patch.diff").read_bytes() == patch.read_bytes()


def test_long_paths_and_runtime_exclusions(tmp_path):
    source = tmp_path / "source"
    nested = source / ("a" * 90) / ("b" * 90) / ("c" * 90)
    snapshot._io_path(nested).mkdir(parents=True)
    snapshot._io_path(nested / "code.py").write_text("long path", encoding="utf-8")
    for relative in [".git/config", "messages/trace.json", "src/messages/domain.py"]:
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("evidence", encoding="utf-8")
    runtime = tmp_path / "diagnosis"
    result = snapshot.prepare_repository_snapshot(workspace=str(source), patch=None, runtime_dir=runtime)
    assert result["repository"] == "complete"
    assert not result["errors"]
    assert not (runtime / "repository" / ".git").exists()
    assert not (runtime / "repository" / "messages").exists()
    assert (runtime / "repository" / "src/messages/domain.py").is_file()
    copied = snapshot._io_path(runtime / "repository" / nested.relative_to(source) / "code.py")
    assert copied.read_text(encoding="utf-8") == "long path"


@pytest.mark.parametrize("workspace_exists", [True, False])
def test_archived_outputs_remain_readable_independently_of_workspace(tmp_path, workspace_exists):
    case_dir = tmp_path / "cases" / "case_1"
    artifacts = case_dir / "artifacts"
    artifacts.mkdir(parents=True)
    (artifacts / "solver.py").write_text("answer = 3\n", encoding="utf-8")
    (artifacts / "results.json").write_text('{"answer": 3}', encoding="utf-8")
    workspace = case_dir / "workspace"
    if workspace_exists:
        workspace.mkdir()
        (workspace / "solver.py").write_text("answer = 2\n", encoding="utf-8")
    (case_dir / "result.json").write_text(json.dumps({
        "case_id": "case_1", "workspace_dir": str(workspace),
    }), encoding="utf-8")
    (case_dir / "trace.json").write_text('{"input": "Deliver a program and a report."}', encoding="utf-8")
    (case_dir / "judge").mkdir()
    (case_dir / "judge" / "private_reference.json").write_text('"not agent evidence"', encoding="utf-8")
    case = CaseReader.read_case_inputs(str(case_dir.parent))[0]
    runtime = tmp_path / "diagnosis"

    assert analyzer._prepare_diagnosis_evidence(case=case, runtime_dir=runtime)

    manifest = json.loads((runtime / "repository_snapshot.json").read_text(encoding="utf-8"))
    assert manifest["repository"] == ("complete" if workspace_exists else "unavailable")
    assert manifest["artifacts"] == "complete"
    assert (runtime / "artifacts/solver.py").read_bytes() == (artifacts / "solver.py").read_bytes()
    assert (runtime / "artifacts/results.json").is_file()
    assert not (runtime / "judge").exists()
    assert not (runtime / "result.json").exists()
    if workspace_exists:
        assert (runtime / "repository/solver.py").read_text(encoding="utf-8") == "answer = 2\n"
    else:
        assert not (runtime / "repository").exists()
    assert "artifacts/" in (runtime / "evidence_summary.md").read_text(encoding="utf-8")


def test_partial_artifact_copy_keeps_readable_evidence(tmp_path, monkeypatch):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "ok.py").write_text("pass\n", encoding="utf-8")
    (artifacts / "bad.bin").touch()
    copy2 = snapshot.shutil.copy2

    def fail_one(src, dst, **kwargs):
        if Path(src).name == "bad.bin":
            raise PermissionError("unreadable artifact")
        return copy2(src, dst, **kwargs)

    monkeypatch.setattr(snapshot.shutil, "copy2", fail_one)
    runtime = tmp_path / "diagnosis"
    result = snapshot.prepare_repository_snapshot(
        workspace=None, patch=None, artifacts=str(artifacts), runtime_dir=runtime,
    )
    assert result["repository"] == "unavailable"
    assert result["artifacts"] == "partial"
    assert (runtime / "artifacts/ok.py").is_file()
    assert result["errors"][0]["path"] == "artifacts/bad.bin"

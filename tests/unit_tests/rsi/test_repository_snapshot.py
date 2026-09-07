# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Regression tests for evaluated-repository evidence copying."""

import json
from pathlib import Path

import pytest

from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer import repository_snapshot as snapshot


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

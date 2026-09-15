# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Copy evaluated evidence without discarding readable files on partial failure."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from openjiuwen.rsi.harness_rsi.artifact_io import _io_path

_COPY_IGNORES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "node_modules",
}
_ROOT_RUNTIME_DIRS = {"agents", "context", "memory", "messages", "todo"}


def prepare_repository_snapshot(
    *,
    workspace: str | None,
    patch: str | None,
    runtime_dir: Path,
) -> dict:
    """Preserve a partial snapshot and disclose every failed copy to diagnosis.

    A patch is independent evidence: an unavailable repository must not prevent
    copying it. copytree collects per-entry failures after copying readable files.
    """
    runtime = _io_path(runtime_dir)
    runtime.mkdir(parents=True, exist_ok=True)
    manifest = {"repository": "unavailable", "patch": "unavailable", "errors": []}
    if patch:
        try:
            shutil.copy2(_io_path(Path(patch).expanduser()), runtime / "source_patch.diff")
            manifest["patch"] = "copied"
        except OSError as exc:
            manifest["errors"].append({"path": "source_patch.diff", "error": str(exc)})

    if workspace:
        source = _io_path(Path(workspace).expanduser())
        destination = runtime / "repository"

        def ignore(directory: str, names: list[str]) -> set[str]:
            ignored = _COPY_IGNORES.intersection(names)
            if Path(directory) == source:
                ignored.update(_ROOT_RUNTIME_DIRS.intersection(names))
            return ignored

        try:
            shutil.copytree(source, destination, ignore=ignore, symlinks=False, copy_function=shutil.copy2)
            manifest["repository"] = "complete"
        except shutil.Error as exc:
            manifest["repository"] = "partial"
            for src, _dst, error in exc.args[0]:
                manifest["errors"].append(
                    {
                        "path": Path(src).relative_to(source).as_posix(),
                        "error": str(error),
                    }
                )
        except OSError as exc:
            manifest["errors"].append({"path": "repository/", "error": str(exc)})

    (runtime / "repository_snapshot.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return manifest

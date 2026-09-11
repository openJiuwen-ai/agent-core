# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Resolve the directory shape accepted by program optimization.

The public RSI request deliberately carries one ``artifact_path``.  A program
optimization task may nevertheless be a small directory bundle containing the
seed, its scorecard, and optional prompt templates::

    task/
      task.json
      seed/...
      run/scorecard.json
      run/prompts/*.md

This module only resolves that input.  The provider copies the resolved pieces
into its durable ``run_dir`` before building a search spec, so the control
plane does not need to know or reproduce this layout.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class ProgramBundle:
    """The provider-relevant parts of one program input path."""

    root: Path
    seed_path: Path
    scorecard_path: Path | None
    prompts_path: Path | None
    manifest_path: Path | None
    is_bundle: bool


def resolve_program_bundle(artifact_path: str | Path) -> ProgramBundle:
    """Resolve a file, source directory, or task directory into its inputs.

    A plain file and a plain source directory remain valid for backwards
    compatibility.  A directory that advertises the task-bundle layout via
    ``task.json``, ``seed``, ``run`` or ``scorecard.json`` is resolved as a
    bundle; its seed is never the metadata-bearing bundle root.
    """

    raw_path = str(artifact_path or "").strip()
    if not raw_path:
        raise ValueError("program optimization needs a starting program")

    path = Path(artifact_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"program input does not exist: {path}")
    if path.is_file():
        return ProgramBundle(
            root=path.parent,
            seed_path=path,
            scorecard_path=None,
            prompts_path=None,
            manifest_path=None,
            is_bundle=False,
        )
    if not path.is_dir():
        raise ValueError(f"program input must be a file or directory: {path}")

    manifest_path = path / "task.json"
    manifest: Mapping[str, Any] | None = None
    if manifest_path.is_file():
        manifest = _read_manifest(manifest_path)

    artifact_value = manifest.get("artifact_path") if manifest is not None else None
    if manifest is not None and artifact_value is not None:
        seed_path = _manifest_path(path, artifact_value, "artifact_path")
    elif (path / "seed").exists():
        seed_path = _inside(path, "seed", "seed directory")
    else:
        seed_path = path

    if not seed_path.exists():
        raise FileNotFoundError(f"program seed does not exist: {seed_path}")
    if not seed_path.is_file() and not seed_path.is_dir():
        raise ValueError(f"program seed must be a file or directory: {seed_path}")

    run_value = manifest.get("run_dir") if manifest is not None else None
    if manifest is not None and run_value is not None:
        run_dir = _manifest_path(path, run_value, "run_dir")
        scorecard_candidates = [run_dir / "scorecard.json"]
    else:
        scorecard_candidates = []
    scorecard_candidates.extend([path / "run" / "scorecard.json", path / "scorecard.json"])
    scorecard_path = _first_file_inside(path, scorecard_candidates)

    prompt_candidates: list[Path] = []
    if scorecard_path is not None:
        prompt_candidates.append(scorecard_path.parent / "prompts")
    prompt_candidates.append(path / "prompts")
    prompts_path = _first_dir_inside(path, prompt_candidates)

    is_bundle = bool(
        manifest_path.is_file() or (path / "seed").exists() or (path / "run").is_dir() or scorecard_path is not None
    )
    return ProgramBundle(
        root=path,
        seed_path=seed_path,
        scorecard_path=scorecard_path,
        prompts_path=prompts_path,
        manifest_path=manifest_path if manifest_path.is_file() else None,
        is_bundle=is_bundle,
    )


def _read_manifest(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"task manifest {path} is not valid JSON: {error}") from error
    if not isinstance(value, Mapping):
        raise ValueError(f"task manifest {path} must contain a JSON object")
    return value


def _manifest_path(root: Path, value: object, field: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"task manifest field {field!r} must be a non-empty relative path")
    candidate = Path(value)
    if candidate.is_absolute():
        raise ValueError(f"task manifest field {field!r} must stay inside the task folder")
    return _inside(root, candidate, field)


def _inside(root: Path, value: str | Path, label: str) -> Path:
    candidate = (root / value).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise ValueError(f"{label} must stay inside the task folder: {value}") from error
    return candidate


def _first_file_inside(root: Path, candidates: list[Path]) -> Path | None:
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        # Scorecards are copied into the provider-owned run directory.  Do not
        # follow a task bundle symlink outside its root while doing that.
        try:
            resolved.relative_to(root)
        except ValueError as error:
            raise ValueError(f"scorecard must stay inside the task folder: {candidate}") from error
        if resolved.is_file():
            return resolved
    return None


def _first_dir_inside(root: Path, candidates: list[Path]) -> Path | None:
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        try:
            resolved.relative_to(root)
        except ValueError as error:
            raise ValueError(f"prompt directory must stay inside the task folder: {candidate}") from error
        if resolved.is_dir():
            return resolved
    return None


__all__ = ["ProgramBundle", "resolve_program_bundle"]

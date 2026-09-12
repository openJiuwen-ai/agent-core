# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Filesystem roots for skill_train local datasets and assets."""

from __future__ import annotations

import os
from pathlib import Path


def skill_train_root() -> Path:
    """Return the ``skill_train`` package directory."""
    return Path(__file__).resolve().parent


def skill_train_data_root() -> Path:
    """Return the default dataset root under ``skill_train/data``.

    Override with ``SKILL_TRAIN_DATA_ROOT``:
    - if the path ends with ``data``, use it as-is;
    - otherwise treat it as a parent checkout and append ``/data``.
    """
    explicit = os.environ.get("SKILL_TRAIN_DATA_ROOT", "").strip()
    if explicit:
        path = Path(explicit).expanduser()
        return path if path.name == "data" else path / "data"
    return skill_train_root() / "data"


def resolve_data_path(
    relpath: str,
    *env_names: str,
    repo_root: Path | None = None,
) -> Path:
    """Resolve a dataset path relative to skill_train data (with fallbacks).

    Search order:
    1. Explicit env overrides (``env_names``)
    2. ``skill_train/data/<trimmed>``
    3. ``SKILL_TRAIN_DATA_ROOT`` variants
    4. Legacy repo-root ``data/...`` (when ``repo_root`` is provided)
    """
    text = str(relpath or "").strip().replace("\\", "/")
    trimmed = text[5:] if text.startswith("data/") else text

    candidates: list[Path] = []
    for name in env_names:
        value = os.environ.get(name, "").strip()
        if value:
            candidates.append(Path(value).expanduser())

    data_root = skill_train_data_root()
    if trimmed:
        candidates.append(data_root / trimmed)
    candidates.append(data_root / text)

    explicit_root = os.environ.get("SKILL_TRAIN_DATA_ROOT", "").strip()
    if explicit_root:
        root = Path(explicit_root).expanduser()
        if trimmed:
            candidates.append(root / trimmed)
            if root.name != "data":
                candidates.append(root / "data" / trimmed)
        candidates.append(root / text)

    if repo_root is not None:
        root = Path(repo_root)
        if text:
            candidates.append(root / text)
        if trimmed and trimmed != text:
            candidates.append(root / "data" / trimmed)

    for path in candidates:
        if path.exists():
            return path
    return data_root / trimmed if trimmed else data_root


def resolve_asset_path(path: str, *, repo_root: Path | None = None) -> str:
    """Resolve a relative asset path (e.g. DocVQA image) to an existing file."""
    raw = str(path or "").strip()
    if not raw:
        return raw
    candidate = Path(raw).expanduser()
    if candidate.is_file():
        return str(candidate.resolve())

    normalized = raw.replace("\\", "/")
    trimmed = normalized[5:] if normalized.startswith("data/") else normalized
    found = resolve_data_path(trimmed, repo_root=repo_root)
    if found.is_file():
        return str(found.resolve())
    if found.exists():
        return str(found.resolve())
    return raw

# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Small typed helpers for filesystem-backed JSON/YAML artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml


def read_yaml_mapping(path: str | Path) -> dict[str, Any]:
    """Read a YAML mapping, returning an empty mapping for a missing file."""
    artifact_path = Path(path).expanduser().resolve()
    if not artifact_path.is_file():
        return {}
    with open(artifact_path, "r", encoding="utf-8") as file:
        data = yaml.safe_load(file) or {}
    if isinstance(data, dict):
        return data
    return {}


def write_yaml_mapping(path: str | Path, data: dict[str, Any]) -> str:
    """Write a YAML mapping and return its resolved path."""
    artifact_path = Path(path).expanduser().resolve()
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    with open(artifact_path, "w", encoding="utf-8") as file:
        yaml.safe_dump(data, file, allow_unicode=True, sort_keys=False)
    return str(artifact_path)


def read_json_mapping(path: str | Path) -> dict[str, Any]:
    """Read a JSON mapping, returning an empty mapping for a missing file."""
    artifact_path = Path(path).expanduser().resolve()
    if not artifact_path.is_file():
        return {}
    with open(artifact_path, "r", encoding="utf-8") as file:
        data = json.load(file)
    if isinstance(data, dict):
        return data
    return {}


def write_json_mapping(path: str | Path, data: dict[str, Any]) -> str:
    """Write a JSON mapping and return its resolved path."""
    artifact_path = Path(path).expanduser().resolve()
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    with open(artifact_path, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2, default=str)
    return str(artifact_path)


__all__ = [
    "read_json_mapping",
    "read_yaml_mapping",
    "write_json_mapping",
    "write_yaml_mapping",
]

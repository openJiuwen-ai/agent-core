# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Dataset file contracts, independent of execution and optimization policy."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path, PureWindowsPath
from typing import Any

from openjiuwen.rsi.harness_rsi.artifact_io import _io_path

INPUT_FIELDS = ("input", "inputs", "task_input", "query", "prompt", "question")


def task_input(case: dict[str, Any]) -> Any:
    """Keep legacy explicit input aliases, never expose the entire case."""
    for key in INPUT_FIELDS:
        if key not in case:
            continue
        value = case[key]
        if key == "input" and isinstance(value, dict) and set(value) == {"user_message"}:
            value = value["user_message"]
        if value is None or isinstance(value, str) and not value.strip():
            raise ValueError(f"case {case.get('case_id', '')}: {key} must not be empty")
        return value
    raise ValueError(f"case {case.get('case_id', '')}: input is required")


def validate_case_fields(case: dict[str, Any], *, require_input: bool = True) -> None:
    """Validate common fields while retaining existing benchmark metadata."""
    if require_input and (not isinstance(case.get("case_id"), str) or not case["case_id"].strip()):
        raise ValueError("case_id must be a non-empty string")
    if require_input or any(key in case for key in INPUT_FIELDS):
        value = task_input(case)
        if not isinstance(value, (str, dict)) or not value:
            raise ValueError("input must be a non-empty string or a legacy input object")
    if "assets" in case:
        _string_list(case["assets"], "assets")
    reference = case.get("reference", {})
    if not isinstance(reference, dict):
        raise TypeError("reference must be an object")
    if "rubric" in reference:
        _string_list(reference["rubric"], "reference.rubric")
    if "files" in reference:
        _string_list(reference["files"], "reference.files")


def _string_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{field} must be a list of non-empty strings")
    return value


def resolve_dataset_file(base: Path, value: str) -> Path:
    """Reject absolute paths, traversal, ADS and escaping links on every OS."""
    normalized = value.replace("\\", "/")
    relative = Path(normalized)
    if not normalized or relative.is_absolute() or PureWindowsPath(value).drive:
        raise ValueError(f"dataset file must be a relative path within the dataset: {value}")
    if ":" in normalized or any(part in {"", ".", ".."} for part in normalized.split("/")):
        raise ValueError(f"dataset file must be a relative path within the dataset: {value}")
    root = base.resolve()
    resolved = (root / relative).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"dataset file escapes dataset directory: {value}")
    if not _io_path(resolved).is_file():
        raise ValueError(f"dataset file not found: {value}")
    return resolved


def referenced_files(case: dict[str, Any], base: Path) -> list[tuple[str, str, Path]]:
    reference = case.get("reference", {})
    result = []
    for kind, values in (("assets", case.get("assets", [])), ("reference", reference.get("files", []))):
        for value in _string_list(values, kind):
            result.append((kind, value.replace("\\", "/"), resolve_dataset_file(base, value)))
    return result


def validate_dataset_files(cases: list[dict[str, Any]], base: Path, *, require_input: bool = True) -> None:
    public, private = set(), set()
    for case in cases:
        validate_case_fields(case, require_input=require_input)
        for kind, _, path in referenced_files(case, base):
            (public if kind == "assets" else private).add(path)
    if public & private:
        raise ValueError("a private reference file must not also appear in assets")


def file_fingerprint(case: dict[str, Any]) -> dict[str, str]:
    if not case.get("assets") and not (case.get("reference") or {}).get("files"):
        return {}
    if not case.get("case_path"):
        raise ValueError("case_path is required to resolve dataset files")
    base = Path(case["case_path"]).resolve().parent
    return {f"{kind}/{relative}": _file_sha256(path)
            for kind, relative, path in referenced_files(case, base)}


def _file_sha256(path: Path) -> str:
    with _io_path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def copy_dataset_files(cases: list[dict[str, Any]], source: Path, target: Path) -> dict[str, str]:
    """Snapshot only declared files; do not copy a whole dataset directory."""
    validate_dataset_files(cases, source.parent)
    copied: dict[str, str] = {}
    for case in cases:
        for _, relative, origin in referenced_files(case, source.parent):
            destination = (target.parent / relative).resolve()
            if not destination.is_relative_to(target.parent.resolve()) or destination == target.resolve():
                raise ValueError(f"dataset file collides with snapshot or escapes its directory: {relative}")
            if relative in copied:
                continue
            source_hash = _file_sha256(origin)
            _io_path(destination.parent).mkdir(parents=True, exist_ok=True)
            shutil.copy2(_io_path(origin), _io_path(destination))
            copied[relative] = _file_sha256(destination)
            if copied[relative] != source_hash:
                raise ValueError(f"dataset file changed during snapshot: {relative}")
    return copied


def copy_public_assets(case: dict[str, Any], workspace: Path) -> list[tuple[str, Path]]:
    if not case.get("assets"):
        return []
    if not case.get("case_path"):
        raise ValueError("case_path is required to resolve assets")
    base = Path(case["case_path"]).resolve().parent
    copied = []
    for kind, relative, origin in referenced_files(case, base):
        if kind != "assets":
            continue
        destination = (workspace / relative).resolve()
        if not destination.is_relative_to(workspace.resolve()):
            raise ValueError(f"asset destination escapes workspace: {relative}")
        _io_path(destination.parent).mkdir(parents=True, exist_ok=True)
        shutil.copy2(_io_path(origin), _io_path(destination))
        copied.append((relative, destination))
    return copied

# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Translate explicitly declared private references into existing verifier input."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from openjiuwen.rsi.harness_rsi.artifact_io import _io_path
from openjiuwen.rsi.harness_rsi.data_loader.case_files import referenced_files, resolve_dataset_file


def adapt_reference(case: dict[str, Any], dataset_path: Path) -> dict[str, Any]:
    """Keep backend selection explicit, never infer it from a filename or ID."""
    declarations = []
    private_paths = []
    for kind, _, path in referenced_files(case, dataset_path.parent):
        if kind != "reference":
            continue
        private_paths.append(path)
        if path.suffix.lower() != ".json":
            continue
        try:
            payload = json.loads(_io_path(path).read_text(encoding="utf-8"))
        except ValueError as exc:
            raise ValueError(f"invalid reference JSON: {path.name}") from exc
        if isinstance(payload, dict) and "adapter" in payload:
            declarations.append(payload)
    if not declarations:
        return case
    if len(declarations) != 1:
        raise ValueError("reference.files must declare exactly one verifier adapter")
    declaration = declarations[0]
    if declaration["adapter"] != "swebench_official":
        raise ValueError(f"unsupported reference adapter: {declaration['adapter']}")
    if "swebench" in case:
        raise ValueError("do not combine legacy swebench configuration with a reference adapter")
    config = declaration.get("config")
    if not isinstance(config, dict):
        raise TypeError("swebench_official reference adapter requires a config object")
    config = dict(config)
    instance_id = config.get("instance_id")
    if not isinstance(instance_id, str) or instance_id != case.get("case_id"):
        raise ValueError("reference instance_id must match case_id")
    raw_path = config.get("official_dataset_path")
    if not isinstance(raw_path, str):
        raise TypeError("official_dataset_path must reference a declared private dataset file")
    official_path = resolve_dataset_file(dataset_path.parent, raw_path)
    if official_path not in private_paths:
        raise ValueError("official_dataset_path must be included in reference.files")
    records = json.loads(_io_path(official_path).read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise TypeError("official SWE dataset must be a list")
    selected = [record for record in records if isinstance(record, dict) and record.get("instance_id") == instance_id]
    if len(selected) != 1:
        raise ValueError("official dataset must contain exactly one matching instance_id")
    for key in ("repo", "base_commit", "version", "test_patch"):
        if key in config and config[key] != selected[0].get(key):
            raise ValueError(f"reference config conflicts with official dataset: {key}")
    config["official_dataset_path"] = str(official_path)
    return {**case, "swebench": config}

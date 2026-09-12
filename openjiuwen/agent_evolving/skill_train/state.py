# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Runtime state persistence for ReflACT training."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List


def save_json(path: str | Path, data: Any) -> None:
    """Write *data* as indented JSON to *path*, creating parent dirs as needed."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_json(path: str | Path, default: Any = None) -> Any:
    """Load JSON from *path*, returning *default* when the file is missing."""
    path = Path(path)
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def append_history(out_root: str, record: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Append *record* to ``history.json`` under *out_root* and return the full list."""
    history_path = os.path.join(out_root, "history.json")
    history = load_json(history_path, default=[]) or []
    history.append(record)
    save_json(history_path, history)
    return history


def save_runtime_state(out_root: str, state: Dict[str, Any]) -> None:
    """Persist training runtime state to ``runtime_state.json`` under *out_root*."""
    save_json(os.path.join(out_root, "runtime_state.json"), state)


def load_runtime_state(out_root: str) -> Dict[str, Any]:
    """Load persisted runtime state from *out_root*, or an empty dict if absent."""
    return load_json(os.path.join(out_root, "runtime_state.json"), default={}) or {}

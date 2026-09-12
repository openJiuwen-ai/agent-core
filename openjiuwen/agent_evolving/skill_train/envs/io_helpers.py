# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Filesystem helpers used by skill_train environment rollouts."""

from __future__ import annotations

import csv
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from openjiuwen.agent_evolving.skill_train.utils import safe_fs_id

_SYSTEM_NAME = "target_system_prompt.txt"
_USER_NAME = "target_user_prompt.txt"
_CONVERSATION_NAME = "conversation.json"


def format_skill_section(skill_content: str) -> str:
    """Wrap non-empty skill text as a markdown section; blank input stays empty."""
    body = skill_content.strip()
    if not body:
        return ""
    return f"## Skill\n{body}\n\n"


def _write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def write_prediction_artifacts(
    out_root: str,
    item_id: str,
    *,
    system_prompt: str | None = None,
    user_prompt: str | None = None,
    conversation: list[Any] | None = None,
) -> str:
    """Persist prompts / conversation under ``predictions/<safe_id>/``.

    Returns the created prediction directory as a string path.
    """
    pred_dir = Path(out_root) / "predictions" / safe_fs_id(item_id)
    pred_dir.mkdir(parents=True, exist_ok=True)

    if system_prompt is not None:
        _write_text(pred_dir / _SYSTEM_NAME, system_prompt)
    if user_prompt is not None:
        _write_text(pred_dir / _USER_NAME, user_prompt)
    if conversation is not None:
        with (pred_dir / _CONVERSATION_NAME).open("w", encoding="utf-8") as handle:
            json.dump(conversation, handle, ensure_ascii=False, indent=2)
    return str(pred_dir)


def _first_glob(directory: Path, pattern: str) -> Path | None:
    matches = sorted(directory.glob(pattern))
    return matches[0] if matches else None


def _rows_from_csv(path: Path, normalize_row: Callable[[dict], dict]) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as handle:
        return [normalize_row(row) for row in csv.DictReader(handle)]


def _rows_from_json(path: Path, normalize_row: Callable[[dict], dict]) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, list):
        raise ValueError(f"JSON split must be an array: {path}")
    return [normalize_row(row) for row in payload]


def load_csv_or_json_split(
    split_path: str,
    normalize_row: Callable[[dict], dict],
    *,
    require_question: bool = True,
    env_label: str = "split",
) -> list[dict]:
    """Load the first ``*.csv`` or ``*.json`` array found under ``split_path``."""
    root = Path(split_path)
    csv_path = _first_glob(root, "*.csv")
    if csv_path is not None:
        items = _rows_from_csv(csv_path, normalize_row)
    else:
        json_path = _first_glob(root, "*.json")
        if json_path is None:
            raise FileNotFoundError(f"neither .csv nor .json present under {split_path}")
        items = _rows_from_json(json_path, normalize_row)

    if require_question and items:
        head = items[0]
        if not str(head.get("question") or "").strip():
            keys = sorted(head.keys())
            raise ValueError(f"{env_label} at {split_path} lacks question fields (keys={keys}).")
    return items

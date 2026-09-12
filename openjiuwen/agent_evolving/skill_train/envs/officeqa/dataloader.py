# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""OfficeQA split loader with optional id-split materialization."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from openjiuwen.agent_evolving.skill_train.datasets.base import SplitDataLoader
from openjiuwen.agent_evolving.skill_train.datasets.materialize import (
    ensure_materialized_officeqa,
    is_id_split_dir,
)
from openjiuwen.agent_evolving.skill_train.envs.io_helpers import load_csv_or_json_split


def _coerce_string_list(raw: Any) -> list[str]:
    """Accept list / JSON list / newline or comma separated text."""
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(part).strip() for part in raw if str(part).strip()]

    text = str(raw).strip()
    if not text:
        return []

    try:
        decoded = json.loads(text)
    except json.JSONDecodeError:
        decoded = None
    if isinstance(decoded, list):
        return [str(part).strip() for part in decoded if str(part).strip()]

    if "\n" in text:
        return [line.strip() for line in text.splitlines() if line.strip()]

    looks_like_csv = "," in text and not text.lower().endswith(".txt")
    if looks_like_csv:
        return [chunk.strip() for chunk in text.split(",") if chunk.strip()]
    return [text]


def _pick_first(row: dict[str, Any], keys: Iterable[str], default: str = "") -> str:
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return default


def _row_to_item(row: dict[str, Any]) -> dict:
    """Map a raw CSV/JSON row onto the OfficeQA item schema."""
    item_id = _pick_first(row, ("uid", "id"))
    question = _pick_first(row, ("question",))
    ground_truth = _pick_first(row, ("ground_truth", "answer"))
    task_type = _pick_first(row, ("category", "difficulty"), default="officeqa") or "officeqa"
    return {
        "id": item_id,
        "uid": item_id,
        "question": question,
        "ground_truth": ground_truth,
        "answers": [ground_truth] if ground_truth else [],
        "task_type": task_type,
        "category": task_type,
        "source_files": _coerce_string_list(row.get("source_files")),
        "source_docs": _coerce_string_list(row.get("source_docs")),
        "split": _pick_first(row, ("split",)),
    }


class OfficeQADataLoader(SplitDataLoader):
    """Hydrate ``officeqa_id_split`` when needed, then load CSV/JSON splits."""

    def setup(self, cfg: dict) -> None:
        split_dir = self.split_dir or cfg.get("split_dir", "")
        if split_dir and is_id_split_dir(split_dir):
            materialized = ensure_materialized_officeqa(split_dir)
            self.split_dir = str(materialized)
            cfg = {**cfg, "split_dir": self.split_dir}
        super().setup(cfg)

    def load_split_items(self, split_path: str) -> list[dict]:
        return load_csv_or_json_split(
            split_path,
            _row_to_item,
            env_label="OfficeQA split",
        )

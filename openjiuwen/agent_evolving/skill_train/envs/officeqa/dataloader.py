# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from __future__ import annotations

import csv
import json
from pathlib import Path

from openjiuwen.agent_evolving.skill_train.datasets.base import SplitDataLoader
from openjiuwen.agent_evolving.skill_train.datasets.materialize import (
    ensure_materialized_officeqa,
    is_id_split_dir,
)


def _parse_list_field(value: str | list[str] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    if not text:
        return []
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError:
        loaded = None
    if isinstance(loaded, list):
        return [str(item).strip() for item in loaded if str(item).strip()]
    if "\n" in text:
        return [part.strip() for part in text.splitlines() if part.strip()]
    if "," in text and not text.lower().endswith(".txt"):
        return [part.strip() for part in text.split(",") if part.strip()]
    return [text]


def _normalize_row(row: dict[str, str]) -> dict:
    item_id = str(row.get("uid") or row.get("id") or "").strip()
    question = str(row.get("question") or "").strip()
    ground_truth = str(row.get("ground_truth") or row.get("answer") or "").strip()
    task_type = str(row.get("category") or row.get("difficulty") or "officeqa").strip() or "officeqa"
    source_files = _parse_list_field(row.get("source_files"))
    source_docs = _parse_list_field(row.get("source_docs"))
    split = str(row.get("split") or "").strip()
    return {
        "id": item_id,
        "uid": item_id,
        "question": question,
        "ground_truth": ground_truth,
        "answers": [ground_truth] if ground_truth else [],
        "task_type": task_type,
        "category": task_type,
        "source_files": source_files,
        "source_docs": source_docs,
        "split": split,
    }


class OfficeQADataLoader(SplitDataLoader):
    """OfficeQA dataloader with automatic ``officeqa_id_split`` materialization."""

    def setup(self, cfg: dict) -> None:
        split_dir = self.split_dir or cfg.get("split_dir", "")
        if split_dir and is_id_split_dir(split_dir):
            materialized = ensure_materialized_officeqa(split_dir)
            self.split_dir = str(materialized)
            cfg = {**cfg, "split_dir": self.split_dir}
        super().setup(cfg)

    def load_split_items(self, split_path: str) -> list[dict]:
        path = Path(split_path)
        csv_files = sorted(path.glob("*.csv"))
        if csv_files:
            with csv_files[0].open(encoding="utf-8", newline="") as f:
                reader = csv.DictReader(f)
                items = [_normalize_row(row) for row in reader]
        else:
            json_files = sorted(path.glob("*.json"))
            if not json_files:
                raise FileNotFoundError(f"No .csv or .json file found in {split_path}")
            with json_files[0].open(encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list):
                raise ValueError(f"Expected JSON array in {json_files[0]}")
            items = [_normalize_row(item) for item in data]

        if items and not str(items[0].get("question") or "").strip():
            raise ValueError(
                f"OfficeQA split at {split_path} is missing question fields "
                f"(keys={sorted(items[0].keys())})."
            )
        return items

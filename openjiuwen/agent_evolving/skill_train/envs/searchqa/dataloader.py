# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""SearchQA task dataloader."""
from __future__ import annotations

import json

from openjiuwen.agent_evolving.skill_train.datasets.base import SplitDataLoader
from openjiuwen.agent_evolving.skill_train.datasets.materialize import (
    ensure_materialized_searchqa,
    is_id_split_dir,
)


def _load_items(path: str) -> list[dict]:
    """Load items from JSON or JSONL file."""
    with open(path, encoding="utf-8") as f:
        content = f.read().strip()
    try:
        data = json.loads(content)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("data") or list(data.values())
    except json.JSONDecodeError:
        pass

    items = []
    for line in content.splitlines():
        line = line.strip()
        if line:
            items.append(json.loads(line))
    return items


class SearchQADataLoader(SplitDataLoader):
    """SearchQA dataloader.

    Accepts materialized ``searchqa_split`` or ``searchqa_id_split`` (auto-hydrated).
    """

    def setup(self, cfg: dict) -> None:
        split_dir = self.split_dir or cfg.get("split_dir", "")
        if split_dir and is_id_split_dir(split_dir):
            materialized = ensure_materialized_searchqa(split_dir)
            self.split_dir = str(materialized)
            cfg = {**cfg, "split_dir": self.split_dir}
        super().setup(cfg)

    def load_raw_items(self, data_path: str) -> list[dict]:
        return _load_items(data_path)

    def load_split_items(self, split_path: str) -> list[dict]:
        items = super().load_split_items(split_path)
        if items and "question" not in items[0]:
            raise ValueError(
                f"SearchQA split at {split_path} is missing question fields "
                f"(keys={sorted(items[0].keys())})."
            )
        return items

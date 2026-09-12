# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""SearchQA task dataloader."""

from __future__ import annotations

from openjiuwen.agent_evolving.skill_train.datasets.base import SplitDataLoader
from openjiuwen.agent_evolving.skill_train.datasets.materialize import (
    ensure_materialized_searchqa,
    is_id_split_dir,
)
from openjiuwen.agent_evolving.skill_train.envs.io_helpers import load_csv_or_json_split


def _as_answers(raw: object) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    text = str(raw).strip()
    return [text] if text else []


def _normalize_row(row: dict) -> dict:
    answers = _as_answers(row.get("answers") if "answers" in row else row.get("answer"))
    task_type = str(row.get("task_type") or "searchqa").strip() or "searchqa"
    return {
        "id": str(row.get("id") or "").strip(),
        "question": str(row.get("question") or "").strip(),
        "context": str(row.get("context") or ""),
        "answers": answers,
        "task_type": task_type,
    }


class SearchQADataLoader(SplitDataLoader):
    """SearchQA loader with automatic ``searchqa_id_split`` materialization."""

    def setup(self, cfg: dict) -> None:
        split_dir = self.split_dir or cfg.get("split_dir", "")
        if split_dir and is_id_split_dir(split_dir):
            materialized = ensure_materialized_searchqa(split_dir)
            self.split_dir = str(materialized)
            cfg = {**cfg, "split_dir": self.split_dir}
        super().setup(cfg)

    def load_split_items(self, split_path: str) -> list[dict]:
        return load_csv_or_json_split(
            split_path,
            _normalize_row,
            env_label="SearchQA split",
        )

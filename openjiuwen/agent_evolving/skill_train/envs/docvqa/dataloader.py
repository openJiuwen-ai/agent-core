# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""DocVQA split loader with optional ``docvqa_id_split`` materialization."""

from __future__ import annotations

import ast
from typing import Any

from openjiuwen.agent_evolving.skill_train.datasets.base import SplitDataLoader
from openjiuwen.agent_evolving.skill_train.datasets.materialize import (
    ensure_materialized_docvqa,
    is_id_split_dir,
)
from openjiuwen.agent_evolving.skill_train.envs.io_helpers import load_csv_or_json_split
from openjiuwen.agent_evolving.skill_train.paths import resolve_asset_path

_DOC_PATH_MARKER = "document_path:"
_DEFAULT_TASK = "docvqa"


def _answers_from_cell(cell: Any) -> list[str]:
    """Parse a CSV/JSON answer cell into a non-empty string list."""
    if isinstance(cell, list):
        return [str(x).strip() for x in cell if str(x).strip()]
    blob = str(cell or "").strip()
    if not blob:
        return []
    try:
        decoded = ast.literal_eval(blob)
    except Exception:
        return [blob]
    if isinstance(decoded, list):
        return [str(x).strip() for x in decoded if str(x).strip()]
    cleaned = str(decoded).strip()
    return [cleaned] if cleaned else []


def _split_question_and_path(question: str) -> tuple[str, str]:
    """Detach an optional trailing ``document_path:`` suffix from the question."""
    if _DOC_PATH_MARKER not in question:
        return question.strip(), ""
    head, _, tail = question.partition(_DOC_PATH_MARKER)
    return head.strip(), tail.strip()


def _field(row: dict, *keys: str, default: str = "") -> str:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return default


def _row_to_item(row: dict) -> dict:
    """Map a raw split row onto the DocVQA item schema used by rollout."""
    q_text, embedded_path = _split_question_and_path(str(row.get("question") or ""))
    golds = _answers_from_cell(row.get("answer") or row.get("ground_truth") or "")
    raw_image = _field(row, "image_path") or embedded_path
    image = resolve_asset_path(raw_image) if raw_image else ""
    topic = _field(row, "topic", "category", default=_DEFAULT_TASK) or _DEFAULT_TASK
    qid = _field(row, "questionId", "id")
    return {
        "id": qid,
        "question": q_text,
        "answer": golds[0] if golds else "",
        "answers": golds,
        "task_type": topic,
        "subtask": topic,
        "image_paths": [image] if image else [],
        "image_path": image,
        "questionId": _field(row, "questionId"),
        "docId": _field(row, "docId"),
        "ucsf_document_id": _field(row, "ucsf_document_id"),
        "ucsf_document_page_no": _field(row, "ucsf_document_page_no"),
        "source_split": _field(row, "source_split"),
    }


class DocVQADataLoader(SplitDataLoader):
    """Hydrates id-only splits, then loads CSV/JSON DocVQA items."""

    def setup(self, cfg: dict) -> None:
        split_dir = self.split_dir or cfg.get("split_dir", "")
        if split_dir and is_id_split_dir(split_dir):
            materialized = ensure_materialized_docvqa(split_dir)
            self.split_dir = str(materialized)
            cfg = {**cfg, "split_dir": self.split_dir}
        super().setup(cfg)

    def load_split_items(self, split_path: str) -> list[dict]:
        return load_csv_or_json_split(
            split_path,
            _row_to_item,
            env_label="DocVQA split",
        )

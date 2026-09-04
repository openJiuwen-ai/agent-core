# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from __future__ import annotations

import ast
import csv
import json
from pathlib import Path

from openjiuwen.agent_evolving.skill_train.datasets.base import SplitDataLoader
from openjiuwen.agent_evolving.skill_train.datasets.materialize import (
    ensure_materialized_docvqa,
    is_id_split_dir,
)
from openjiuwen.agent_evolving.skill_train.paths import resolve_asset_path


def _parse_answers(raw: object) -> list[str]:
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    text = str(raw or "").strip()
    if not text:
        return []
    try:
        parsed = ast.literal_eval(text)
    except Exception:
        return [text]
    if isinstance(parsed, list):
        return [str(item).strip() for item in parsed if str(item).strip()]
    return [str(parsed).strip()]


def _extract_document_path(question: str) -> tuple[str, str]:
    marker = "document_path:"
    if marker not in question:
        return question.strip(), ""
    main, tail = question.split(marker, 1)
    return main.strip(), tail.strip()


def _normalize_row(row: dict) -> dict:
    question_text, document_path = _extract_document_path(str(row.get("question") or ""))
    answers = _parse_answers(row.get("answer") or row.get("ground_truth") or "")
    raw_image = str(row.get("image_path") or document_path or "").strip()
    image_path = resolve_asset_path(raw_image) if raw_image else ""
    task_type = str(row.get("topic") or row.get("category") or "docvqa").strip() or "docvqa"
    return {
        "id": str(row.get("questionId") or row.get("id") or "").strip(),
        "question": question_text,
        "answer": answers[0] if answers else "",
        "answers": answers,
        "task_type": task_type,
        "subtask": task_type,
        "image_paths": [image_path] if image_path else [],
        "image_path": image_path,
        "questionId": str(row.get("questionId") or "").strip(),
        "docId": str(row.get("docId") or "").strip(),
        "ucsf_document_id": str(row.get("ucsf_document_id") or "").strip(),
        "ucsf_document_page_no": str(row.get("ucsf_document_page_no") or "").strip(),
        "source_split": str(row.get("source_split") or "").strip(),
    }


class DocVQADataLoader(SplitDataLoader):
    """DocVQA dataloader with automatic ``docvqa_id_split`` materialization."""

    def setup(self, cfg: dict) -> None:
        split_dir = self.split_dir or cfg.get("split_dir", "")
        if split_dir and is_id_split_dir(split_dir):
            materialized = ensure_materialized_docvqa(split_dir)
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
                f"DocVQA split at {split_path} is missing question/answer fields "
                f"(keys={sorted(items[0].keys())})."
            )
        return items

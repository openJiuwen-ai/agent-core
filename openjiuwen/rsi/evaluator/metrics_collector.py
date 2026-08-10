# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Evaluation metrics collection."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class MetricsCollector:
    """Aggregate case results into summary metrics."""

    async def collect(self, case_results_dir: str, output_path: str) -> str:
        """Write ``summary.json`` from case result artifacts."""
        root = Path(case_results_dir).expanduser().resolve()
        result_files = sorted(root.glob("*/result.json"))
        results = [_load_json(path) for path in result_files]
        total = len(results)
        passed = sum(1 for item in results if _case_passed(item))
        failed = total - passed
        scores = [float(item["score"]) for item in results if isinstance(item.get("score"), int | float)]
        evaluation_method = _aggregate_evaluation_method(results)
        summary = {
            "total_cases": total,
            "passed_cases": passed,
            "failed_cases": failed,
            "average_score": (sum(scores) / len(scores)) if scores else None,
            "evaluation_method": evaluation_method,
            "case_results": [str(path) for path in result_files],
        }
        output = Path(output_path).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        with open(output, "w", encoding="utf-8") as file:
            json.dump(summary, file, ensure_ascii=False, indent=2)
        return str(output)


__all__ = [
    "MetricsCollector",
]


def _load_json(path: Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError(f"case result must be a mapping: {path}")
    return data


def _case_passed(result: dict[str, Any]) -> bool:
    evaluation = result.get("evaluation")
    if isinstance(evaluation, dict) and "passed" in evaluation:
        return bool(evaluation.get("passed"))
    return result.get("status") == "passed"


def _aggregate_evaluation_method(results: list[dict[str, Any]]) -> str:
    """Take the first non-empty ``evaluation.method`` across case results."""
    for result in results:
        evaluation = result.get("evaluation")
        if not isinstance(evaluation, dict):
            continue
        method = evaluation.get("method")
        if method:
            return str(method)
    return ""

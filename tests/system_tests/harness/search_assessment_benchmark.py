# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Explicit live-model benchmark over fixed, independently labelled candidates.

Not auto-collected by pytest. A host supplies its configured Model to benchmark().
No search-engine or page-fetch requests are made. Labels never enter the prompt.
"""

from __future__ import annotations

import json
import math
import time
from collections import Counter
from pathlib import Path
from statistics import median
from typing import Any

from openjiuwen.core.foundation.llm import Model
from openjiuwen.harness.tools.web._assessment import assess_candidates
from openjiuwen.harness.tools.web.free_search import _select_search_candidates


def load_cases() -> list[dict[str, Any]]:
    return json.loads(Path(__file__).with_name("fixtures").joinpath("search_assessment_cases.json").read_text(encoding="utf-8"))


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    elapsed = sorted(item["elapsed_ms"] for item in records)
    labels = [label for item in records for label in item["labels"]]
    assessed = [label for label in labels if label["actual"] != "unavailable"]
    relevant = [label for label in labels if label["expected"] == "relevant"]
    selected = [label for label in labels if label["selected"]]
    return {
        "batches": len(records),
        "candidates": len(labels),
        "status_counts": dict(Counter(item["status"] for item in records)),
        "classification_accuracy": sum(label["actual"] == label["expected"] for label in labels) / max(1, len(labels)),
        "assessed_accuracy": sum(label["actual"] == label["expected"] for label in assessed) / max(1, len(assessed)),
        "relevant_retention": sum(label["selected"] for label in relevant) / max(1, len(relevant)),
        "rejected_selected": sum(label["expected"] == "rejected" for label in selected),
        "selected_count": len(selected),
        "false_rejection": sum(label["actual"] == "rejected" and label["expected"] != "rejected" for label in labels),
        "median_ms": median(elapsed) if elapsed else 0,
        "p95_ms": elapsed[math.ceil(len(elapsed) * 0.95) - 1] if elapsed else 0,
    }


async def benchmark(
    model: Model,
    *,
    rounds: int = 2,
    timeout_seconds: float = 15,
    cases: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    records = []
    for round_index in range(rounds):
        for case in cases if cases is not None else load_cases():
            raw = [{key: value for key, value in row.items() if key != "expected"} for row in case["rows"]]
            started = time.perf_counter()
            assessment = await assess_candidates(
                model, case["query"], list(enumerate(raw)),
                original_task=case["original_task"], timeout_seconds=timeout_seconds,
            )
            _, decisions = _select_search_candidates(case["query"], raw, max_results=8, assessment=assessment)
            record = {
                "id": case["id"], "round": round_index + 1,
                "status": assessment.status, "reason": assessment.reason, "error_type": assessment.error_type,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
                "labels": [
                    {"id": item["index"], "expected": case["rows"][item["index"]]["expected"],
                     "actual": item["relevance"] if assessment.status == "ok" else "unavailable",
                     "selected": item["selected"], "reasons": item["reasons"]}
                    for item in decisions
                ],
            }
            records.append(record)
    return {"summary": summarize(records), "records": records}

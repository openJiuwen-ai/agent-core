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
    """Score successful model judgments separately from selection and availability."""
    elapsed = sorted(item["elapsed_ms"] for item in records)
    labels = [label for item in records for label in item["labels"]]
    assessed = [label for label in labels if label["decision_source"] == "model"]
    relevant = [label for label in labels if label["expected"] == "relevant"]
    selected = [label for label in labels if label["selected"]]
    accuracy = sum(label["actual"] == label["expected"] for label in assessed) / max(1, len(assessed))
    return {
        "batches": len(records),
        "candidates": len(labels),
        "assessed_candidates": len(assessed),
        "structural_rejections": sum(label["decision_source"] == "structure" for label in labels),
        "unavailable_candidates": sum(label["decision_source"] == "assessment_unavailable" for label in labels),
        "status_counts": dict(Counter(item["status"] for item in records)),
        "classification_accuracy": accuracy,
        "assessed_accuracy": accuracy,
        "relevant_retention": sum(label["selected"] for label in relevant) / max(1, len(relevant)),
        "rejected_selected": sum(label["expected"] == "rejected" for label in selected),
        "selected_count": len(selected),
        "false_rejection": sum(label["actual"] == "rejected" and label["expected"] != "rejected" for label in assessed),
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
            allowed_domains = case.get("allowed_domains")
            # Match production filtering; free-form model reasons are not provenance.
            _, structural_decisions = _select_search_candidates(case["query"], raw, allowed_domains=allowed_domains)
            candidates = [(item["index"], item["row"]) for item in structural_decisions if item["relevance"] != "rejected"]
            eligible_ids = {index for index, _ in candidates}
            assessment = await assess_candidates(
                model, case["query"], candidates,
                original_task=case["original_task"], timeout_seconds=timeout_seconds,
            )
            _, decisions = _select_search_candidates(
                case["query"], raw, max_results=8, allowed_domains=allowed_domains, assessment=assessment,
            )
            judgments = {item.id: item.relevance for item in assessment.results} if assessment.status == "ok" else {}
            labels = []
            for item in decisions:
                index = item["index"]
                if index not in eligible_ids:
                    actual, source = "structural_rejection", "structure"
                elif index in judgments:
                    actual, source = judgments[index], "model"
                else:
                    actual, source = "unavailable", "assessment_unavailable"
                labels.append({
                    "id": index, "expected": case["rows"][index]["expected"], "actual": actual,
                    "decision_source": source, "selected": item["selected"], "reasons": item["reasons"],
                })
            record = {
                "id": case["id"], "round": round_index + 1,
                "status": assessment.status, "reason": assessment.reason, "error_type": assessment.error_type,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
                "labels": labels,
            }
            records.append(record)
    return {"summary": summarize(records), "records": records}

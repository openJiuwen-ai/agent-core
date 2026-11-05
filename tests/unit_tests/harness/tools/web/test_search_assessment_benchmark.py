# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Separate model quality from deterministic filtering and unavailable batches."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.system_tests.harness.search_assessment_benchmark import benchmark, summarize


def test_summary_scores_only_model_judgments():
    records = [{"status": "ok", "elapsed_ms": 10, "labels": [
        {"expected": "relevant", "actual": "relevant", "selected": True, "decision_source": "model"},
        {"expected": "uncertain", "actual": "rejected", "selected": False, "decision_source": "model"},
        {"expected": "relevant", "actual": "structural_rejection", "selected": False, "decision_source": "structure"},
        {"expected": "rejected", "actual": "structural_rejection", "selected": False, "decision_source": "structure"},
        {"expected": "relevant", "actual": "unavailable", "selected": True, "decision_source": "assessment_unavailable"},
    ]}]
    summary = summarize(records)
    assert summary["classification_accuracy"] == summary["assessed_accuracy"] == 0.5
    assert summary["false_rejection"] == 1
    assert summary["assessed_candidates"] == 2
    assert summary["structural_rejections"] == 2
    assert summary["unavailable_candidates"] == 1
    assert summary["candidates"] == 5
    assert summary["relevant_retention"] == pytest.approx(2 / 3)


@pytest.mark.asyncio
@pytest.mark.parametrize("unavailable", [False, True])
async def test_benchmark_filters_before_model_and_keeps_judgment_provenance(unavailable):
    rows = [
        {"title": "Bad target", "url": "file:///document", "expected": "relevant"},
        {"title": "Navigation", "url": "https://bing.com/search?q=x", "expected": "relevant"},
        {"title": "Document", "url": "https://source.invalid/a", "expected": "relevant"},
        {"title": "Duplicate", "url": "https://source.invalid/a", "expected": "relevant"},
        {"title": "Outside domain", "url": "https://other.invalid/a", "expected": "relevant"},
    ]
    case = {"id": "provenance", "query": "query", "original_task": "task", "rows": rows,
            "allowed_domains": ["source.invalid"]}
    content = "invalid JSON" if unavailable else json.dumps({"results": [
        {"id": 2, "relevance": "rejected", "reason": "invalid_target"},
    ]})
    model = SimpleNamespace(invoke=AsyncMock(return_value=SimpleNamespace(content=content)))
    report = await benchmark(model, rounds=1, cases=[case])
    model.invoke.assert_awaited_once()
    payload = json.loads(model.invoke.call_args.kwargs["messages"][1]["content"])
    assert [row["id"] for row in payload["candidates"]] == [2]
    assert all("expected" not in row for row in payload["candidates"])
    labels = report["records"][0]["labels"]
    for index, reason in [(0, "invalid_target"), (1, "search_navigation"), (3, "duplicate_url"), (4, "outside_allowed_domains")]:
        assert labels[index]["decision_source"] == "structure"
        assert labels[index]["actual"] == "structural_rejection"
        assert labels[index]["reasons"] == [reason]
    assert labels[2]["decision_source"] == ("assessment_unavailable" if unavailable else "model")
    assert report["summary"]["false_rejection"] == (0 if unavailable else 1)
    assert report["summary"]["assessed_candidates"] == (0 if unavailable else 1)
    assert report["summary"]["structural_rejections"] == 4


@pytest.mark.asyncio
async def test_no_structurally_valid_candidates_do_not_call_model_or_claim_accuracy():
    case = {"id": "empty", "query": "query", "original_task": "task", "rows": [
        {"title": "Bad target", "url": "https://[broken/doc", "expected": "relevant"},
    ]}
    model = SimpleNamespace(invoke=AsyncMock())
    report = await benchmark(model, rounds=1, cases=[case])
    model.invoke.assert_not_awaited()
    assert report["records"][0]["status"] == "skipped"
    assert report["summary"]["classification_accuracy"] == 0
    assert report["summary"]["false_rejection"] == 0
    assert report["summary"]["assessed_candidates"] == 0
    assert report["summary"]["structural_rejections"] == 1
    assert summarize([])["classification_accuracy"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(("label", "count", "selected", "limit_reason"), [
    ("uncertain", 3, 2, "uncertain_limit"),
    ("relevant", 10, 8, "result_limit"),
])
async def test_selection_limits_do_not_remove_model_judgments_from_metrics(label, count, selected, limit_reason):
    case = {"id": "limits", "query": "query", "original_task": "task", "rows": [
        {"title": f"Document {index}", "url": f"https://source.invalid/{index}", "expected": label}
        for index in range(count)
    ]}
    content = json.dumps({"results": [
        {"id": index, "relevance": label, "reason": "Judgment"} for index in range(count)
    ]})
    model = SimpleNamespace(invoke=AsyncMock(return_value=SimpleNamespace(content=content)))
    report = await benchmark(model, rounds=1, cases=[case])
    assert report["summary"]["classification_accuracy"] == 1
    assert report["summary"]["assessed_candidates"] == count
    assert report["summary"]["selected_count"] == selected
    assert report["summary"]["false_rejection"] == 0
    assert report["records"][0]["labels"][-1]["reasons"][-1] == limit_reason

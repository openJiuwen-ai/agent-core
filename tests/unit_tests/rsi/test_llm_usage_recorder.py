# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for LLM usage ledger collection."""

from __future__ import annotations

import json

from openjiuwen.core.foundation.llm.schema.message import UsageMetadata
from openjiuwen.rsi.usage_recorder import (
    llm_usage_ledger,
    llm_usage_scope,
    record_llm_usage,
    summarize_llm_usage_file,
)


def test_records_llm_usage_and_summarizes_by_stage_and_model(tmp_path) -> None:
    usage_path = tmp_path / "llm_usage.jsonl"

    with llm_usage_ledger(usage_path, run_id="run_001"):
        with llm_usage_scope(stage="dataset_generation", operation="task_analysis"):
            record_llm_usage(
                UsageMetadata(
                    model_name="glm-5.2",
                    input_tokens=100,
                    output_tokens=20,
                    total_tokens=120,
                    cache_tokens=70,
                    input_cost=0.1,
                    output_cost=0.04,
                    total_cost=0.14,
                ),
                metadata={"agent_id": "dataset_generator"},
            )
        with llm_usage_scope(stage="batch_001.team_skill_stage", operation="execute"):
            record_llm_usage(
                UsageMetadata(
                    model_name="deepseek-v4-flash",
                    input_tokens=30,
                    output_tokens=10,
                    total_tokens=40,
                    cache_tokens=15,
                ),
            )

    lines = [json.loads(line) for line in usage_path.read_text(encoding="utf-8").splitlines()]
    assert [line["stage"] for line in lines] == [
        "dataset_generation",
        "batch_001.team_skill_stage",
    ]
    assert lines[0]["run_id"] == "run_001"
    assert lines[0]["operation"] == "task_analysis"
    assert lines[0]["metadata"] == {"agent_id": "dataset_generator"}

    summary = summarize_llm_usage_file(usage_path)

    assert summary["total"]["calls"] == 2
    assert summary["total"]["input_tokens"] == 130
    assert summary["total"]["output_tokens"] == 30
    assert summary["total"]["total_tokens"] == 160
    assert summary["total"]["cache_tokens"] == 85
    assert summary["total"]["cache_hit_rate"] == 85 / 130
    assert summary["total"]["total_cost"] == 0.14
    assert summary["by_stage"]["dataset_generation"]["input_tokens"] == 100
    assert summary["by_model"]["deepseek-v4-flash"]["total_tokens"] == 40

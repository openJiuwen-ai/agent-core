# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for orchestrator run context persistence."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import yaml

from openjiuwen.rsi.orchestrator.context import OrchestratorContextStore
from openjiuwen.rsi.schema import (
    CallAuditRecord,
    CurrentArtifactRefs,
    DatasetArtifact,
    RunStrategyMetadata,
)


def test_context_create_save_load_preserves_strategy_and_call_audit(tmp_path: Path) -> None:
    """Run context persists strategy metadata and call audit records as first-class fields."""
    context_path = tmp_path / "workspace" / "team" / "orchestrator_context.yaml"
    store = OrchestratorContextStore(str(context_path))
    strategy = RunStrategyMetadata(
        evaluation_strategy="hybrid",
        coordination_strategy="team_first_single_pass",
        promotion_policy="epoch_full_evaluation",
        strategy_name="hybrid_team_first_single_pass",
    )

    context = store.create("评估一个团队", strategy=strategy)
    context = replace(
        context,
        calls=[
            CallAuditRecord(
                call_id="call_001",
                module="TeamEvaluator",
                method="evaluate_batch",
                inputs={"eval_ref_path": "eval_ref.yaml"},
                output={"status": "ok"},
                status="succeeded",
            )
        ],
    )
    store.save(context)

    raw_context = yaml.safe_load(context_path.read_text(encoding="utf-8"))
    loaded = store.load()

    assert raw_context["strategy"] == {
        "evaluation_strategy": "hybrid",
        "coordination_strategy": "team_first_single_pass",
        "promotion_policy": "epoch_full_evaluation",
        "strategy_name": "hybrid_team_first_single_pass",
        "enabled_at": strategy.enabled_at.isoformat(),
        "full_evaluation_enabled": True,
    }
    assert loaded.strategy == strategy
    assert loaded.calls == context.calls


def test_context_load_preserves_dataset_case_count(tmp_path: Path) -> None:
    context_path = tmp_path / "workspace" / "team" / "orchestrator_context.yaml"
    store = OrchestratorContextStore(str(context_path))
    context = store.create("optimize team")
    context = replace(
        context,
        current=CurrentArtifactRefs(
            dataset=DatasetArtifact(
                dataset_id="dataset_001",
                dataset_dir="datasets/dataset_001",
                dataset_files=["datasets/dataset_001/cases.json"],
                cases=5,
            )
        ),
    )

    store.save(context)
    loaded = store.load()

    assert loaded.current.dataset is not None
    assert loaded.current.dataset.cases == 5

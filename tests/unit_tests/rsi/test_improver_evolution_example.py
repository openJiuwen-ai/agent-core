# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

from examples.rsi import evolve_improver_policy as example


def test_propose_writes_unpromoted_versioned_candidates(
    tmp_path: Path,
    monkeypatch,
) -> None:
    ledger_path = tmp_path / "ledger.yaml"
    ledger_path.write_text("schema_version: 1\n", encoding="utf-8")
    monkeypatch.setattr(
        example,
        "analyze_candidate_feedback_ledgers",
        lambda *args, **kwargs: {
            "training_ledger_digest": "sha256:training",
            "evidence_refs": ["cohort:one", "cohort:two"],
            "stable_patterns": [
                {
                    "pattern_id": "duplicate_candidates",
                    "pattern_type": "duplicate_candidates",
                    "surface": None,
                    "support_cohorts": 2,
                    "opportunity_cohorts": 2,
                    "rate": 1.0,
                    "evidence_cohort_ids": ["one", "two"],
                    "recommended_policy_change": {
                        "field": "generation_directives.require_unique_candidate_fingerprint",
                        "operation": "set",
                        "value": True,
                        "rationale": "Repeated duplicate candidates consumed the search budget.",
                    },
                }
            ],
        },
    )
    output_dir = tmp_path / "out"

    result = example._propose(
        argparse.Namespace(
            ledger=[str(ledger_path)],
            parent_policy="",
            output_dir=str(output_dir),
            min_support_cohorts=2,
            high_value_gain_threshold=0.0,
        )
    )

    assert result == 0
    manifest = yaml.safe_load((output_dir / "candidate_manifest.yaml").read_text(encoding="utf-8"))
    assert manifest["status"] == "awaiting_meta_validation"
    assert manifest["candidate_count"] == 1
    assert manifest["promotion"] == {
        "status": "inconclusive",
        "reason": "paired_unseen_live_meta_validation_required",
    }
    policy_path = Path(manifest["candidates"][0]["policy_path"])
    assert policy_path.is_file()


def test_validate_writes_live_promotion_decision(tmp_path: Path) -> None:
    baseline_path = tmp_path / "baseline.json"
    candidate_path = tmp_path / "candidate.yaml"
    baseline = [_checkpoint("cp_1", "I0", "sha256:i0", selection_regret=0.2)]
    candidate = [_checkpoint("cp_1", "I1", "sha256:i1", selection_regret=0.1)]
    baseline_path.write_text(json.dumps({"results": baseline}), encoding="utf-8")
    candidate_path.write_text(yaml.safe_dump({"checkpoints": candidate}), encoding="utf-8")
    output_path = tmp_path / "validation.yaml"

    result = example._validate(
        argparse.Namespace(
            baseline_results=str(baseline_path),
            candidate_results=str(candidate_path),
            mode="live_generation",
            thresholds="",
            meta_train_checkpoint_id=[],
            output=str(output_path),
        )
    )

    assert result == 0
    report = yaml.safe_load(output_path.read_text(encoding="utf-8"))
    assert report["validation"]["status"] == "accepted"
    assert report["promotion"]["status"] == "inconclusive"
    assert "insufficient_unseen_checkpoints" in report["promotion"]["reason_codes"]


def _checkpoint(
    checkpoint_id: str,
    version_id: str,
    policy_digest: str,
    *,
    selection_regret: float,
) -> dict:
    return {
        "checkpoint_id": checkpoint_id,
        "split": "meta_test",
        "improver_version_id": version_id,
        "improver_policy_digest": policy_digest,
        "base_harness_id": "H0",
        "failure_evidence_id": "E0",
        "base_model_id": "M0",
        "k": 3,
        "top_m": 1,
        "token_budget": 100,
        "tool_budget": 10,
        "runtime_budget": 60,
        "verifier_id": "V0",
        "protocol_id": "P0",
        "metrics": {
            "best_of_k_gain": 0.4,
            "top_m_gain": 0.2,
            "selection_regret": selection_regret,
            "final_harness_gain_per_budget": 0.01,
            "regression_failure_rate": 0.0,
            "infrastructure_failure_rate": 0.0,
        },
    }

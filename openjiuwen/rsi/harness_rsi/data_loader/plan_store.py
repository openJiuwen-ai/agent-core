# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Persist dataset profile and batch plan artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from openjiuwen.rsi.harness_rsi.data_loader.batch_planner import batch_plan_item


class BatchPlanStore:
    """Write DataLoader-owned profile and batch plan artifacts."""

    @staticmethod
    def write_dataset_profile(root: Path, profile: dict[str, Any]) -> str:
        """Write ``dataset_profile.yaml`` and return its absolute path."""
        profile_path = root / "dataset_profile.yaml"
        with open(profile_path, "w", encoding="utf-8") as file:
            yaml.safe_dump(profile, file, allow_unicode=True, sort_keys=False)
        return str(profile_path)

    @staticmethod
    def write_batch_plan(
        *,
        root: Path,
        epoch: int,
        batch_size: int,
        balance_keys: list[str],
        profile: dict[str, Any],
        batches: list[list[dict[str, Any]]],
    ) -> str:
        """Write ``batch_plan.yaml`` and return its absolute path."""
        plan_path = root / "batch_plan.yaml"
        payload = {
            "plan_id": f"batch_plan_epoch_{epoch:03d}",
            "dataset_dir": str(root),
            "strategy": "curriculum_balanced",
            "epoch": epoch,
            "seed": f"{root.name}:epoch_{epoch:03d}",
            "batch_size": batch_size,
            "balance_keys": list(balance_keys),
            "profile_summary": profile["summary"],
            "batches": [batch_plan_item(batch, batch_index) for batch_index, batch in enumerate(batches, start=1)],
            "warnings": profile["warnings"],
            "metadata": profile["metadata"],
        }
        with open(plan_path, "w", encoding="utf-8") as file:
            yaml.safe_dump(payload, file, allow_unicode=True, sort_keys=False)
        return str(plan_path)


__all__ = [
    "BatchPlanStore",
]

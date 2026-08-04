# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Persistence boundary for ``orchestrator_context.yaml``."""

from __future__ import annotations

from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from openjiuwen.rsi.schema import (
    BestArtifactRefs,
    CallAuditRecord,
    CurrentArtifactRefs,
    DatasetArtifact,
    EvaluationHistoryItem,
    MemberOptimizationHistoryItem,
    OrchestratorHistory,
    OrchestratorPhase,
    OrchestratorRunContext,
    RunStrategyMetadata,
    TeamSkillOptimizationHistoryItem,
)
from openjiuwen.rsi.text_encoding import repair_text_mojibake


class OrchestratorContextStore:
    """Read/write access to the run context source of truth."""

    def __init__(self, context_path: str) -> None:
        self.context_path = context_path

    def load(self) -> OrchestratorRunContext:
        """Deserialize ``orchestrator_context.yaml`` into a context object."""
        path = Path(self.context_path)
        with open(path, "r", encoding="utf-8") as file:
            data = yaml.safe_load(file) or {}
        if not isinstance(data, dict):
            raise ValueError(f"orchestrator context must be a mapping: {path}")
        return _context_from_dict(data)

    def save(self, context: OrchestratorRunContext) -> None:
        """Serialize the context object to ``orchestrator_context.yaml``."""
        path = Path(self.context_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        context = replace(context, updated_at=datetime.now(UTC).astimezone())
        with open(path, "w", encoding="utf-8") as file:
            yaml.safe_dump(
                _context_to_dict(context),
                file,
                allow_unicode=True,
                sort_keys=False,
            )

    def create(
        self,
        task: str,
        *,
        strategy: RunStrategyMetadata | None = None,
    ) -> OrchestratorRunContext:
        """Create the initial context for a new optimization run."""
        now = datetime.now(UTC).astimezone()
        context_path = str(Path(self.context_path).expanduser().resolve())
        return OrchestratorRunContext(
            task_id=f"task_{now.strftime('%Y%m%d%H%M%S')}",
            task=repair_text_mojibake(task),
            context_path=context_path,
            checkpoint_dir=str(Path(context_path).parent / "checkpoints"),
            created_at=now,
            updated_at=now,
            strategy=strategy or RunStrategyMetadata(enabled_at=now),
        )


def _context_to_dict(context: OrchestratorRunContext) -> dict[str, Any]:
    """Convert context dataclasses into a YAML-safe dict."""
    data = asdict(context)
    data["phase"] = context.phase.value
    for key in ("created_at", "updated_at", "last_checkpoint_at"):
        value = data.get(key)
        if isinstance(value, datetime):
            data[key] = value.isoformat()
    strategy = data.get("strategy")
    if isinstance(strategy, dict) and isinstance(strategy.get("enabled_at"), datetime):
        strategy["enabled_at"] = strategy["enabled_at"].isoformat()
    current = data.get("current")
    if isinstance(current, dict):
        dataset = current.get("dataset")
        if isinstance(dataset, dict) and dataset.get("cases") is None:
            dataset.pop("cases", None)
    return data


def _context_from_dict(data: dict[str, Any]) -> OrchestratorRunContext:
    """Convert a YAML dict into ``OrchestratorRunContext``."""
    current_data = _mapping(data.get("current"))
    dataset_data = current_data.get("dataset")
    current = CurrentArtifactRefs(
        dataset=(
            DatasetArtifact(
                dataset_id=str(dataset_data.get("dataset_id", "")),
                dataset_dir=str(dataset_data.get("dataset_dir", "")),
                dataset_files=[str(item) for item in dataset_data.get("dataset_files", [])],
                cases=_optional_int(dataset_data.get("cases")),
            )
            if isinstance(dataset_data, dict)
            else None
        ),
        team_skill_ref_path=str(current_data.get("team_skill_ref_path", "")),
        harness_refs_path=str(current_data.get("harness_refs_path", "")),
        harness_refs={str(key): str(value) for key, value in _mapping(current_data.get("harness_refs")).items()},
        eval_ref_path=current_data.get("eval_ref_path"),
    )
    best_data = _mapping(data.get("best"))
    history_data = _mapping(data.get("history"))
    strategy_data = _mapping(data.get("strategy"))
    return OrchestratorRunContext(
        task_id=str(data.get("task_id", "")),
        task=repair_text_mojibake(str(data.get("task", ""))),
        context_path=str(data.get("context_path", "")),
        checkpoint_dir=str(data.get("checkpoint_dir", "")),
        phase=OrchestratorPhase(str(data.get("phase", OrchestratorPhase.INITIALIZING.value))),
        epoch=int(data.get("epoch", 0)),
        created_at=_parse_datetime(data.get("created_at")),
        updated_at=_parse_datetime(data.get("updated_at")),
        current=current,
        best=BestArtifactRefs(
            team_skill_ref_path=best_data.get("team_skill_ref_path"),
            harness_refs_path=best_data.get("harness_refs_path"),
            harness_refs={str(key): str(value) for key, value in _mapping(best_data.get("harness_refs")).items()},
            eval_ref_path=best_data.get("eval_ref_path"),
            score=_optional_float(best_data.get("score")),
        ),
        history=OrchestratorHistory(
            evaluations=[
                EvaluationHistoryItem(
                    eval_ref_path=str(item.get("eval_ref_path", "")),
                    phase=str(item.get("phase", "")),
                    score=_optional_float(item.get("score")),
                )
                for item in _mapping_list(history_data.get("evaluations"))
            ],
            team_skill_optimizations=[
                TeamSkillOptimizationHistoryItem(
                    before_team_skill_ref_path=str(item.get("before_team_skill_ref_path", "")),
                    after_team_skill_ref_path=str(item.get("after_team_skill_ref_path", "")),
                    eval_ref_path=str(item.get("eval_ref_path", "")),
                )
                for item in _mapping_list(history_data.get("team_skill_optimizations"))
            ],
            member_optimizations=[
                MemberOptimizationHistoryItem(
                    before_harness_refs_path=str(item.get("before_harness_refs_path", "")),
                    after_harness_refs_path=str(item.get("after_harness_refs_path", "")),
                    eval_ref_path=str(item.get("eval_ref_path", "")),
                    role=str(item.get("role", "")),
                    before_role_harness_ref_path=str(item.get("before_role_harness_ref_path", "")),
                    after_role_harness_ref_path=str(item.get("after_role_harness_ref_path", "")),
                )
                for item in _mapping_list(history_data.get("member_optimizations"))
            ],
        ),
        strategy=RunStrategyMetadata(
            evaluation_strategy=str(strategy_data.get("evaluation_strategy", "hybrid")),
            coordination_strategy=str(strategy_data.get("coordination_strategy", "team_first_single_pass")),
            promotion_policy=str(strategy_data.get("promotion_policy", "epoch_full_evaluation")),
            full_evaluation_enabled=_bool_value(
                strategy_data.get("full_evaluation_enabled"),
                default=True,
            ),
            strategy_name=str(strategy_data.get("strategy_name", "hybrid_team_first_single_pass")),
            enabled_at=_parse_datetime(strategy_data.get("enabled_at")) or datetime.now(UTC).astimezone(),
        ),
        calls=[
            CallAuditRecord(
                call_id=str(item.get("call_id", "")),
                module=str(item.get("module", "")),
                method=str(item.get("method", "")),
                inputs=_mapping(item.get("inputs")),
                output=_mapping(item.get("output")),
                status=str(item.get("status", "pending")),
                error=str(item.get("error", "")),
            )
            for item in _mapping_list(data.get("calls"))
        ],
        last_checkpoint_at=_parse_datetime(data.get("last_checkpoint_at")),
        metadata=_mapping(data.get("metadata")),
    )


def _mapping(value: Any) -> dict[str, Any]:
    """Return ``value`` as a mapping or an empty mapping."""
    if isinstance(value, dict):
        return value
    return {}


def _mapping_list(value: Any) -> list[dict[str, Any]]:
    """Return mapping items from ``value`` or an empty list."""
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _parse_datetime(value: Any) -> datetime | None:
    """Parse an ISO datetime string if present."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        return datetime.fromisoformat(value)
    return None


def _optional_float(value: Any) -> float | None:
    """Parse an optional score from persisted YAML."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float | str):
        return float(value)
    return None


def _optional_int(value: Any) -> int | None:
    """Parse an optional count from persisted YAML."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.strip():
        return int(value)
    return None


def _bool_value(value: Any, *, default: bool) -> bool:
    """Parse bool-like values from persisted YAML."""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y", "on"}:
            return True
        if normalized in {"false", "0", "no", "n", "off"}:
            return False
    return default


__all__ = [
    "OrchestratorContextStore",
]

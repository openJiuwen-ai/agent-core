# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Context packet produced when the coding agent submits selected spans.

This is not an agent handoff. ``submit_code_context`` records locations so
eval can score them and the same coding agent can keep editing.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from openjiuwen.harness.schema.code_graph import CodeGraphResult

SCHEMA_VERSION = "1.0"


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def new_loc_id() -> str:
    return _new_id("loc")


@dataclass
class LocalizationArtifact:
    """Selected spans recorded by submit_code_context."""

    artifact_id: str
    repo_snapshot: str
    task: str
    status: str
    locations: list[dict[str, Any]] = field(default_factory=list)
    relations: list[dict[str, Any]] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    query_history_digest: dict[str, Any] = field(default_factory=dict)
    budget_used: dict[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "artifact_id": self.artifact_id,
            "repo_snapshot": self.repo_snapshot,
            "task": self.task,
            "status": self.status,
            "locations": list(self.locations),
            "relations": list(self.relations),
            "assumptions": list(self.assumptions),
            "open_questions": list(self.open_questions),
            "query_history_digest": dict(self.query_history_digest),
            "budget_used": dict(self.budget_used),
        }

    @classmethod
    def from_mapping(cls, data: dict[str, Any] | None) -> "LocalizationArtifact":
        payload = dict(data or {})
        return cls(
            artifact_id=str(payload.get("artifact_id") or _new_id("loc")),
            repo_snapshot=str(payload.get("repo_snapshot") or ""),
            task=str(payload.get("task") or ""),
            status=str(payload.get("status") or "ERROR"),
            locations=list(payload.get("locations") or []),
            relations=list(payload.get("relations") or []),
            assumptions=[str(item) for item in (payload.get("assumptions") or [])],
            open_questions=[str(item) for item in (payload.get("open_questions") or [])],
            query_history_digest=dict(payload.get("query_history_digest") or {}),
            budget_used=dict(payload.get("budget_used") or {}),
            schema_version=str(payload.get("schema_version") or SCHEMA_VERSION),
        )


def localization_from_result(
    result: CodeGraphResult,
    *,
    task: str,
    artifact_id: str | None = None,
) -> LocalizationArtifact:
    """Build a context packet from the current graph run result."""
    stats = dict(result.stats or {})
    return LocalizationArtifact(
        artifact_id=artifact_id or _new_id("loc"),
        repo_snapshot=str(stats.get("index_snapshot") or ""),
        task=task,
        status=result.status,
        locations=[item.to_dict() for item in result.locations],
        relations=[item.to_dict() for item in result.relations],
        assumptions=[],
        open_questions=list(result.open_questions),
        query_history_digest={"warnings": list(result.warnings)},
        budget_used={
            "tool_calls": stats.get("tool_calls"),
            "candidate_count": stats.get("candidate_count"),
            "selected_count": stats.get("selected_count"),
        },
    )

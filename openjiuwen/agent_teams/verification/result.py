# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Verification result models for the Team Verification Layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class VerificationStatus(str, Enum):
    """Quality gate verdict."""

    PASS = "pass"
    FAIL = "fail"
    NEEDS_REWORK = "needs_rework"
    SKIPPED = "skipped"


class QualityDimension(str, Enum):
    """Quality dimensions assessed during verification."""

    CORRECTNESS = "correctness"
    COMPLETENESS = "completeness"
    CONSISTENCY = "consistency"
    CLARITY = "clarity"
    SECURITY = "security"
    PERFORMANCE = "performance"


@dataclass
class DimensionScore:
    """Score for a single quality dimension."""

    dimension: QualityDimension
    score: int  # 0-100
    reasoning: str = ""
    findings: list[str] = field(default_factory=list)


@dataclass
class VerificationInput:
    """Input parameters for triggering a verification review."""

    task_id: str
    task_title: str
    task_content: str
    assignee: str
    output: str
    team_context: str = ""


@dataclass
class VerificationResult:
    """Structured result of a teammate output verification."""

    task_id: str
    task_title: str
    assignee: str
    status: VerificationStatus
    overall_score: int  # 0-100
    dimensions: list[DimensionScore] = field(default_factory=list)
    summary: str = ""
    rework_instructions: str = ""
    verified_at: str = ""
    reviewer_model: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for storage and event emission."""
        return {
            "task_id": self.task_id,
            "task_title": self.task_title,
            "assignee": self.assignee,
            "status": self.status.value,
            "overall_score": self.overall_score,
            "dimensions": [
                {
                    "dimension": d.dimension.value,
                    "score": d.score,
                    "reasoning": d.reasoning,
                    "findings": d.findings,
                }
                for d in self.dimensions
            ],
            "summary": self.summary,
            "rework_instructions": self.rework_instructions,
            "verified_at": self.verified_at,
            "reviewer_model": self.reviewer_model,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VerificationResult:
        """Deserialize from dict."""
        dims = []
        for d in data.get("dimensions", []):
            dims.append(
                DimensionScore(
                    dimension=QualityDimension(d.get("dimension", "correctness")),
                    score=d.get("score", 0),
                    reasoning=d.get("reasoning", ""),
                    findings=d.get("findings", []),
                )
            )
        return cls(
            task_id=data.get("task_id", ""),
            task_title=data.get("task_title", ""),
            assignee=data.get("assignee", ""),
            status=VerificationStatus(data.get("status", "skipped")),
            overall_score=data.get("overall_score", 0),
            dimensions=dims,
            summary=data.get("summary", ""),
            rework_instructions=data.get("rework_instructions", ""),
            verified_at=data.get("verified_at", ""),
            reviewer_model=data.get("reviewer_model", ""),
            metadata=data.get("metadata", {}),
        )

    def is_passing(self, threshold: int = 70) -> bool:
        """Check if the result meets the passing threshold."""
        return self.status == VerificationStatus.PASS and self.overall_score >= threshold

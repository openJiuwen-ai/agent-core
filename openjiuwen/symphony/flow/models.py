"""Symphony flow 数据模型：经验配方、能力包与评审结论。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

RECIPE_SCHEMA_VERSION = "symphony.experience_recipe.v1"
PACKAGE_SCHEMA_VERSION = "symphony.capability_package.v1"
REVIEW_SCHEMA_VERSION = "symphony.package_review.v1"

RECIPE_STATUS_DRAFT = "draft"
RECIPE_STATUS_ACTIVE = "active"
RECIPE_STATUS_DEPRECATED = "deprecated"
RECIPE_STATUSES = {
    RECIPE_STATUS_DRAFT,
    RECIPE_STATUS_ACTIVE,
    RECIPE_STATUS_DEPRECATED,
}

RECIPE_GRADE_CANDIDATE = "candidate"
RECIPE_GRADE_VERIFIED = "verified"
RECIPE_GRADES = {RECIPE_GRADE_CANDIDATE, RECIPE_GRADE_VERIFIED}

OUTCOME_SUCCESS = "success"
OUTCOME_FAILED = "failed"
OUTCOME_PARTIAL = "partial"
OUTCOMES = {OUTCOME_SUCCESS, OUTCOME_FAILED, OUTCOME_PARTIAL}

TARGET_KIND_SKILL = "skill"
TARGET_KIND_PLUGIN = "plugin"
SUPPORTED_TARGET_KINDS = {TARGET_KIND_SKILL, TARGET_KIND_PLUGIN}

VERDICT_APPROVED = "approved"
VERDICT_REJECTED = "rejected"
VERDICT_NEEDS_HUMAN_REVIEW = "needs_human_review"
VERDICTS = {VERDICT_APPROVED, VERDICT_REJECTED, VERDICT_NEEDS_HUMAN_REVIEW}

SKILL_PACK_TYPE = "skill_pack"
CAN_FEED = "can_feed"

CHECK_PASS = "pass"
CHECK_FAIL = "fail"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_short(value: str, *, length: int = 12) -> str:
    return sha256_hex(value)[:length]


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_hash(value: Any) -> str:
    """返回稳定的 sha256 内容指纹；字符串按原文，其余按规范化 JSON。"""

    if isinstance(value, str):
        return "sha256:" + sha256_hex(value)
    return "sha256:" + sha256_hex(canonical_json(value))


@dataclass
class RecipeEvidence:
    """一条不可变的执行证据：query + 能力级执行图。"""

    trace_id: str
    query: str
    outcome: str
    graph: dict[str, Any]
    ingested_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "query": self.query,
            "outcome": self.outcome,
            "graph": self.graph,
            "ingested_at": self.ingested_at,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RecipeEvidence":
        return cls(
            trace_id=str(payload.get("trace_id") or ""),
            query=str(payload.get("query") or ""),
            outcome=str(payload.get("outcome") or ""),
            graph=payload.get("graph") if isinstance(payload.get("graph"), dict) else {},
            ingested_at=str(payload.get("ingested_at") or ""),
        )


@dataclass
class ExperienceRecipe:
    """可复用编排方案：任务描述 + skill pack（JGF）+ 执行过程文本。"""

    recipe_id: str
    version: int
    status: str
    grade: str
    applicability: dict[str, Any]
    combination_structure: dict[str, Any]
    execution_narrative: str
    quality: dict[str, Any]
    provenance: dict[str, Any]
    schema_version: str = RECIPE_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "recipe_id": self.recipe_id,
            "version": self.version,
            "status": self.status,
            "grade": self.grade,
            "applicability": self.applicability,
            "combination_structure": self.combination_structure,
            "execution_narrative": self.execution_narrative,
            "quality": self.quality,
            "provenance": self.provenance,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ExperienceRecipe":
        return cls(
            recipe_id=str(payload.get("recipe_id") or ""),
            version=int(payload.get("version") or 1),
            status=str(payload.get("status") or RECIPE_STATUS_DRAFT),
            grade=str(payload.get("grade") or RECIPE_GRADE_CANDIDATE),
            applicability=payload.get("applicability") if isinstance(payload.get("applicability"), dict) else {},
            combination_structure=payload.get("combination_structure")
            if isinstance(payload.get("combination_structure"), dict)
            else {},
            execution_narrative=str(payload.get("execution_narrative") or ""),
            quality=payload.get("quality") if isinstance(payload.get("quality"), dict) else {},
            provenance=payload.get("provenance") if isinstance(payload.get("provenance"), dict) else {},
            schema_version=str(payload.get("schema_version") or RECIPE_SCHEMA_VERSION),
        )

    @property
    def capability_ids(self) -> list[str]:
        nodes = self.combination_structure.get("nodes")
        if not isinstance(nodes, dict):
            return []
        return sorted(nodes.keys())

    def content_identity(self) -> str:
        """用于变更检测：内容（非计数）发生变化时才产生新版本。"""

        return content_hash(
            {
                "status": self.status,
                "grade": self.grade,
                "applicability": self.applicability,
                "combination_structure": self.combination_structure,
                "execution_narrative": self.execution_narrative,
            }
        )


@dataclass
class ReviewCheck:
    """一项静态检查的结果。"""

    check: str
    result: str
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"check": self.check, "result": self.result, "reasons": self.reasons}


@dataclass
class ReviewResult:
    """PackageReviewGate 的三态评审结论与检查明细。"""

    review_id: str
    package_id: str
    target_kind: str
    verdict: str
    checks: list[ReviewCheck] = field(default_factory=list)
    materials_hash: str = ""
    reviewed_at: str = ""
    schema_version: str = REVIEW_SCHEMA_VERSION

    @property
    def failed_checks(self) -> list[ReviewCheck]:
        return [check for check in self.checks if check.result == CHECK_FAIL]

    def failure_reasons(self) -> list[str]:
        reasons: list[str] = []
        for check in self.failed_checks:
            detail = "; ".join(check.reasons) if check.reasons else "no detail"
            reasons.append(f"{check.check}: {detail}")
        return reasons

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "review_id": self.review_id,
            "package_id": self.package_id,
            "target_kind": self.target_kind,
            "verdict": self.verdict,
            "checks": [check.to_dict() for check in self.checks],
            "materials_hash": self.materials_hash,
            "reviewed_at": self.reviewed_at,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ReviewResult":
        checks = [
            ReviewCheck(
                check=str(item.get("check") or ""),
                result=str(item.get("result") or CHECK_FAIL),
                reasons=[str(reason) for reason in (item.get("reasons") or []) if reason],
            )
            for item in (payload.get("checks") or [])
            if isinstance(item, dict)
        ]
        return cls(
            review_id=str(payload.get("review_id") or ""),
            package_id=str(payload.get("package_id") or ""),
            target_kind=str(payload.get("target_kind") or ""),
            verdict=str(payload.get("verdict") or VERDICT_REJECTED),
            checks=checks,
            materials_hash=str(payload.get("materials_hash") or ""),
            reviewed_at=str(payload.get("reviewed_at") or ""),
        )


@dataclass
class InstallPreparation:
    """一次安装准备的结果：approved 时携带产物与凭证，否则只有原因。"""

    recipe_id: str
    target_kind: str
    verdict: str
    package: dict[str, Any] | None = None
    artifact_dir: str | None = None
    review: ReviewResult | None = None
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "recipe_id": self.recipe_id,
            "target_kind": self.target_kind,
            "verdict": self.verdict,
            "package": self.package,
            "artifact_dir": self.artifact_dir,
            "review": self.review.to_dict() if self.review else None,
            "reasons": self.reasons,
        }

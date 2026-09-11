"""PackageReviewGate：确定性静态评审（结构/依赖/安全/完整性）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openjiuwen.symphony.flow.codegen import (
    validate_generated_script,
    validate_meta_name,
)
from openjiuwen.symphony.flow.models import (
    CHECK_FAIL,
    CHECK_PASS,
    TARGET_KIND_SKILL,
    VERDICT_APPROVED,
    VERDICT_NEEDS_HUMAN_REVIEW,
    VERDICT_REJECTED,
    ReviewCheck,
    ReviewResult,
    content_hash,
    sha256_short,
    utc_now_iso,
)
from openjiuwen.symphony.flow.packager import CapabilityPackager

_FORBIDDEN_SECRET_PATTERNS = ("api_key", "apikey", "secret", "password", "token")
_FORBIDDEN_CODE_TOKENS = ("import os", "import sys", "subprocess", "eval(", "exec(")
_ARTIFACT_ESCAPE_PATTERN = ".."


class PackageReviewGate:
    """静态评审：不调用 LLM，全部为确定性规则检查。"""

    def review(
        self,
        package: dict[str, Any],
        *,
        artifact_dir: str | Path | None = None,
    ) -> ReviewResult:
        checks: list[ReviewCheck] = []
        checks.append(self._check_structure(package))
        checks.append(self._check_dependencies(package))
        checks.append(self._check_code_safety(package))
        checks.append(self._check_secrets(package))
        checks.append(self._check_artifact_paths(artifact_dir))
        checks.append(self._check_integrity(package))
        checks.append(self._check_meta_naming(package))
        checks.append(self._check_materials(package))

        failed = [check for check in checks if check.result == CHECK_FAIL]
        verdict = (
            VERDICT_APPROVED
            if not failed
            else (
                VERDICT_NEEDS_HUMAN_REVIEW
                if len(failed) == 1 and failed[0].check == "meta_naming"
                else VERDICT_REJECTED
            )
        )
        return ReviewResult(
            review_id=f"review_{sha256_short(content_hash(package), length=12)}",
            package_id=str(package.get("package_id") or ""),
            target_kind=str(package.get("target_kind") or TARGET_KIND_SKILL),
            verdict=verdict,
            checks=checks,
            materials_hash=str(package.get("integrity") or ""),
            reviewed_at=utc_now_iso(),
        )

    # ---------------- checks ----------------

    @staticmethod
    def _check_structure(package: dict[str, Any]) -> ReviewCheck:
        reasons: list[str] = []
        for key in ("package_id", "recipe_id", "target_kind", "materials", "integrity"):
            if not package.get(key):
                reasons.append(f"missing field: {key}")
        materials = package.get("materials")
        if not isinstance(materials, dict):
            reasons.append("materials is not an object")
        else:
            recipe = materials.get("recipe")
            if not isinstance(recipe, dict) or not recipe.get("combination_structure"):
                reasons.append("materials.recipe.combination_structure missing")
            if not str(materials.get("swarmflow_script") or "").strip():
                reasons.append("materials.swarmflow_script empty")
        return ReviewCheck(
            check="structure",
            result=CHECK_FAIL if reasons else CHECK_PASS,
            reasons=reasons,
        )

    @staticmethod
    def _check_dependencies(package: dict[str, Any]) -> ReviewCheck:
        reasons: list[str] = []
        materials = package.get("materials") or {}
        nodes = ((materials.get("recipe") or {}).get("combination_structure") or {}).get("nodes")
        if not isinstance(nodes, dict) or not nodes:
            reasons.append("skill pack has no nodes")
        else:
            for member_id, node in nodes.items():
                metadata = (node or {}).get("metadata") or {}
                if not str(metadata.get("version") or "").strip():
                    reasons.append(f"capability {member_id} missing version")
        return ReviewCheck(
            check="dependencies",
            result=CHECK_FAIL if reasons else CHECK_PASS,
            reasons=reasons,
        )

    @staticmethod
    def _check_code_safety(package: dict[str, Any]) -> ReviewCheck:
        reasons: list[str] = []
        script = str((package.get("materials") or {}).get("swarmflow_script") or "")
        if script:
            reasons.extend(validate_generated_script(script))
            lowered = script.lower()
            for token in _FORBIDDEN_CODE_TOKENS:
                if token in lowered:
                    reasons.append(f"forbidden token in script: {token}")
        return ReviewCheck(
            check="code_safety",
            result=CHECK_FAIL if reasons else CHECK_PASS,
            reasons=reasons,
        )

    @staticmethod
    def _check_secrets(package: dict[str, Any]) -> ReviewCheck:
        reasons: list[str] = []
        serialized = str(package)
        lowered = serialized.lower()
        for pattern in _FORBIDDEN_SECRET_PATTERNS:
            if pattern in lowered:
                reasons.append(f"possible secret field: {pattern}")
        return ReviewCheck(
            check="secrets",
            result=CHECK_FAIL if reasons else CHECK_PASS,
            reasons=reasons,
        )

    @staticmethod
    def _check_artifact_paths(artifact_dir: str | Path | None) -> ReviewCheck:
        reasons: list[str] = []
        if artifact_dir is not None:
            text = str(artifact_dir)
            if _ARTIFACT_ESCAPE_PATTERN in text:
                reasons.append("artifact path escapes with '..'")
        return ReviewCheck(
            check="artifact_paths",
            result=CHECK_FAIL if reasons else CHECK_PASS,
            reasons=reasons,
        )

    @staticmethod
    def _check_integrity(package: dict[str, Any]) -> ReviewCheck:
        reasons: list[str] = []
        if not CapabilityPackager.verify_package_integrity(package):
            reasons.append("integrity hash mismatch")
        return ReviewCheck(
            check="integrity",
            result=CHECK_FAIL if reasons else CHECK_PASS,
            reasons=reasons,
        )

    @staticmethod
    def _check_meta_naming(package: dict[str, Any]) -> ReviewCheck:
        reasons: list[str] = []
        meta_name = str((package.get("materials") or {}).get("meta_name") or "")
        if not validate_meta_name(meta_name):
            reasons.append(f"meta_name invalid: {meta_name!r}")
        return ReviewCheck(
            check="meta_naming",
            result=CHECK_FAIL if reasons else CHECK_PASS,
            reasons=reasons,
        )

    @staticmethod
    def _check_materials(package: dict[str, Any]) -> ReviewCheck:
        reasons: list[str] = []
        materials = package.get("materials") or {}
        recipe = materials.get("recipe") or {}
        applicability = recipe.get("applicability") or {}
        if not str(applicability.get("task_description") or "").strip():
            reasons.append("task_description empty")
        if not str(recipe.get("execution_narrative") or "").strip():
            reasons.append("execution_narrative empty")
        return ReviewCheck(
            check="materials",
            result=CHECK_FAIL if reasons else CHECK_PASS,
            reasons=reasons,
        )

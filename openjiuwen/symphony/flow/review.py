"""PackageReviewGate：确定性静态评审（结构/依赖/安全/完整性）。"""

from __future__ import annotations

import json
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Protocol, runtime_checkable

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
from openjiuwen.symphony.flow.privacy import model_response_text, redact_private_json
from openjiuwen.symphony.interfaces import SymphonyLLM

_FORBIDDEN_CODE_TOKENS = ("import os", "import sys", "subprocess", "eval(", "exec(")
_ARTIFACT_ESCAPE_PATTERN = ".."
_FORBIDDEN_PERMISSIONS = frozenset({"credential_access", "filesystem_write", "process", "shell"})
_ALLOWED_LICENSES = frozenset({"Apache-2.0", "BSD-3-Clause", "MIT", "Proprietary"})
_REVIEW_SYSTEM_PROMPT = (
    "Review the supplied immutable capability package using only its contents. "
    "Return JSON with verdict set to approved, rejected, or needs_human_review."
)


@runtime_checkable
class PackageReviewAgent(Protocol):
    """Narrow isolated boundary: inspect immutable package materials only."""

    async def review(self, package: Mapping[str, Any]) -> str:
        """Return approved, rejected, or needs_human_review without executing content."""

        raise NotImplementedError


class LLMPackageReviewAgent:
    """Restricted model reviewer with no tool, filesystem, or sysop surface."""

    def __init__(self, llm: SymphonyLLM) -> None:
        if not callable(getattr(llm, "invoke", None)):
            raise TypeError("llm must provide invoke")
        self._llm = llm

    async def review(self, package: Mapping[str, Any]) -> str:
        """Review only a canonical redacted copy of the supplied package."""

        safe_package = redact_private_json(package)
        canonical = json.dumps(
            {"package": safe_package},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        try:
            response = await self._llm.invoke(
                [
                    {"role": "system", "content": _REVIEW_SYSTEM_PROMPT},
                    {"role": "user", "content": canonical},
                ],
                temperature=0.0,
            )
            raw = model_response_text(response)
            payload = json.loads(raw)
        except Exception:
            return VERDICT_NEEDS_HUMAN_REVIEW
        verdict = payload.get("verdict") if isinstance(payload, dict) else None
        if verdict in {VERDICT_APPROVED, VERDICT_REJECTED, VERDICT_NEEDS_HUMAN_REVIEW}:
            return verdict
        return VERDICT_NEEDS_HUMAN_REVIEW


class PackageReviewGate:
    """Run deterministic checks before an optional isolated read-only reviewer."""

    def __init__(self, review_agent: PackageReviewAgent | None = None) -> None:
        if review_agent is not None and not callable(getattr(review_agent, "review", None)):
            raise TypeError("review_agent must provide review")
        self._review_agent = review_agent

    async def review(
        self,
        package: dict[str, Any],
        *,
        artifact_dir: str | Path | None = None,
    ) -> ReviewResult:
        result = self.review_static(package, artifact_dir=artifact_dir)
        if result.verdict != VERDICT_APPROVED:
            return result
        if self._review_agent is None:
            result.verdict = VERDICT_NEEDS_HUMAN_REVIEW
            result.checks.append(
                ReviewCheck(
                    check="review_agent",
                    result=CHECK_FAIL,
                    reasons=["review agent is not configured"],
                )
            )
            _refresh_review_id(result)
            return result
        try:
            verdict = await self._review_agent.review(_freeze(package))
        except Exception:
            verdict = VERDICT_NEEDS_HUMAN_REVIEW
        if verdict not in {VERDICT_APPROVED, VERDICT_REJECTED, VERDICT_NEEDS_HUMAN_REVIEW}:
            verdict = VERDICT_NEEDS_HUMAN_REVIEW
        result.verdict = verdict
        result.checks.append(
            ReviewCheck(
                check="review_agent",
                result=CHECK_PASS if verdict == VERDICT_APPROVED else CHECK_FAIL,
                reasons=[] if verdict == VERDICT_APPROVED else [f"review agent verdict: {verdict}"],
            )
        )
        _refresh_review_id(result)
        return result

    def review_static(
        self,
        package: dict[str, Any],
        *,
        artifact_dir: str | Path | None = None,
    ) -> ReviewResult:
        """Run deterministic checks without invoking the review agent."""

        checks: list[ReviewCheck] = []
        checks.append(self._check_structure(package))
        checks.append(self._check_dependencies(package))
        checks.append(self._check_code_safety(package))
        checks.append(self._check_secrets(package))
        checks.append(self._check_artifact_paths(artifact_dir))
        checks.append(self._check_integrity(package))
        checks.append(self._check_meta_naming(package))
        checks.append(self._check_materials(package))
        checks.append(self._check_permissions(package))
        checks.append(self._check_license(package))

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
        result = ReviewResult(
            review_id="review_000000000000",
            package_id=str(package.get("package_id") or ""),
            target_kind=str(package.get("target_kind") or TARGET_KIND_SKILL),
            verdict=verdict,
            checks=checks,
            materials_hash=str(package.get("integrity") or ""),
            reviewed_at=utc_now_iso(),
        )
        _refresh_review_id(result)
        return result

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
        if redact_private_json(package) != package:
            reasons.append("package contains unredacted credential material")
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

    @staticmethod
    def _check_permissions(package: dict[str, Any]) -> ReviewCheck:
        permissions = (package.get("materials") or {}).get("permissions")
        reasons: list[str] = []
        if not isinstance(permissions, list) or not all(isinstance(item, str) for item in permissions):
            reasons.append("materials.permissions must be a string list")
        else:
            forbidden = sorted(set(permissions) & _FORBIDDEN_PERMISSIONS)
            if forbidden:
                reasons.append(f"forbidden permissions: {', '.join(forbidden)}")
        return ReviewCheck(
            check="permissions",
            result=CHECK_FAIL if reasons else CHECK_PASS,
            reasons=reasons,
        )

    @staticmethod
    def _check_license(package: dict[str, Any]) -> ReviewCheck:
        license_name = (package.get("materials") or {}).get("license")
        reasons: list[str] = []
        if not isinstance(license_name, str) or not license_name.strip():
            reasons.append("materials.license is required")
        elif license_name not in _ALLOWED_LICENSES:
            reasons.append(f"unsupported license: {license_name}")
        return ReviewCheck(
            check="license",
            result=CHECK_FAIL if reasons else CHECK_PASS,
            reasons=reasons,
        )


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _refresh_review_id(result: ReviewResult) -> None:
    identity = {
        "package_id": result.package_id,
        "target_kind": result.target_kind,
        "verdict": result.verdict,
        "checks": [check.to_dict() for check in result.checks],
        "materials_hash": result.materials_hash,
    }
    result.review_id = f"review_{sha256_short(content_hash(identity), length=12)}"

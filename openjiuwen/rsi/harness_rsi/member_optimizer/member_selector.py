# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Deterministic member target selection from attribution reports.

Per feat_009 rule.md Section 3.5 and design.md Section 4.7.
This component does NOT call any LLM. It purely scores and selects
from the pre-computed role and mechanism attribution reports.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from openjiuwen.rsi.harness_rsi.member_optimizer.schema import (
    MechanismAttributionReport,
    MechanismType,
    MemberOptimizationTarget,
    MemberSelectionReport,
    RoleAttributionReport,
    UnselectedAttribution,
)

# ---------------------------------------------------------------------------
# Severity scoring
# ---------------------------------------------------------------------------

_SEVERITY_WEIGHTS: dict[str, int] = {
    "critical": 4,
    "high": 3,
    "medium": 2,
    "low": 1,
}
_INSUFFICIENT_ROLE_EVIDENCE = MechanismType.INSUFFICIENT_ROLE_EVIDENCE.value
_UNSUPPORTED_OPTIMIZATION_SURFACES = frozenset({"rail"})


def _severity_weight(severity: str) -> int:
    """Return the weight for a severity string. Unknown severity defaults to 1."""
    return _SEVERITY_WEIGHTS.get(severity.lower(), 1)


def _attribution_severity(attribution: object) -> str:
    """Read issue severity carried in attribution evidence or metadata."""
    evidence = getattr(attribution, "evidence", [])
    if isinstance(evidence, list):
        for item in evidence:
            if isinstance(item, dict):
                severity = str(item.get("severity", "") or "").strip()
                if severity:
                    return severity
    metadata = getattr(attribution, "metadata", None)
    if isinstance(metadata, dict):
        return str(metadata.get("severity", "") or "").strip()
    return ""


def _mechanisms_for_issue(
    mechanism_attribution_report: MechanismAttributionReport,
    *,
    role: str,
    issue_id: str,
) -> list[object]:
    """Return mechanism attributions for one role issue."""

    return [
        item
        for item in mechanism_attribution_report.role_mechanisms.get(role, [])
        if getattr(item, "issue_id", "") == issue_id
    ]


def _has_actionable_mechanism(mechanisms: list[object]) -> bool:
    """Whether mechanism attribution can justify harness modification."""

    if not mechanisms:
        return True
    for item in mechanisms:
        mechanism_type = str(getattr(item, "mechanism_type", "") or "").strip().lower()
        failure_signature = str(getattr(item, "failure_signature", "") or "").strip().lower()
        if mechanism_type != _INSUFFICIENT_ROLE_EVIDENCE and failure_signature != _INSUFFICIENT_ROLE_EVIDENCE:
            return True
    return False


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


@dataclass
class RoleScore:
    """Scoring breakdown for one candidate role."""

    role: str
    member_name: str
    harness_ref_path: str
    score: float
    max_severity: int
    avg_confidence: float
    issue_count: int
    attributed_issue_ids: list[str]
    mechanism_types: list[str]
    optimization_surfaces: list[str]
    evidence_refs: list[dict[str, str]] = field(default_factory=list)
    issue_confidences: dict[str, float] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# MemberSelector
# ---------------------------------------------------------------------------


class MemberSelector:
    """Deterministically select roles for optimization from attribution reports."""

    @staticmethod
    def select(
        role_attribution_report: RoleAttributionReport,
        mechanism_attribution_report: MechanismAttributionReport,
        *,
        min_attribution_confidence: float = 0.5,
        max_roles_per_run: int = 2,
        severity_map: dict[str, int] | None = None,
    ) -> MemberSelectionReport:
        """Select roles for optimization based on attribution reports.

        Scoring: severity_score * 10 + confidence_score * 5 + issue_score
        - severity_score: sum of severity weights for eligible issues
        - confidence_score: average confidence of eligible issues
        - issue_score: min(eligible_issue_count, 5) / 5

        Sort: score desc, max_severity desc, role asc (stable)
        Limit: max_roles_per_run

        Returns:
            MemberSelectionReport with selected targets and unselected attributions.
        """
        severity_weights = severity_map if severity_map is not None else _SEVERITY_WEIGHTS

        role_attrs = role_attribution_report.assigned_role_issues

        grouped: dict[str, list] = {}
        for ri in role_attrs:
            grouped.setdefault(ri.role, []).append(ri)

        candidates: list[RoleScore] = []
        unselected_attrs: list[UnselectedAttribution] = []
        filtered_insufficient_count = 0
        deferred_contract_issue_ids: list[str] = []
        for role, attrs in grouped.items():
            eligible = []
            for attr in attrs:
                if attr.confidence < min_attribution_confidence:
                    continue
                mechanisms = _mechanisms_for_issue(
                    mechanism_attribution_report,
                    role=attr.role,
                    issue_id=attr.issue_id,
                )
                if not _has_actionable_mechanism(mechanisms):
                    filtered_insufficient_count += 1
                    deferred_contract_issue_ids.append(attr.issue_id)
                    unselected_attrs.append(
                        UnselectedAttribution(
                            issue_id=attr.issue_id,
                            role=attr.role,
                            reason=(
                                "Mechanism attribution is insufficient_role_evidence; "
                                "not actionable as a member harness change"
                            ),
                            confidence=attr.confidence,
                        )
                    )
                    continue
                eligible.append(attr)
            if not eligible:
                continue

            severity_scores = [severity_weights.get(_attribution_severity(a).lower(), 1) for a in eligible]
            severity_score = sum(severity_scores)
            confidence_score = sum(a.confidence for a in eligible) / len(eligible)
            issue_score = min(len(eligible), 5) / 5.0
            score = severity_score * 10 + confidence_score * 5 + issue_score

            max_sev = max(severity_weights.get(_attribution_severity(a).lower(), 1) for a in eligible)

            mechanism_types: list[str] = []
            optimization_surfaces: list[str] = []
            for ri in eligible:
                mech_list = mechanism_attribution_report.role_mechanisms.get(ri.role, [])
                for m in mech_list:
                    if m.issue_id == ri.issue_id and m.mechanism_type:
                        mechanism_types.append(m.mechanism_type)
                    surface = str(getattr(m, "optimization_surface", "") or "").strip()
                    if m.issue_id == ri.issue_id and surface and surface not in _UNSUPPORTED_OPTIMIZATION_SURFACES:
                        optimization_surfaces.append(surface)

            harness_ref_path = ""
            member_name = ""
            for r in role_attribution_report.candidate_roles:
                if r.role == role:
                    harness_ref_path = r.harness_ref_path
                    member_name = r.member_name
                    break

            candidates.append(
                RoleScore(
                    role=role,
                    member_name=member_name,
                    harness_ref_path=harness_ref_path,
                    score=score,
                    max_severity=max_sev,
                    avg_confidence=confidence_score,
                    issue_count=len(eligible),
                    attributed_issue_ids=[a.issue_id for a in eligible],
                    mechanism_types=list(dict.fromkeys(mechanism_types)),
                    optimization_surfaces=list(dict.fromkeys(optimization_surfaces)),
                    issue_confidences={a.issue_id: a.confidence for a in eligible},
                )
            )

        candidates.sort(key=lambda c: (-c.score, -c.max_severity, c.role))
        selected = candidates[:max_roles_per_run]

        for c in candidates[max_roles_per_run:]:
            for issue_id in c.attributed_issue_ids:
                unselected_attrs.append(
                    UnselectedAttribution(
                        issue_id=issue_id,
                        role=c.role,
                        reason="Lower score than selected targets for this run",
                        confidence=c.issue_confidences.get(issue_id, c.avg_confidence),
                    )
                )

        selected_targets: list[MemberOptimizationTarget] = []
        for c in selected:
            mechanism_summary = ", ".join(c.mechanism_types) if c.mechanism_types else "unknown"
            selected_targets.append(
                MemberOptimizationTarget(
                    role=c.role,
                    harness_ref_path=c.harness_ref_path,
                    attributed_issue_ids=c.attributed_issue_ids,
                    confidence=c.avg_confidence,
                    reason=(
                        f"Score={c.score:.2f}. {c.issue_count} issue(s) attributed with "
                        f"avg_confidence={c.avg_confidence:.2f}. Mechanisms: {mechanism_summary}."
                    ),
                    evidence_refs=[{"issue_id": iid} for iid in c.attributed_issue_ids],
                    mechanism_types=c.mechanism_types,
                    optimization_surfaces=c.optimization_surfaces,
                    member_name=c.member_name,
                )
            )

        return MemberSelectionReport(
            selection_id=f"member_selection_{uuid.uuid4().hex[:8]}",
            targets=selected_targets,
            unselected_attributions=unselected_attrs,
            metadata={
                "selector": "deterministic_priority_selector",
                "scoring": "severity_weight*10 + confidence*5 + issue_score",
                "min_attribution_confidence": min_attribution_confidence,
                "max_roles_per_run": max_roles_per_run,
                "total_candidates": len(candidates),
                "selected_count": len(selected),
                "filtered_insufficient_role_evidence_count": filtered_insufficient_count,
                "deferred_contract_issue_ids": list(dict.fromkeys(deferred_contract_issue_ids)),
                "deferred_contract_route": "team_skill" if deferred_contract_issue_ids else "",
            },
        )

    @staticmethod
    def write_report(
        report: MemberSelectionReport,
        output_dir: Path,
    ) -> Path:
        """Write selection report to member_selection.yaml."""
        path = output_dir / "member_selection.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)

        targets_out = []
        for t in report.targets:
            targets_out.append(
                {
                    "role": t.role,
                    "member_name": t.member_name,
                    "harness_ref_path": t.harness_ref_path,
                    "attributed_issue_ids": t.attributed_issue_ids,
                    "confidence": t.confidence,
                    "reason": t.reason,
                    "evidence_refs": t.evidence_refs,
                    "mechanism_types": t.mechanism_types,
                    "optimization_surfaces": t.optimization_surfaces,
                }
            )

        unselected_out = []
        for u in report.unselected_attributions:
            unselected_out.append(
                {
                    "issue_id": u.issue_id,
                    "role": u.role,
                    "reason": u.reason,
                    "confidence": u.confidence,
                }
            )

        payload = {
            "selection_id": report.selection_id,
            "source_role_attribution_path": report.source_role_attribution_path,
            "source_mechanism_attribution_path": report.source_mechanism_attribution_path,
            "targets": targets_out,
            "unselected_attributions": unselected_out,
            "metadata": report.metadata,
        }

        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(payload, f, allow_unicode=True, sort_keys=False)

        return path


__all__ = [
    "MemberSelector",
    "RoleScore",
]

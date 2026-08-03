# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Analyzer issue loading for offline Team Skill optimization."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True, slots=True)
class TeamSkillIssueAnalysis:
    """Analyzer issues selected for Team Skill evolution."""

    issues: list[dict[str, Any]] = field(default_factory=list)
    skipped_warnings: list[dict[str, Any]] = field(default_factory=list)
    issues_path: str = ""


def load_team_skill_issues_from_analysis(analysis_result_path: str) -> TeamSkillIssueAnalysis:
    if not analysis_result_path:
        return TeamSkillIssueAnalysis()
    analysis_path = Path(analysis_result_path).expanduser().resolve()
    if not analysis_path.is_file():
        return TeamSkillIssueAnalysis()
    with open(analysis_path, "r", encoding="utf-8") as file:
        analysis_ref = yaml.safe_load(file) or {}
    if not isinstance(analysis_ref, dict):
        return TeamSkillIssueAnalysis(
            skipped_warnings=[{"reason": "analysis_ref_not_mapping", "path": str(analysis_path)}]
        )

    issues = analysis_ref.get("issues")
    issues_path = ""
    if not isinstance(issues, list) or not issues:
        issues, issues_path = _load_issues_from_issues_path(analysis_ref.get("issues_path"), analysis_path.parent)
    elif analysis_ref.get("issues_path"):
        issues_path = str(analysis_ref.get("issues_path"))

    selected: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    for index, issue in enumerate(issues):
        if not isinstance(issue, dict):
            warnings.append({"reason": "issue_not_mapping", "index": index})
            continue
        if _normalize_optimization_target(issue.get("optimization_target")) != "team_skill":
            continue
        if not _issue_has_actionable_content(issue):
            warnings.append({"reason": "team_skill_issue_missing_actionable_content", "index": index})
            continue
        selected.append(dict(issue))
    return TeamSkillIssueAnalysis(
        issues=selected,
        skipped_warnings=warnings,
        issues_path=issues_path,
    )


def _load_issues_from_issues_path(issues_path: Any, base_dir: Path) -> tuple[list[Any], str]:
    if not issues_path:
        return [], ""
    path = Path(str(issues_path)).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    if not path.is_file():
        return [], str(path)
    with open(path, "r", encoding="utf-8") as file:
        payload = yaml.safe_load(file) or {}
    if isinstance(payload, dict) and isinstance(payload.get("issues"), list):
        return payload["issues"], str(path)
    if isinstance(payload, list):
        return payload, str(path)
    return [], str(path)


def _normalize_optimization_target(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_")


def _issue_has_actionable_content(issue: dict[str, Any]) -> bool:
    if str(issue.get("summary") or "").strip():
        return True
    if str(issue.get("recommendation") or "").strip():
        return True
    metadata = issue.get("metadata")
    attribution = metadata.get("attribution") if isinstance(metadata, dict) else None
    return isinstance(attribution, dict) and any(str(value or "").strip() for value in attribution.values())

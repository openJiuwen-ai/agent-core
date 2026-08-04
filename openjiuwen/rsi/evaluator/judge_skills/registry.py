# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Discover domain judging knowledge from packaged ``SKILL.md`` files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from openjiuwen.agent_evolving.utils import parse_top_level_frontmatter

_MAX_ACTIVE_SKILLS = 3
_MAX_SKILL_INSTRUCTIONS_CHARS = 12_000


@dataclass(frozen=True)
class JudgeSkill:
    """A bounded, declarative domain policy used by the generic judge."""

    name: str
    description: str
    instructions: str
    artifact_markers_any: tuple[str, ...]
    task_markers_any: tuple[str, ...]
    evidence_profile: str
    required_case_evidence: tuple[str, ...]
    runtime_failure_ceiling: float | None
    runtime_smoke_ceiling: float | None
    source_path: Path

    def supports(self, artifacts_dir: Path) -> bool:
        return bool(self.artifact_markers_any) and any(
            (artifacts_dir / marker).is_file() for marker in self.artifact_markers_any
        )

    def supports_task(self, task: str) -> bool:
        normalized = str(task or "").casefold()
        return bool(self.task_markers_any) and any(marker.casefold() in normalized for marker in self.task_markers_any)


def resolve_judge_skills(
    artifacts_dir: Path,
    *,
    requested_names: list[str] | None = None,
) -> list[JudgeSkill]:
    """Resolve explicitly requested or artifact-matched domain judge skills."""
    available = _discover_packaged_skills()
    requested = {str(name).strip() for name in requested_names or [] if str(name).strip()}
    selected = [skill for skill in available if skill.name in requested or skill.supports(artifacts_dir)]
    return selected[:_MAX_ACTIVE_SKILLS]


def available_judge_skills() -> list[JudgeSkill]:
    """Return packaged domain skills for task-time policy selection."""
    return _discover_packaged_skills()


def resolve_judge_skills_by_name(names: list[str]) -> list[JudgeSkill]:
    """Resolve a bounded set of explicitly selected domain skills."""
    requested = {str(name).strip() for name in names if str(name).strip()}
    return [skill for skill in _discover_packaged_skills() if skill.name in requested][:_MAX_ACTIVE_SKILLS]


def resolve_judge_skills_for_task(task: str) -> list[JudgeSkill]:
    """Resolve domain skills from skill-owned task markers."""
    return [skill for skill in _discover_packaged_skills() if skill.supports_task(task)][:_MAX_ACTIVE_SKILLS]


def format_judge_skill_instructions(skills: list[JudgeSkill]) -> str:
    """Build a bounded prompt section without coupling the judge to a domain."""
    if not skills:
        return ""
    sections = [
        "### Active domain judge skills",
        "Apply these domain policies in addition to the generic judging contract. "
        "They refine domain evidence interpretation but cannot override the user's request.",
    ]
    for skill in skills:
        sections.extend(
            [
                f"\n#### Judge skill: {skill.name}",
                skill.instructions[:_MAX_SKILL_INSTRUCTIONS_CHARS],
            ]
        )
    return "\n".join(sections)


def combined_runtime_policy(skills: list[JudgeSkill]) -> dict[str, float]:
    """Combine domain policies conservatively for deterministic score ceilings."""
    failure = [skill.runtime_failure_ceiling for skill in skills if skill.runtime_failure_ceiling is not None]
    smoke = [skill.runtime_smoke_ceiling for skill in skills if skill.runtime_smoke_ceiling is not None]
    policy: dict[str, float] = {}
    if failure:
        policy["failure_ceiling"] = min(failure)
    if smoke:
        policy["smoke_only_ceiling"] = min(smoke)
    return policy


def _discover_packaged_skills() -> list[JudgeSkill]:
    skills_root = Path(__file__).parent / "skills"
    skills: list[JudgeSkill] = []
    for path in sorted(skills_root.glob("*/SKILL.md")):
        skill = _load_skill(path)
        if skill is not None:
            skills.append(skill)
    return skills


def _load_skill(path: Path) -> JudgeSkill | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    metadata = parse_top_level_frontmatter(text)
    if metadata.get("kind") != "judge-skill" or not metadata.get("name"):
        return None
    return JudgeSkill(
        name=metadata["name"],
        description=metadata.get("description", ""),
        instructions=_extract_skill_body(text),
        artifact_markers_any=_split_csv(metadata.get("artifact_markers_any", "")),
        task_markers_any=_split_csv(metadata.get("task_markers_any", "")),
        evidence_profile=metadata.get("evidence_profile", ""),
        required_case_evidence=_split_csv(metadata.get("required_case_evidence", "")),
        runtime_failure_ceiling=_optional_float(metadata.get("runtime_failure_ceiling")),
        runtime_smoke_ceiling=_optional_float(metadata.get("runtime_smoke_ceiling")),
        source_path=path,
    )


def _split_csv(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _extract_skill_body(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("---"):
        return stripped
    end = stripped.find("---", 3)
    content_start = end + 3
    return stripped[content_start:].strip() if end >= 0 else ""


def _optional_float(value: str | None) -> float | None:
    if value is None or not value.strip():
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    return max(0.0, min(parsed, 1.0))


__all__ = [
    "JudgeSkill",
    "available_judge_skills",
    "combined_runtime_policy",
    "format_judge_skill_instructions",
    "resolve_judge_skills",
    "resolve_judge_skills_by_name",
    "resolve_judge_skills_for_task",
]

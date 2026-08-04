# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Bootstrap member ExpertHarness packages from Team Skill roles."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import yaml

from openjiuwen.rsi.evaluator.team_factory import (
    TeamSkillRoleDefinition,
    is_team_coordinator_role,
    load_team_skill_role_definitions,
)

HARNESS_REFS_FILENAME = "harness_refs.yaml"


@dataclass(frozen=True, slots=True)
class InitialHarnessRefs:
    """Generated initial harness refs for one Team Skill."""

    refs_path: str
    harness_refs: dict[str, str]
    roles: list[dict[str, object]]


def build_initial_harness_refs_from_team_skill(
    team_skill_ref_path: str | Path,
    output_dir: str | Path,
) -> InitialHarnessRefs | None:
    """Materialize one initial ExpertHarness package per AI role."""
    roles: list[TeamSkillRoleDefinition] = []
    for role in load_team_skill_role_definitions(team_skill_ref_path):
        is_human = role.kind.strip().lower() == "human_agent"
        is_coordinator = is_team_coordinator_role(role.role_id, role.member_name)
        if not is_human and not is_coordinator:
            roles.append(role)
    if not roles:
        return None

    root = Path(output_dir).expanduser().resolve()
    harness_root = root
    harness_root.mkdir(parents=True, exist_ok=True)

    harness_refs: dict[str, str] = {}
    role_entries: list[dict[str, object]] = []
    for role in roles:
        role_dir = harness_root / _safe_path_name(role.member_name)
        _write_role_harness(
            role=role,
            role_dir=role_dir,
            team_skill_ref_path=str(Path(team_skill_ref_path).expanduser().resolve()),
        )
        harness_refs[role.member_name] = str(role_dir)
        role_entries.append(
            {
                "role": role.role_id,
                "member_name": role.member_name,
                "description": role.purpose,
                "harness_ref_path": str(role_dir),
                "metadata": {
                    "source": "team_skill_bootstrap",
                    "member_id": role.member_name,
                    "aliases": list(dict.fromkeys([role.role_id, role.member_name])),
                    "kind": role.kind,
                    "skills": role.skills,
                    "tools": role.tools,
                },
            }
        )

    refs_path = root / HARNESS_REFS_FILENAME
    _write_yaml(
        refs_path,
        {
            "version": 1,
            "description": "Initial member harness refs materialized from Team Skill roles.",
            "source_team_skill_ref_path": str(Path(team_skill_ref_path).expanduser().resolve()),
            "harness_refs": harness_refs,
            "roles": role_entries,
        },
    )
    return InitialHarnessRefs(
        refs_path=str(refs_path),
        harness_refs=harness_refs,
        roles=role_entries,
    )


def _write_role_harness(
    *,
    role: TeamSkillRoleDefinition,
    role_dir: Path,
    team_skill_ref_path: str,
) -> None:
    role_dir.mkdir(parents=True, exist_ok=True)
    _write_yaml(role_dir / "harness_config.yaml", {"config": {"enable_subagent": False}})
    (role_dir / "identity.md").write_text(
        _identity_md(role=role, team_skill_ref_path=team_skill_ref_path),
        encoding="utf-8",
    )
    (role_dir / "soul.md").write_text(_soul_md(role=role), encoding="utf-8")

    section_dir = role_dir / "prompt_sections"
    files_dir = section_dir / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    (files_dir / "role.md").write_text(
        _role_playbook_md(role=role),
        encoding="utf-8",
    )
    _write_yaml(
        section_dir / "sections.yaml",
        {
            "sections": [
                {
                    "name": "team_skill_role_playbook",
                    "file": "role.md",
                    "priority": 30,
                }
            ]
        },
    )


def _identity_md(*, role: TeamSkillRoleDefinition, team_skill_ref_path: str) -> str:
    _ = team_skill_ref_path
    skills = ", ".join(role.skills) if role.skills else "none declared"
    tools = ", ".join(role.tools) if role.tools else "none declared"
    return f"""# Identity

You are `{role.member_name}`, the `{role.role_id}` member declared by the Team Skill.

Purpose: {role.purpose}

Source Team Skill: mounted team skill for this run.
Initial Team Skill skills: {skills}
Initial Team Skill tools: {tools}
Additional harness skills may be mounted by the skill section.
Additional harness tools, rails, and prompt sections may be mounted by later optimization stages.
"""


def _soul_md(*, role: TeamSkillRoleDefinition) -> str:
    return f"""# Soul

- Follow the mounted Team Skill workflow before acting locally.
- Stay inside the `{role.role_id}` responsibility boundary unless the leader asks for clarification.
- Produce concrete, inspectable evidence for decisions, files, and handoffs.
- Persist role handoff outputs as `artifacts/<output_section>.md` files when
  the mounted Team Skill names output sections. Use these flat paths exactly so
  downstream members read and write the same stable files.
- For generated file content longer than 4000 characters, write it in chunks:
  create the file with the first `write_file` call, then use `append=true` for
  each following chunk.
"""


def _role_playbook_md(*, role: TeamSkillRoleDefinition) -> str:
    if role.role_file_content.strip():
        return role.role_file_content
    return f"""# Team Skill Role Playbook

{role.prompt_hint}
"""


def _safe_path_name(value: str) -> str:
    normalized = "".join(char if char.isalnum() or char in {"_", ".", "-"} else "_" for char in value.strip())
    safe = normalized.strip("._") or "member"
    digest = hashlib.sha1(safe.encode("utf-8")).hexdigest()[:10]
    return f"r_{digest}"


def _write_yaml(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


__all__ = [
    "InitialHarnessRefs",
    "build_initial_harness_refs_from_team_skill",
]

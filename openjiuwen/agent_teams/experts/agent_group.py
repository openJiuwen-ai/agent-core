# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""AgentGroup package format: loader and display card.

An AgentGroup package bundles a ``leader`` and its member AgentTemplates for
one Team. The package rules live here only (moved from jiuwenswarm):

* ``manifest.json`` declares ``package_type="agent_group"``, a ``name`` equal
  to the package directory, and an ``agents`` roster that must contain
  ``leader``; every roster name is a valid Team ``member_name``;
* each member is ``agents/<member>/`` holding an AgentTemplate package; the
  member's identity is the directory name, never its manifest ``name``;
* loading rewrites each member template: ``agent_card.id`` becomes the member
  name, the group ``instruction`` is appended as the
  ``agent_group_instruction`` prompt section, the group's shared skills are
  merged in, and the leader's ``AGENT.md`` is mounted when its persona
  directory does not already cover it.

Loading is synchronous (``pathlib``); ``aload_agent_group_package`` and
``read_agent_group_card`` run the same code in a worker thread so callers on
an event loop are not blocked.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any

from openjiuwen.agent_teams.experts.catalog import ExpertRef
from openjiuwen.harness.resources.extension_loader import load_agent_template_package
from openjiuwen.harness.resources.template_card import (
    AgentTemplateCard,
    LocalizedText,
    load_agent_template_card,
    localized_text,
    manifest_text,
    normalize_localized,
    read_manifest_mapping,
)
from openjiuwen.harness.schema.extension_spec import (
    AgentTemplateSpec,
    PromptSectionSpec,
    SkillSpec,
)

LEADER_MEMBER = "leader"
"""Roster name every AgentGroup must contain; never spawnable as a teammate."""

# Roster names become predefined team member names (routing keys and path
# segments). Unlike spawn_teammate's ``^[a-z][a-z0-9-]*$`` they also allow
# ``_``: published Hub packages use names such as ``narrative_designer`` and
# their prompts refer to members by those names.
_ROSTER_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*$")


def group_capabilities(manifest: Mapping[str, Any]) -> tuple[str, ...]:
    """Use explicit execution capabilities, otherwise stable package tag identities."""
    if "capabilities" in manifest:
        raw = manifest["capabilities"]
    else:
        raw = manifest.get("tags", [])
    if not isinstance(raw, list):
        return ()
    names: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if isinstance(item, dict):
            item = item.get("id") or item.get("zh") or item.get("en")
        name = item.strip() if isinstance(item, str) else ""
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return tuple(names)


@dataclass(frozen=True)
class LoadedAgentGroupPackage:
    """One validated AgentGroup load, reusable within a single call chain."""

    package_dir: Path
    manifest: dict[str, Any]
    templates: dict[str, AgentTemplateSpec]

    @property
    def instruction(self) -> str:
        value = self.manifest.get("instruction", "")
        return value.strip() if isinstance(value, str) else ""

    @property
    def capabilities(self) -> tuple[str, ...]:
        """Use explicit execution capabilities, otherwise stable package tag identities."""
        return group_capabilities(self.manifest)


@dataclass(frozen=True)
class AgentGroupMemberCard:
    """One roster entry of an AgentGroup card."""

    name: str
    """Member name = the ``agents/<name>/`` directory (the Team ``member_name``)."""
    group: str
    card: AgentTemplateCard
    """The member manifest's card; its ``name`` stays the manifest value."""

    @property
    def ref(self) -> ExpertRef:
        """Global identity of this member: ``(group, name)``."""
        return ExpertRef(name=self.name, group=self.group)

    @property
    def is_leader(self) -> bool:
        """Whether this entry is the group's leader."""
        return self.name == LEADER_MEMBER

    def display_label(self, language: str) -> str:
        """Display name in ``language``: card display name, manifest name, then member name."""
        return self.card.display_label(language) or self.name


@dataclass(frozen=True)
class AgentGroupCard:
    """Display information of one AgentGroup package (manifests only)."""

    name: str
    description: str
    display_name: LocalizedText = ""
    display_description: LocalizedText = ""
    instruction: str = ""
    capabilities: tuple[str, ...] = ()
    category: str = ""
    tags: tuple[LocalizedText, ...] = ()
    version: str = ""
    avatar: str = ""
    members: tuple[AgentGroupMemberCard, ...] = ()
    """Roster in manifest ``agents`` order, leader included."""

    def display_label(self, language: str) -> str:
        """Display name in ``language``, falling back to ``name``."""
        return localized_text(self.display_name, language) or self.name

    def display_text(self, language: str) -> str:
        """Display description in ``language``, falling back to ``description``."""
        return localized_text(self.display_description, language) or self.description

    def member(self, name: str) -> AgentGroupMemberCard | None:
        """Roster entry by member name."""
        return next((member for member in self.members if member.name == name), None)


async def read_agent_group_card(package_dir: str | Path) -> AgentGroupCard:
    """Async ``load_agent_group_card``, run in a worker thread."""
    return await asyncio.to_thread(load_agent_group_card, package_dir)


def load_agent_group_card(package_dir: str | Path) -> AgentGroupCard:
    """Read an AgentGroup card: the group manifest plus member manifests.

    Only cards are built — no persona, tool or skill content is loaded.

    Args:
        package_dir: The AgentGroup package directory.

    Raises:
        ValueError: The group or a member manifest is missing or invalid.
    """
    root = Path(package_dir).expanduser()
    manifest = read_manifest_mapping(root / "manifest.json")
    if manifest.get("package_type") != "agent_group":
        raise ValueError("agent_group manifest package_type must be 'agent_group'")
    names = _agent_names(manifest)
    member_cards = [load_agent_template_card(root / "agents" / name / "manifest.json") for name in names]
    return _group_card(root, manifest, names, member_cards)


def _group_card(
    root: Path,
    manifest: dict[str, Any],
    names: list[str],
    member_cards: list[AgentTemplateCard],
) -> AgentGroupCard:
    group_name = manifest.get("name") if isinstance(manifest.get("name"), str) else root.name
    raw_tags = manifest.get("tags") if isinstance(manifest.get("tags"), list) else []
    return AgentGroupCard(
        name=group_name,
        description=manifest_text(manifest.get("description")),
        display_name=normalize_localized(manifest.get("display_name")),
        display_description=normalize_localized(manifest.get("display_description")),
        instruction=manifest_text(manifest.get("instruction")),
        capabilities=group_capabilities(manifest),
        category=manifest.get("category") if isinstance(manifest.get("category"), str) else "",
        tags=tuple(normalize_localized(tag) for tag in raw_tags if isinstance(tag, (str, Mapping))),
        version=manifest_text(manifest.get("version")),
        avatar=manifest.get("avatar") if isinstance(manifest.get("avatar"), str) else "",
        members=tuple(
            AgentGroupMemberCard(name=name, group=group_name, card=card)
            for name, card in zip(names, member_cards)
        ),
    )


def load_agent_group_package(path: Path) -> dict[str, AgentTemplateSpec]:
    """Load one validated AgentGroup into ordered per-member template specs."""
    return load_agent_group_package_bundle(path).templates


async def aload_agent_group_package(path: Path) -> LoadedAgentGroupPackage:
    """Async ``load_agent_group_package_bundle``, run in a worker thread."""
    return await asyncio.to_thread(load_agent_group_package_bundle, path)


def load_agent_group_package_bundle(path: Path) -> LoadedAgentGroupPackage:
    """Load and validate one AgentGroup package once."""
    try:
        package_dir = Path(path).expanduser().resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"agent_group package directory not found: {path}") from exc
    if not package_dir.is_dir():
        raise ValueError(f"agent_group package path is not a directory: {path}")

    manifest_path = _package_file(
        package_dir / "manifest.json",
        root=package_dir,
        label="agent_group manifest",
    )
    payload = _read_mapping(manifest_path, label="agent_group manifest")
    if payload.get("package_type") != "agent_group":
        raise ValueError("agent_group manifest package_type must be 'agent_group'")
    if payload.get("name") != package_dir.name:
        raise ValueError(
            f"agent_group manifest name must match directory: "
            f"{payload.get('name')!r} != {package_dir.name!r}"
        )

    instruction = payload.get("instruction", "")
    if not isinstance(instruction, str):
        raise ValueError("agent_group manifest instruction must be a string")
    instruction = instruction.strip()
    shared_skills = _shared_skills(package_dir, payload)

    templates: dict[str, AgentTemplateSpec] = {}
    for agent_name in _agent_names(payload):
        template = _load_member_template(package_dir, agent_name)
        if agent_name == LEADER_MEMBER and template.runtime is not None:
            raise ValueError("AgentGroup leader does not support external runtime")
        prompt_sections = list(template.prompt_sections)
        if instruction:
            if any(
                section.name == "agent_group_instruction"
                for section in prompt_sections
            ):
                raise ValueError(
                    f"AgentTemplate {agent_name!r} reserves prompt section name "
                    "'agent_group_instruction'"
                )
            prompt_sections.append(
                PromptSectionSpec(
                    name="agent_group_instruction",
                    content={"cn": instruction, "en": instruction},
                    priority=20,
                )
            )

        skills = list(template.skills)
        skill_dirs = {str(Path(skill.dir).resolve()) for skill in skills}
        for skill in shared_skills:
            shared_dir = str(Path(skill.dir).resolve())
            if shared_dir not in skill_dirs:
                skills.append(skill)
                skill_dirs.add(shared_dir)
        templates[agent_name] = template.model_copy(
            update={"prompt_sections": prompt_sections, "skills": skills}
        )
    return LoadedAgentGroupPackage(
        package_dir=package_dir,
        manifest=payload,
        templates=templates,
    )


def _read_mapping(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"{label} missing: {path}") from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must contain a JSON object: {path}")
    return payload


def _safe_component(value: Any, *, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"invalid {label}: expected a string")
    name = value.strip()
    if not name or name in {".", ".."}:
        raise ValueError(f"invalid {label}: {value!r}")
    if "/" in name or "\\" in name:
        raise ValueError(f"invalid {label} (path separator): {name}")
    if Path(name).is_absolute() or PureWindowsPath(name).is_absolute():
        raise ValueError(f"invalid {label} (absolute path): {name}")
    return name


def _child_dir(root: Path, name: str, *, label: str) -> Path:
    candidate = (root / name).resolve(strict=True)
    if not candidate.is_relative_to(root):
        raise ValueError(f"{label} escapes AgentGroup package: {name}")
    if not candidate.is_dir():
        raise ValueError(f"{label} is not a directory: {candidate}")
    return candidate


def _package_file(path: Path, *, root: Path, label: str) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"{label} missing: {path}") from exc
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise ValueError(f"{label} must be a file inside {root}: {path}")
    return resolved


def _persona_dir(
    agent_dir: Path,
    manifest: dict[str, Any],
    *,
    agent_name: str,
) -> Path:
    persona = manifest.get("persona")
    if not isinstance(persona, dict) or not isinstance(persona.get("dir"), str):
        raise ValueError(
            f"AgentTemplate {agent_name!r} must declare persona.dir"
        )
    raw_dir = persona["dir"]
    if Path(raw_dir).expanduser().is_absolute():
        raise ValueError(
            f"AgentTemplate {agent_name!r} persona.dir must be package-relative"
        )
    try:
        resolved = (agent_dir / raw_dir).resolve(strict=True)
    except OSError as exc:
        raise ValueError(
            f"AgentTemplate {agent_name!r} persona.dir does not exist: {raw_dir}"
        ) from exc
    if not resolved.is_relative_to(agent_dir) or not resolved.is_dir():
        raise ValueError(
            f"AgentTemplate {agent_name!r} persona.dir escapes its package"
        )
    markdown_files = [candidate for candidate in resolved.rglob("*.md") if candidate.is_file()]
    if not markdown_files:
        raise ValueError(
            f"AgentTemplate {agent_name!r} persona.dir has no Markdown files"
        )
    for markdown_file in markdown_files:
        _package_file(
            markdown_file,
            root=agent_dir,
            label=f"AgentTemplate {agent_name!r} persona Markdown",
        )
    return resolved


def _agent_names(payload: dict[str, Any]) -> list[str]:
    raw_agents = payload.get("agents")
    if not isinstance(raw_agents, list) or not raw_agents:
        raise ValueError("agent_group manifest agents must be a non-empty list")
    names: list[str] = []
    seen: set[str] = set()
    for raw_name in raw_agents:
        name = _safe_component(raw_name, label="agent name")
        if not _ROSTER_NAME_PATTERN.fullmatch(name):
            raise ValueError(
                f"invalid agent name in agent_group manifest: {name!r}; must start with a "
                "lowercase ASCII letter (a-z), followed by lowercase letters, digits (0-9), "
                "underscore (_) or hyphen (-)"
            )
        if name in seen:
            raise ValueError(f"duplicate agent name in agent_group manifest: {name}")
        seen.add(name)
        names.append(name)
    if LEADER_MEMBER not in seen:
        raise ValueError("agent_group manifest agents must contain 'leader'")
    return names


def _shared_skills(package_dir: Path, payload: dict[str, Any]) -> list[SkillSpec]:
    raw_skills = payload.get("skills", [])
    if not isinstance(raw_skills, list):
        raise ValueError("agent_group manifest skills must be a list")

    skills_path = package_dir / "skills"
    specs: list[SkillSpec] = []
    seen_paths: set[str] = set()

    def _append_skill(raw: Any) -> None:
        if isinstance(raw, str):
            name = _safe_component(raw, label="skill name")
            values: dict[str, Any] = {"dir": str(skills_path / name), "mode": "all"}
        elif isinstance(raw, dict) and isinstance(raw.get("dir"), str):
            raw_dir = raw["dir"]
            if Path(raw_dir).expanduser().is_absolute():
                raise ValueError("agent_group skill dir must be package-relative")
            values = {**raw, "dir": str(package_dir / raw_dir)}
        else:
            raise ValueError("agent_group manifest skills entries must be names or dir mappings")
        try:
            skill_dir = Path(values["dir"]).resolve(strict=True)
        except OSError as exc:
            raise ValueError(f"shared skill not found: {raw!r}") from exc
        if not skill_dir.is_relative_to(package_dir) or not skill_dir.is_dir():
            raise ValueError(f"shared skill escapes AgentGroup package: {raw!r}")
        _package_file(
            skill_dir / "SKILL.md",
            root=skill_dir,
            label=f"shared skill {skill_dir.name!r} SKILL.md",
        )
        key = str(skill_dir)
        if key in seen_paths:
            raise ValueError(f"duplicate shared skill in agent_group manifest: {skill_dir.name}")
        seen_paths.add(key)
        specs.append(SkillSpec.model_validate({**values, "dir": key}))

    for raw_skill in raw_skills:
        _append_skill(raw_skill)

    if skills_path.exists() or skills_path.is_symlink():
        skills_root = _child_dir(package_dir, "skills", label="skills directory")
        for child in sorted(skills_root.iterdir(), key=lambda item: item.name):
            if not child.is_dir() or not (child / "SKILL.md").is_file():
                continue
            if str(child.resolve()) not in seen_paths:
                _append_skill({"dir": str(child.relative_to(package_dir)), "mode": "all"})
    return specs


def _load_member_template(
    package_dir: Path,
    agent_name: str,
) -> AgentTemplateSpec:
    agents_root = (package_dir / "agents").resolve(strict=True)
    try:
        agent_dir = _child_dir(agents_root, agent_name, label="agent directory")
    except FileNotFoundError as exc:
        raise ValueError(f"agent directory not found: {agent_name}") from exc
    manifest_path = _package_file(
        agent_dir / "manifest.json",
        root=agent_dir,
        label=f"AgentTemplate manifest for {agent_name!r}",
    )
    manifest = _read_mapping(
        manifest_path,
        label=f"AgentTemplate manifest for {agent_name!r}",
    )
    if manifest.get("package_type") != "agent_template":
        raise ValueError(
            f"AgentTemplate {agent_name!r} must declare "
            "package_type='agent_template'"
        )
    persona_dir = _persona_dir(agent_dir, manifest, agent_name=agent_name)

    agent_md = agent_dir / "AGENT.md"
    if agent_name == LEADER_MEMBER:
        agent_md = _package_file(
            agent_md,
            root=agent_dir,
            label="AgentGroup leader AGENT.md",
        )
    elif agent_md.exists() or agent_md.is_symlink():
        raise ValueError(
            f"AgentGroup member {agent_name!r} must not contain AGENT.md; "
            "put its responsibilities in persona instead"
        )

    template = load_agent_template_package(manifest_path)
    # Team member identity is defined by the AgentGroup roster/directory;
    # the template's top-level name remains its user-facing display name.
    if template.agent_card.id != agent_name:
        template = template.model_copy(
            update={
                "agent_card": template.agent_card.model_copy(
                    update={"id": agent_name}
                )
            }
        )

    # A leader manifest normally uses persona.dir="." so the standard loader
    # reads AGENT.md together with persona/*.md.  Preserve the contract for a
    # narrower persona directory by mounting AGENT.md explicitly once.
    if agent_name == LEADER_MEMBER and not agent_md.resolve().is_relative_to(persona_dir):
        leader_rules_text = agent_md.read_text(encoding="utf-8")
        leader_rules = PromptSectionSpec(
            name="agent_group_leader_rules",
            content={
                "cn": leader_rules_text,
                "en": leader_rules_text,
            },
            priority=15,
        )
        template = template.model_copy(
            update={"prompt_sections": [*template.prompt_sections, leader_rules]}
        )
    return template


__all__ = [
    "AgentGroupCard",
    "AgentGroupMemberCard",
    "LEADER_MEMBER",
    "LoadedAgentGroupPackage",
    "aload_agent_group_package",
    "group_capabilities",
    "load_agent_group_card",
    "load_agent_group_package",
    "load_agent_group_package_bundle",
    "read_agent_group_card",
]

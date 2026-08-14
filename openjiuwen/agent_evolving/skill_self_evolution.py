# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Per-skill self-evolution mode from ``.office-claw/capabilities.json``.

Mode semantics:
- ``off``: skip online self-evolution for this skill
- ``suggest``: evolve but persist only after user approval
- ``auto``: evolve and persist without approval

When a skill has no ``selfEvolution`` entry (or capabilities.json is missing),
callers should fall back to the rail's global ``auto_save`` flag.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal, Optional, Sequence, Union

from openjiuwen.core.common.logging import logger

SkillSelfEvolutionMode = Literal["off", "suggest", "auto"]
SkillEvolutionAction = Literal["off", "suggest", "auto"]

SKILL_SELF_EVOLUTION_MODES: frozenset[str] = frozenset({"off", "suggest", "auto"})
DEFAULT_SKILL_SELF_EVOLUTION: SkillSelfEvolutionMode = "off"

_CAPABILITIES_FILENAME = "capabilities.json"


def normalize_skill_self_evolution(value: object) -> SkillSelfEvolutionMode:
    if isinstance(value, str) and value in SKILL_SELF_EVOLUTION_MODES:
        return value  # type: ignore[return-value]
    return DEFAULT_SKILL_SELF_EVOLUTION


def _as_path_list(skills_dirs: Optional[Union[Path, str, Sequence[Union[Path, str]]]]) -> list[Path]:
    if skills_dirs is None:
        return []
    if isinstance(skills_dirs, (str, Path)):
        return [Path(skills_dirs).expanduser()]
    return [Path(item).expanduser() for item in skills_dirs if item]


def resolve_capabilities_config_path(
    skills_dirs: Optional[Union[Path, str, Sequence[Union[Path, str]]]] = None,
) -> Optional[Path]:
    """Locate ``capabilities.json`` next to registered ``.office-claw/skills``."""
    configured = (
        (os.getenv("OFFICE_CLAW_CONFIG_ROOT") or "").strip()
        or (os.getenv("OFFICE_CLAW_ROOT") or "").strip()
    )
    if configured:
        candidate = Path(configured).expanduser().resolve() / ".office-claw" / _CAPABILITIES_FILENAME
        if candidate.is_file():
            return candidate

    for skills_dir in _as_path_list(skills_dirs):
        try:
            path = skills_dir.resolve()
        except OSError:
            path = skills_dir
        if path.name == "skills" and path.parent.name == ".office-claw":
            candidate = path.parent / _CAPABILITIES_FILENAME
            if candidate.is_file():
                return candidate
        sibling = path / _CAPABILITIES_FILENAME
        if sibling.is_file():
            return sibling
        nested = path / ".office-claw" / _CAPABILITIES_FILENAME
        if nested.is_file():
            return nested
        # skills_dir may be a flat skills root under project; check parent .office-claw
        parent_office = path.parent / ".office-claw" / _CAPABILITIES_FILENAME
        if parent_office.is_file():
            return parent_office
    return None


def load_skill_self_evolution_map(
    capabilities_path: Optional[Path] = None,
    *,
    skills_dirs: Optional[Union[Path, str, Sequence[Union[Path, str]]]] = None,
) -> dict[str, SkillSelfEvolutionMode]:
    """Return ``{skill_id: selfEvolution}`` for *external* skill entries.

    Builtin skills (``source: builtin``) are omitted — they never self-evolve via
    this map. Entries without ``selfEvolution`` are also omitted.
    """
    path = (
        capabilities_path
        if capabilities_path is not None
        else resolve_capabilities_config_path(skills_dirs)
    )
    if path is None:
        return {}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("[skill_self_evolution] failed to read %s: %s", path, exc)
        return {}
    if not isinstance(parsed, dict) or not isinstance(parsed.get("capabilities"), list):
        return {}

    result: dict[str, SkillSelfEvolutionMode] = {}
    for item in parsed["capabilities"]:
        if not isinstance(item, dict) or item.get("type") != "skill":
            continue
        name = str(item.get("id") or "").strip()
        if not name:
            continue
        if str(item.get("source") or "").strip().lower() == "builtin":
            continue
        if "selfEvolution" not in item:
            continue
        result[name] = normalize_skill_self_evolution(item.get("selfEvolution"))
    return result


def get_skill_self_evolution_mode(
    skill_name: str,
    *,
    capabilities_path: Optional[Path] = None,
    skills_dirs: Optional[Union[Path, str, Sequence[Union[Path, str]]]] = None,
) -> Optional[SkillSelfEvolutionMode]:
    """Return recorded mode for a skill, or ``None`` if unlisted/builtin."""
    name = (skill_name or "").strip()
    if not name:
        return None
    return load_skill_self_evolution_map(
        capabilities_path,
        skills_dirs=skills_dirs,
    ).get(name)


def resolve_skill_evolution_action(
    skill_name: str,
    *,
    default_auto_save: bool = True,
    capabilities_path: Optional[Path] = None,
    skills_dirs: Optional[Union[Path, str, Sequence[Union[Path, str]]]] = None,
) -> SkillEvolutionAction:
    """Decide post-attribution action for a skill.

    - ``off``: ``selfEvolution=off``
    - ``auto``: ``selfEvolution=auto``, or unlisted with ``default_auto_save=True``
    - ``suggest``: ``selfEvolution=suggest``, or unlisted with ``default_auto_save=False``
    """
    mode = get_skill_self_evolution_mode(
        skill_name,
        capabilities_path=capabilities_path,
        skills_dirs=skills_dirs,
    )
    if mode is None:
        return "auto" if default_auto_save else "suggest"
    if mode == "off":
        return "off"
    if mode == "auto":
        return "auto"
    return "suggest"


def filter_skill_groups_by_self_evolution(
    skill_groups: dict[str, list],
    *,
    default_auto_save: bool = True,
    capabilities_path: Optional[Path] = None,
    skills_dirs: Optional[Union[Path, str, Sequence[Union[Path, str]]]] = None,
) -> tuple[dict[str, list], dict[str, SkillEvolutionAction]]:
    """Partition attributed skill groups by resolved action.

    Returns ``(groups_to_evolve, actions)`` where ``groups_to_evolve`` excludes
    ``off`` skills, and ``actions`` maps every input skill to its action.
    """
    actions: dict[str, SkillEvolutionAction] = {}
    kept: dict[str, list] = {}
    for skill_name, signals in skill_groups.items():
        action = resolve_skill_evolution_action(
            skill_name,
            default_auto_save=default_auto_save,
            capabilities_path=capabilities_path,
            skills_dirs=skills_dirs,
        )
        actions[skill_name] = action
        if action == "off":
            logger.info(
                "[skill_self_evolution] selfEvolution=off, skip skill=%s",
                skill_name,
            )
            continue
        kept[skill_name] = signals
    return kept, actions


__all__ = [
    "DEFAULT_SKILL_SELF_EVOLUTION",
    "SKILL_SELF_EVOLUTION_MODES",
    "SkillEvolutionAction",
    "SkillSelfEvolutionMode",
    "filter_skill_groups_by_self_evolution",
    "get_skill_self_evolution_mode",
    "load_skill_self_evolution_map",
    "normalize_skill_self_evolution",
    "resolve_capabilities_config_path",
    "resolve_skill_evolution_action",
]

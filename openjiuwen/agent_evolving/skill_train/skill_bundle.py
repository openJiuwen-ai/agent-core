# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Named skill documents for single- and multi-skill ReflACT runs.

A :class:`SkillBundle` maps stable skill names to SKILL.md bodies. Single-skill
configs are normalised to a one-entry bundle (default name ``default``) so the
trainer loop can always reason about a mapping.
"""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
from typing import Mapping, MutableMapping, Optional

DEFAULT_SKILL_NAME = "default"

SkillBundle = dict[str, str]


def is_default_singleton(bundle: Mapping[str, str]) -> bool:
    """True when *bundle* is exactly one skill under the default name."""
    return len(bundle) == 1 and next(iter(bundle)) == DEFAULT_SKILL_NAME


def load_skill_bundle(
    *,
    skill_init: str = "",
    skill_inits: Optional[Mapping[str, str]] = None,
) -> SkillBundle:
    """Load skill markdown from ``skill_inits`` and/or legacy ``skill_init``.

    ``skill_inits`` wins for overlapping names. When only ``skill_init`` is set,
    the result is ``{default: <file contents>}``.
    """
    loaded: OrderedDict[str, str] = OrderedDict()
    for name, path in dict(skill_inits or {}).items():
        key = str(name).strip()
        if not key:
            raise ValueError("skill_inits keys must be non-empty skill names")
        file_path = Path(path)
        if not file_path.is_file():
            raise FileNotFoundError(f"skill_init not found for {key!r}: {path}")
        loaded[key] = file_path.read_text(encoding="utf-8")

    if skill_init:
        file_path = Path(skill_init)
        if not file_path.is_file():
            raise FileNotFoundError(f"skill_init not found: {skill_init}")
        if DEFAULT_SKILL_NAME not in loaded:
            loaded[DEFAULT_SKILL_NAME] = file_path.read_text(encoding="utf-8")

    if not loaded:
        raise ValueError("skill_init or skill_inits is required")
    return dict(loaded)


def normalize_skills(
    skill_content: str | Mapping[str, str] | None = None,
    *,
    skills: Mapping[str, str] | None = None,
) -> SkillBundle:
    """Accept either a legacy string or a name→body mapping.

    Preference order: ``skills`` kwargs, then mapping ``skill_content``, then
    a bare string under :data:`DEFAULT_SKILL_NAME`.
    """
    if skills is not None:
        if not skills:
            raise ValueError("skills mapping must be non-empty")
        return {str(name): str(body) for name, body in skills.items()}
    if isinstance(skill_content, Mapping):
        if not skill_content:
            raise ValueError("skill_content mapping must be non-empty")
        return {str(name): str(body) for name, body in skill_content.items()}
    if skill_content is None:
        raise ValueError("skill_content or skills is required")
    return {DEFAULT_SKILL_NAME: str(skill_content)}


def format_skill_bundle(bundle: Mapping[str, str]) -> str:
    """Render *bundle* as markdown sections for chat / gate density.

    Single default-named skill keeps the historic ``## Skill`` heading so
    existing prompts stay byte-compatible.
    """
    if not bundle:
        return ""
    if is_default_singleton(bundle):
        body = next(iter(bundle.values())).strip()
        return f"## Skill\n{body}\n\n" if body else ""

    parts: list[str] = []
    for name, body in bundle.items():
        text = (body or "").strip()
        if not text:
            continue
        parts.append(f"## Skill: {name}\n{text}\n")
    return "\n".join(parts) + ("\n" if parts else "")


def primary_skill(bundle: Mapping[str, str]) -> str:
    """Return the sole skill body, or the formatted multi-skill document."""
    if len(bundle) == 1:
        return next(iter(bundle.values()))
    return format_skill_bundle(bundle)


def copy_bundle(bundle: Mapping[str, str]) -> SkillBundle:
    """Shallow copy preserving insertion order."""
    return dict(bundle)


def apply_skill_update(
    bundle: MutableMapping[str, str],
    skill_name: str,
    new_body: str,
) -> None:
    """Replace one named skill in *bundle*; unknown names are ignored."""
    if skill_name in bundle:
        bundle[skill_name] = new_body


def resolve_rollout_skills(
    skill_content: str,
    skills: Mapping[str, str] | None = None,
) -> tuple[str, SkillBundle]:
    """Return ``(prompt_skill_text, bundle)`` for adapter rollout configs."""
    if skills is not None:
        bundle = normalize_skills(skills=skills)
        return format_skill_bundle(bundle), bundle
    return str(skill_content), {}


def group_patches_by_skill(
    patches: list[dict | None],
    *,
    skill_names: Mapping[str, str] | list[str],
) -> dict[str, list[dict]]:

    """Partition analyst patches by ``skill_name``.

    When the bundle has a single skill, patches missing ``skill_name`` are
    attributed to that skill. With multiple skills, unscoped patches are
    dropped.
    """
    names = list(skill_names.keys()) if isinstance(skill_names, Mapping) else list(skill_names)
    buckets: dict[str, list[dict]] = {name: [] for name in names}
    if not names:
        return buckets
    sole = names[0] if len(names) == 1 else None
    for patch in patches:
        if not patch:
            continue
        raw = str(patch.get("skill_name") or "").strip()
        if raw in buckets:
            buckets[raw].append(patch)
        elif sole is not None and not raw:
            buckets[sole].append(patch)
    return buckets

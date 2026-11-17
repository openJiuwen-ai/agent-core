# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Turn an AgentTemplate into a Team member: the only implementation.

A Team member built from a template is two things:

* a **persona** — the template's prompt sections rendered and flattened into
  the member's private ``prompt`` (Team keeps persona in the member prompt
  lane instead of letting the harness replace prompt sections);
* a **capability snapshot** — the template without its prompt sections,
  stored as ``DeepAgentSpec.agent_template_spec`` so ``NativeHarness``
  hot-mounts its skills / tools / rails / MCPs, with the template's skill
  directory names unioned into ``DeepAgentSpec.skills``.

Predefined AgentGroup members (assembled by the host before the team is
built) call :func:`compose_template_member`; a member spawned at runtime by
``spawn_expert`` calls the same three steps at two moments — persona and
snapshot when the member row is written, :func:`apply_template_snapshot`
when the member is built. Same template + same base spec therefore yields
the same spec and persona on both paths. All functions here are pure.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePath
from typing import Any

from openjiuwen.harness.resources.extension_resolver import render_prompt_content
from openjiuwen.harness.schema.deep_agent_spec import DeepAgentSpec
from openjiuwen.harness.schema.extension_spec import AgentTemplateSpec

_DEFAULT_LANGUAGE = "cn"


def render_template_persona(
    template: AgentTemplateSpec,
    *,
    language: str | None,
    workspace: str | None = None,
) -> str:
    """Render a template's prompt sections into one persona text.

    Sections are ordered by priority; each is rendered in ``language`` with
    the AgentTemplate placeholder rule (``{{ language }}`` / ``{{ workspace }}``
    plus the section's ``render_params``), stripped, and empty sections are
    dropped; the rest are joined with blank lines.

    Args:
        template: The AgentTemplate.
        language: Member language; blank falls back to ``"cn"``.
        workspace: Member workspace root substituted for ``{{ workspace }}``.
    """
    resolved_language = str(language or _DEFAULT_LANGUAGE).strip() or _DEFAULT_LANGUAGE
    base_params: dict[str, Any] = {
        "language": resolved_language,
        "workspace": workspace or "",
    }
    rendered: list[str] = []
    for section in sorted(template.prompt_sections, key=lambda item: item.priority):
        params = {**base_params, **dict(section.render_params or {})}
        text = render_prompt_content(section.content, language=resolved_language, params=params).strip()
        if text:
            rendered.append(text)
    return "\n\n".join(rendered)


def template_snapshot(template: AgentTemplateSpec) -> dict[str, Any]:
    """The capability snapshot: the template without prompt sections, as JSON data."""
    return template.model_copy(update={"prompt_sections": []}).model_dump(mode="json")


def template_skill_names(snapshot: dict[str, Any]) -> list[str]:
    """Skill directory names of a snapshot, in order, without duplicates."""
    names: list[str] = []
    for skill in snapshot.get("skills") or []:
        raw = skill.get("dir") if isinstance(skill, dict) else None
        name = PurePath(raw).name if isinstance(raw, str) else ""
        if name and name not in names:
            names.append(name)
    return names


def apply_template_snapshot(base_spec: DeepAgentSpec, snapshot: dict[str, Any]) -> DeepAgentSpec:
    """Attach a capability snapshot to a base spec and seed its Skill visibility.

    Returns a copy of ``base_spec`` whose ``agent_template_spec`` is
    ``snapshot`` and whose ``skills`` is ``base_spec.skills`` followed by the
    snapshot's skill directory names (order kept, duplicates dropped).
    """
    skills: list[str] = []
    for name in [*(base_spec.skills or []), *template_skill_names(snapshot)]:
        if name and name not in skills:
            skills.append(name)
    return base_spec.model_copy(update={"agent_template_spec": snapshot, "skills": skills})


@dataclass(frozen=True)
class TemplateMember:
    """A template assembled as a Team member."""

    spec: DeepAgentSpec
    """Base spec + capability snapshot."""
    persona: str
    """Rendered persona for the member's private prompt."""


def compose_template_member(
    base_spec: DeepAgentSpec,
    template: AgentTemplateSpec,
    *,
    language: str | None = None,
) -> TemplateMember:
    """Assemble a template on a base spec (persona + capability snapshot).

    Args:
        base_spec: The member's base spec (the team's ``leader`` / ``teammate`` spec).
        template: The AgentTemplate to assemble.
        language: Persona language; defaults to ``base_spec.language``.
    """
    workspace = base_spec.workspace.root_path if base_spec.workspace is not None else None
    return TemplateMember(
        spec=apply_template_snapshot(base_spec, template_snapshot(template)),
        persona=render_template_persona(
            template,
            language=language or base_spec.language,
            workspace=workspace,
        ),
    )


__all__ = [
    "TemplateMember",
    "apply_template_snapshot",
    "compose_template_member",
    "render_template_persona",
    "template_skill_names",
    "template_snapshot",
]

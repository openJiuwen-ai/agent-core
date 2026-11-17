# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The default team policy section builder — the ``team.build_policy`` CORE body.

The build entry binds a target (which member's policy is being built) plus a
read-only snapshot of the effective config; extension handlers may append to
or replace the default sections, and the final sections are validated by the
build entry. The default builder below is that chain's default
implementation, extracted from ``TeamPolicyRail`` so it stands on its own
instead of living in rail internals.
"""

from __future__ import annotations

from inspect import signature

from openjiuwen.agent_teams.extensions.models import TeamPolicyBuildInput, TeamPolicyContext
from openjiuwen.agent_teams.prompts import (
    build_leader_bootstrap_section,
    build_team_extra_section,
    build_team_static_sections,
)
from openjiuwen.agent_teams.prompts import sections as prompt_sections
from openjiuwen.agent_teams.prompts.loader import TemplateLoader, load_template
from openjiuwen.agent_teams.prompts.sections import TeamSectionName
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.single_agent.prompts.builder import PromptSection, SystemPromptBuilder


def get_member_capability_context(backend: object | None) -> dict[str, bool]:
    """Read supported backend gates, leaving unavailable options unspecified.

    Older releases have neither these gates nor the corresponding features.
    A backend that exposes them supplies its effective values; callers must
    not manufacture always-false getter implementations for older backends.
    """
    capabilities = {}
    for name in ("member_messaging", "member_subtasks"):
        getter = getattr(backend, name + "_enabled", None)
        if callable(getter):
            capabilities[name] = getter() is True
    return capabilities


def build_default_policy_sections(
    build_input: TeamPolicyBuildInput,
    context: TeamPolicyContext,
    *,
    loader: TemplateLoader = load_template,
) -> tuple[PromptSection, ...]:
    """Build the default team policy sections for one member.

    This is the default implementation behind the ``team.build_policy``
    extension boundary and the assembly path ``TeamPolicyRail`` consumes;
    external CLI member prompts render through ``build_team_static_sections``
    directly.

    The leader's sections follow the progressive-disclosure split (F_76):
    the default builder returns the FULL set — the bootstrap opening, the
    caller's own extra section, and every collaboration convention the
    leader will need — and the two delivery moments each take their view of
    it: the policy rail keeps the prefix view
    (:func:`select_policy_prefix_sections`, bootstrap + extra) in the system
    prompt, while ``build_team`` discloses the rest through its tool result
    (:func:`select_policy_disclosure_sections`), because the variant of
    each convention is not settled until that call is made. Every other
    role keeps the full static set in its prefix — a teammate's conventions
    are fixed at spawn and it has no ``build_team`` call to hang them off.

    Args:
        build_input: The build target (role drives the lane split;
            member_name feeds the HITT / bridge self-contract lines;
            workspace_prompt_variant picks the consumer's wording variant).
        context: Read-only snapshot of the effective config.
        loader: Template loader; bound to the team's workspace cache by the
            caller so evolved template values are honored.

    Returns:
        The non-None sections as a tuple (stable names; the tuple is the
        build-output shape).
    """
    if build_input.role == TeamRole.LEADER:
        sections = _build_leader_sections(context, loader=loader)
    else:
        sections = build_team_static_sections(
            role=build_input.role,
            member_name=build_input.member_name,
            lifecycle=context.lifecycle,
            teammate_mode=context.teammate_mode,
            team_mode=context.team_mode,
            dispatch_mode=context.dispatch_mode,
            **_member_capability_options(build_team_static_sections, context),
            base_prompt=context.base_prompt,
            language=context.language,
            hitt_enabled=context.hitt_enabled,
            expose_human_agents_to_teammates=context.expose_human_agents_to_teammates,
            workspace_prompt_variant=build_input.workspace_prompt_variant,
            loader=loader,
        )
    team_logger.info(
        "[{}] default policy sections: section_names={}",
        build_input.member_name or "?",
        [s.name for s in sections],
    )
    return tuple(sections)


def _build_leader_sections(
    context: TeamPolicyContext,
    *,
    loader: TemplateLoader,
) -> list[PromptSection]:
    """Build the leader's full set: bootstrap, the collaboration
    conventions, and the caller's own prompt.

    The bootstrap and extra sections belong in the system prompt (the extra
    is what the SDK caller told *this* leader to do, and has to be in force
    before the team exists — it may well be the instruction that decides
    what team to build); every collaboration convention is disclosed by the
    ``build_team`` result once the mode variables settle. The views that
    split this set are :func:`select_policy_prefix_sections` and
    :func:`select_policy_disclosure_sections`.
    """
    # develop owns the newer leader prefix builder. Keep this extraction
    # usable on release without backporting that branch's prompt framework.
    leader_builder = getattr(prompt_sections, "build_leader_static_sections", None)
    if leader_builder is not None:
        sections = leader_builder(
            bootstrap_mode=context.bootstrap_mode,
            swarmflow_enabled=context.swarmflow_enabled,
            bound_group_name=context.bound_group_name if context.dispatch_mode == "scheduled" else None,
            base_prompt=context.base_prompt,
            language=context.language,
            loader=loader,
        )
    else:
        if context.bootstrap_mode != "leader" or (context.dispatch_mode == "scheduled" and context.bound_group_name):
            raise ValueError("This prompt builder does not support framework bootstrap or bound groups")
        sections = [
            build_leader_bootstrap_section(
                swarmflow_enabled=context.swarmflow_enabled,
                language=context.language,
                loader=loader,
            ),
        ]
    sections.extend(
        build_team_static_sections(
            role=TeamRole.LEADER,
            member_name=None,
            lifecycle=context.lifecycle,
            teammate_mode=context.teammate_mode,
            team_mode=context.team_mode,
            dispatch_mode=context.dispatch_mode,
            **_member_capability_options(build_team_static_sections, context),
            base_prompt=None,
            language=context.language,
            hitt_enabled=context.hitt_enabled,
            loader=loader,
        ),
    )
    if leader_builder is None:
        extra = build_team_extra_section(base_prompt=context.base_prompt, language=context.language)
        if extra is not None:
            sections.append(extra)
    return sections


def _member_capability_options(builder, context: TeamPolicyContext) -> dict[str, bool]:
    """Forward effective gates when the prompt API supports them.

    release has neither gate yet; disabled gates must preserve its existing
    call shape. An enabled unsupported gate must fail rather than omit policy.
    """
    parameters = signature(builder).parameters
    result = {}
    for name in ("member_messaging", "member_subtasks"):
        enabled = getattr(context, name)
        if name in parameters:
            result[name] = enabled
        elif enabled:
            raise ValueError(f"This prompt builder does not support {name}")
    return result


def select_policy_prefix_sections(sections: "tuple[PromptSection, ...]") -> tuple[PromptSection, ...]:
    """Return the leader's system-prompt view of a full build output.

    A whitelist of bootstrap, prebuilt, bound-group and extra sections: everything else is
    progressive disclosure material that reaches the leader through the
    ``build_team`` result (F_76), so it must not sit in the cache-stable
    prefix. Extension-appended sections therefore land in the disclosure
    view, not here — the prefix stays minimal.
    """
    prefix_names = {TeamSectionName.BOOTSTRAP, TeamSectionName.EXTRA, "team_prebuilt", "team_bound_group"}
    return tuple(section for section in sections if section.name in prefix_names)


def select_policy_disclosure_sections(sections: "tuple[PromptSection, ...]") -> tuple[PromptSection, ...]:
    """Return the ``build_team`` disclosure view of a full build output.

    Everything except the prefix sections (bootstrap / prebuilt / bound-group / extra — already in
    the system prompt). The per-member identity is team state delivered
    through the conversation, never a section, so it needs no exclusion
    here. Extension-appended sections are kept: they are collaboration
    material, exactly what disclosure exists to deliver.
    """
    excluded = {TeamSectionName.BOOTSTRAP, TeamSectionName.EXTRA, "team_prebuilt", "team_bound_group"}
    return tuple(section for section in sections if section.name not in excluded)


def render_policy_sections(sections: "tuple[PromptSection, ...]", *, language: str) -> str:
    """Render policy sections into one prompt text, priority-ordered.

    The same assembly ``build_leader_policy_disclosure`` uses: a bare
    ``SystemPromptBuilder`` over the given sections, ``\\n\\n``-joined.
    """
    builder = SystemPromptBuilder(language=language)
    for section in sections:
        builder.add_section(section)
    return builder.build()


__all__ = [
    "build_default_policy_sections",
]

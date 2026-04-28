# coding: utf-8
"""Role-aware prompt and policy helpers.

The system prompt is the primary driver of team behavior —
the CoordinatorLoop only wakes the DeepAgent and injects
unread messages; all decision logic comes from these prompts.

Policy text lives in external Markdown templates under
``prompts/{cn,en}/`` so that prompt authors can edit them
without touching Python source.
"""

from __future__ import annotations

from typing import Any

from openjiuwen.agent_teams.agent.prompts import (
    load_shared_template,
    load_template,
)
from openjiuwen.agent_teams.schema.team import TeamRole

_I18N_LABELS: dict[str, dict[str, str]] = {
    "cn": {
        "persona": "当前人设",
        "member_name_label": "你的成员名（member_name）",
        "team_info_heading": "团队信息",
        "team_name_label": "团队名（team_name）",
        "display_name_label": "显示名（display_name）",
        "team_desc": "团队目标与指令",
        "relationships_heading": "成员关系",
    },
    "en": {
        "persona": "Current Persona",
        "member_name_label": "Your member_name",
        "team_info_heading": "Team Info",
        "team_name_label": "team_name",
        "display_name_label": "display_name",
        "team_desc": "Team Goal & Directives",
        "relationships_heading": "Relationships",
    },
}

_WORKFLOW_TEMPLATES: dict[str, str] = {
    "default": "leader_workflow",
    "predefined": "leader_workflow_predefined",
    "hybrid": "leader_workflow_hybrid",
}


def role_policy(role: TeamRole, language: str = "cn") -> str:
    """Return the base policy string for a role."""
    name = "leader_policy" if role == TeamRole.LEADER else "teammate_policy"
    return load_template(name, language).content


def _format_team_info(team_info: dict[str, Any], labels: dict[str, str]) -> str:
    """Format team information from database TeamInfo into a prompt section."""
    lines = [f"\n## {labels['team_info_heading']}"]
    team_name = team_info.get("team_name")
    if team_name:
        lines.append(f"- {labels['team_name_label']}: {team_name}")
    display_name = team_info.get("display_name")
    if display_name:
        lines.append(f"- {labels['display_name_label']}: {display_name}")
    desc = team_info.get("desc")
    if desc:
        lines.append(f"- {labels['team_desc']}: {desc}")
    return "\n".join(lines)


def _format_team_members(
    team_members: list[dict[str, str]],
    labels: dict[str, str],
    self_member_name: str | None = None,
) -> str:
    """Format team member list into a Relationships prompt section.

    Args:
        team_members: List of member dicts with member_name, display_name, desc.
        labels: I18n label dict for the current language.
        self_member_name: If provided, exclude this member from the list.
    """
    lines = [f"\n## {labels['relationships_heading']}"]
    for m in team_members:
        member_name = m.get("member_name", "")
        if member_name == self_member_name:
            continue
        display_name = m.get("display_name", "unknown")
        desc = m.get("desc", "")
        line = f"- member_name={member_name} display_name={display_name}"
        if desc:
            line += f" :: {desc}"
        lines.append(line)
    return "\n".join(lines)


def _build_team_policy(
    *,
    role: TeamRole,
    persona: str,
    base_prompt: str | None = None,
    team_info: dict[str, Any] | None = None,
    team_members: list[dict[str, str]] | None = None,
    member_name: str | None = None,
    lifecycle: str = "temporary",
    language: str = "cn",
    team_mode: str = "default",
) -> str:
    """Build the team-specific policy section (role, persona, team context)."""
    labels = _I18N_LABELS.get(language, _I18N_LABELS["cn"])

    policy_name = "leader_policy" if role == TeamRole.LEADER else "teammate_policy"
    role_policy_text = load_template(policy_name, language).content

    member_name_section = (
        f"{labels['member_name_label']}: {member_name}\n" if member_name else ""
    )

    workflow_section = ""
    if role == TeamRole.LEADER:
        workflow_name = _WORKFLOW_TEMPLATES.get(team_mode, "leader_workflow")
        workflow_section = load_template(workflow_name, language).content

    lifecycle_section = ""
    if role == TeamRole.LEADER:
        lc_name = "lifecycle_persistent" if lifecycle == "persistent" else "lifecycle_temporary"
        lifecycle_section = load_template(lc_name, language).content

    team_info_section = _format_team_info(team_info, labels) if team_info else ""
    team_members_section = (
        _format_team_members(team_members, labels, self_member_name=member_name)
        if team_members
        else ""
    )
    base_prompt_section = f"\n{base_prompt}" if base_prompt else ""

    template = load_shared_template("system_prompt")
    return template.format({
        "member_name_section": member_name_section,
        "role_policy": role_policy_text,
        "workflow_section": workflow_section,
        "lifecycle_section": lifecycle_section,
        "persona_label": labels["persona"],
        "persona": persona,
        "team_info_section": team_info_section,
        "team_members_section": team_members_section,
        "base_prompt_section": base_prompt_section,
    }).content


def build_system_prompt(
    *,
    role: TeamRole,
    persona: str,
    base_prompt: str | None = None,
    team_info: dict[str, Any] | None = None,
    team_members: list[dict[str, str]] | None = None,
    member_name: str | None = None,
    lifecycle: str = "temporary",
    language: str = "cn",
    team_mode: str = "default",
) -> str:
    """Compose the system prompt for one team role.

    The result is passed as ``system_prompt`` to ``DeepAgentSpec.build()``,
    which places it in the IDENTITY section of the ``SystemPromptBuilder``.
    Rails (SysOperationRail, ContextEngineeringRail, etc.) then append
    their own sections (tools, safety, runtime) at invoke time.

    Final prompt structure at runtime::

        [IDENTITY]  team policy + persona + team context  ← this function
        [TOOLS]     tool descriptions                     ← SysOperationRail etc.
        [SAFETY]    security rules                        ← SecurityRail
        [RUNTIME]   execution constraints                 ← auto
        ...         other rail-injected sections

    Args:
        role: LEADER or TEAMMATE.
        persona: Character persona description.
        base_prompt: Optional extra instructions appended at the end.
        team_info: Optional team metadata dict.
        team_members: Optional list of member dicts.
        member_name: Current member's unique name; used to exclude self
            from the team members section.
        lifecycle: Team lifecycle mode ("temporary" or "persistent").
        language: Prompt language ("cn" or "en").
        team_mode: Workflow variant — "default", "predefined", or "hybrid".
    """
    return _build_team_policy(
        role=role,
        persona=persona,
        base_prompt=base_prompt,
        team_info=team_info,
        team_members=team_members,
        member_name=member_name,
        lifecycle=lifecycle,
        language=language,
        team_mode=team_mode,
    )


__all__ = [
    "build_system_prompt",
    "role_policy",
]

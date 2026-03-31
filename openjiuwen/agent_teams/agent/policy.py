# coding: utf-8
"""Role-aware prompt and policy helpers.

The system prompt is the primary driver of team behavior —
the CoordinationLoop only wakes the DeepAgent and injects
unread messages; all decision logic comes from these prompts.

Policy text lives in external Markdown templates under
``prompts/{cn,en}/`` so that prompt authors can edit them
without touching Python source.
"""

from __future__ import annotations

from typing import Any

from openjiuwen.agent_teams.agent.prompts import load_shared_template, load_template
from openjiuwen.agent_teams.schema.team import TeamRole

_I18N_LABELS: dict[str, dict[str, str]] = {
    "cn": {
        "persona": "当前人设",
        "domain": "当前专业领域",
        "member_id": "你的成员ID",
        "team_info_heading": "团队信息",
        "team_name": "团队名称",
        "team_desc": "团队描述",
        "team_prompt": "团队指令",
        "relationships_heading": "成员关系",
    },
    "en": {
        "persona": "Current Persona",
        "domain": "Current Domain",
        "member_id": "Your Member ID",
        "team_info_heading": "Team Info",
        "team_name": "Team Name",
        "team_desc": "Team Description",
        "team_prompt": "Team Directive",
        "relationships_heading": "Relationships",
    },
}


def role_policy(role: TeamRole, language: str = "cn") -> str:
    """Return the base policy string for a role."""
    name = "leader_policy" if role == TeamRole.LEADER else "teammate_policy"
    return load_template(name, language).content


def leader_tool_guide(language: str = "cn") -> str:
    """Return the leader tool catalog."""
    return load_template("leader_tool_guide", language).content


def teammate_tool_guide(language: str = "cn") -> str:
    """Return the teammate tool catalog."""
    return load_template("teammate_tool_guide", language).content


def _format_team_info(team_info: dict[str, Any], labels: dict[str, str]) -> str:
    """Format team information from database TeamInfo into a prompt section."""
    lines = [f"\n## {labels['team_info_heading']}"]
    name = team_info.get("name")
    if name:
        lines.append(f"- {labels['team_name']}: {name}")
    desc = team_info.get("desc")
    if desc:
        lines.append(f"- {labels['team_desc']}: {desc}")
    prompt = team_info.get("prompt")
    if prompt:
        lines.append(f"- {labels['team_prompt']}: {prompt}")
    return "\n".join(lines)


def _format_team_members(
    team_members: list[dict[str, str]],
    labels: dict[str, str],
    self_member_id: str | None = None,
) -> str:
    """Format team member list into a Relationships prompt section.

    Args:
        team_members: List of member dicts with name, member_id, desc.
        labels: I18n label dict for the current language.
        self_member_id: If provided, exclude this member from the list.
    """
    lines = [f"\n## {labels['relationships_heading']}"]
    for m in team_members:
        member_id = m.get("member_id", "")
        if member_id == self_member_id:
            continue
        name = m.get("name", "unknown")
        desc = m.get("desc", "")
        line = f"- {name} (id: {member_id})"
        if desc:
            line += f": {desc}"
        lines.append(line)
    return "\n".join(lines)


def build_system_prompt(
    *,
    role: TeamRole,
    persona: str,
    domain: str,
    base_prompt: str | None = None,
    team_info: dict[str, Any] | None = None,
    team_members: list[dict[str, str]] | None = None,
    member_id: str | None = None,
    lifecycle: str = "temporary",
    language: str = "cn",
) -> str:
    """Compose the final system prompt for one team role.

    Args:
        role: LEADER or TEAMMATE.
        persona: Character persona description.
        domain: Professional domain.
        base_prompt: Optional extra instructions appended at the end.
        team_info: Optional team metadata dict.
        team_members: Optional list of member dicts.
        member_id: Current member's ID; used to exclude self from
            the team members section.
        lifecycle: Team lifecycle mode ("temporary" or "persistent").
        language: Prompt language ("cn" or "en").
    """
    labels = _I18N_LABELS.get(language, _I18N_LABELS["cn"])

    # Role-specific sub-templates
    policy_name = "leader_policy" if role == TeamRole.LEADER else "teammate_policy"
    tool_guide_name = "leader_tool_guide" if role == TeamRole.LEADER else "teammate_tool_guide"

    role_policy_text = load_template(policy_name, language).content
    tool_guide_text = load_template(tool_guide_name, language).content

    # Conditional sections
    member_id_section = f"{labels['member_id']}: {member_id}\n" if member_id else ""

    lifecycle_section = ""
    if role == TeamRole.LEADER:
        lc_name = "lifecycle_persistent" if lifecycle == "persistent" else "lifecycle_temporary"
        lifecycle_section = load_template(lc_name, language).content

    team_info_section = _format_team_info(team_info, labels) if team_info else ""
    team_members_section = _format_team_members(team_members, labels, self_member_id=member_id) if team_members else ""
    base_prompt_section = f"\n{base_prompt}" if base_prompt else ""

    # Assemble via the shared layout template
    template = load_shared_template("system_prompt")
    return template.format({
        "member_id_section": member_id_section,
        "role_policy": role_policy_text,
        "tool_guide": tool_guide_text,
        "lifecycle_section": lifecycle_section,
        "persona_label": labels["persona"],
        "persona": persona,
        "domain_label": labels["domain"],
        "domain": domain,
        "team_info_section": team_info_section,
        "team_members_section": team_members_section,
        "base_prompt_section": base_prompt_section,
    }).content


__all__ = [
    "build_system_prompt",
    "leader_tool_guide",
    "role_policy",
    "teammate_tool_guide",
]

# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.


"""PromptSection builders for the team policy rail.

Each function produces a single ``PromptSection`` covering one slice of
team-specific content (role, workflow, lifecycle, private prompt, ...). The
rail composes these sections into the shared ``SystemPromptBuilder``
alongside the harness sections (safety, tools, memory, ...).

Section layout (aligned with ``prompt_design.md``):

  P:11  team_role        — member id + role policy (always)
  P:12  team_hitt        — HITT collaboration rules. LEADER + HUMAN_AGENT
                          always get the full roster section (when human
                          members exist). TEAMMATE gets a role-neutral
                          anonymous section by default — no human_agent
                          ``member_name`` listed and no "real humans"
                          label — so peer role is not leaked into other
                          members' prompts. Setting
                          ``TeamAgentSpec.expose_human_agents_to_teammates=
                          True`` switches teammates to the legacy roster
                          section.
  P:13  team_workflow    — leader workflow (LEADER only)
  P:14  team_lifecycle   — team lifecycle policy (LEADER only)
  P:16  team_private_prompt — member-private working agreement (when set)
  P:17  team_extra       — user-supplied base prompt (when set)
  P:65  team_info        — team metadata (after capabilities)
  P:66  team_members     — relationships with peers
"""

from __future__ import annotations

from typing import Any, Optional

from openjiuwen.agent_teams.prompts.loader import load_template
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.core.single_agent.prompts.builder import PromptSection, SystemPromptBuilder

# ---------------------------------------------------------------------------
# Section name constants
# ---------------------------------------------------------------------------


class TeamSectionName:
    """Centralized section names owned by ``TeamPolicyRail``."""

    ROLE = "team_role"
    HITT = "team_hitt"
    HITT_ROSTER = "team_hitt_roster"
    BRIDGE = "team_bridge"
    WORKFLOW = "team_workflow"
    LIFECYCLE = "team_lifecycle"
    PRIVATE_PROMPT = "team_private_prompt"
    EXTRA = "team_extra"
    ATTACHMENT_NOTICE = "team_attachment_notice"
    INBOUND_TAGS = "team_inbound_tags"
    INFO = "team_info"
    MEMBERS = "team_members"


# ---------------------------------------------------------------------------
# Bilingual labels
# ---------------------------------------------------------------------------

_LABELS: dict[str, dict[str, str]] = {
    "cn": {
        "member_name_line": "你的 member_name",
        "role_heading": "# 团队角色",
        "workflow_heading": "# 工作流程",
        "lifecycle_heading": "# 团队生命周期",
        "private_prompt_heading": "# 私有工作约定",
        "info_heading": "# 团队信息",
        "team_name_label": "team_name（团队唯一标识）",
        "display_name_label": "display_name（团队展示名）",
        "team_desc": "团队目标与指令",
        "team_workspace": "团队共享工作空间",
        "team_workspace_purpose": (
            "用于存放团队共享文件（方案、设计、交付成果），"
            "所有成员通过该路径前缀读写同一份文件，系统自动管理版本和文件锁"
        ),
        "team_workspace_abs": "绝对路径",
        "members_heading": "# 成员关系",
        "leader_mode_plan": (
            "团队成员执行模式: plan_mode（成员选择或接到任务后需直接通过 submit_plan 提交计划，"
            "由你通过 approve_plan 审批后才能执行）"
        ),
        "leader_mode_build": ("团队成员执行模式: build_mode（成员领取任务后自主执行并直接完成，无需你审批计划）"),
        "teammate_mode_plan": (
            "你的执行模式: plan_mode（选择或接到任务后必须先通过 submit_plan 提交计划，"
            "该工具会认领任务；"
            "等待 leader 通过 approve_plan 审批后才能开始执行）"
        ),
        "teammate_mode_build": ("你的执行模式: build_mode（领取任务后可自主执行并直接标记完成，无需 leader 审批计划）"),
    },
    "en": {
        "member_name_line": "Your member_name",
        "role_heading": "# Team Role",
        "workflow_heading": "# Workflow",
        "lifecycle_heading": "# Team Lifecycle",
        "private_prompt_heading": "# Private Working Agreement",
        "info_heading": "# Team Info",
        "team_name_label": "team_name (unique identifier)",
        "display_name_label": "display_name (human-readable label)",
        "team_desc": "Team Goal & Directives",
        "team_workspace": "Team Shared Workspace",
        "team_workspace_purpose": (
            "Holds team-shared files (plans, designs, deliverables); "
            "all members read/write the same files through this path prefix. "
            "Versioning and file locks are managed automatically"
        ),
        "team_workspace_abs": "Absolute path",
        "members_heading": "# Relationships",
        "leader_mode_plan": (
            "Teammate execution mode: plan_mode (teammates must submit a plan "
            "with submit_plan after selecting or receiving a task; "
            "that tool reserves the task, then teammates wait for your exact plan_id approval via approve_plan "
            "before executing)"
        ),
        "leader_mode_build": (
            "Teammate execution mode: build_mode (teammates execute and "
            "complete tasks autonomously without plan approval)"
        ),
        "teammate_mode_plan": (
            "Your execution mode: plan_mode (after selecting or receiving a task you must "
            "submit a plan via submit_plan; that tool reserves the task. Wait for the leader to approve "
            "that plan_id via approve_plan before executing)"
        ),
        "teammate_mode_build": (
            "Your execution mode: build_mode (after claiming a task you "
            "execute autonomously and mark it completed without leader plan "
            "approval)"
        ),
    },
}


def _labels_for(language: str) -> dict[str, str]:
    return _LABELS.get(language, _LABELS["cn"])


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------


def build_team_role_section(
    *,
    role: TeamRole,
    member_name: str | None,
    teammate_mode: str = "build_mode",
    language: str = "cn",
) -> PromptSection:
    """Build the role + member name section.

    Args:
        role: LEADER or TEAMMATE.
        member_name: Optional member identifier (semantic slug).
        teammate_mode: Execution mode applied to teammates in this team
            (``"plan_mode"`` or ``"build_mode"``). For LEADER, rendered
            as a description of how teammates execute; for TEAMMATE,
            rendered as the member's own execution mode.
        language: Prompt language ('cn' or 'en').

    Returns:
        PromptSection containing role policy text under a single H1
        heading, with the member name appended as a leading line when set.
    """
    labels = _labels_for(language)
    policy_name = "leader_policy" if role == TeamRole.LEADER else "teammate_policy"
    role_text = load_template(policy_name, language).content.strip()

    member_line = f"{labels['member_name_line']}: {member_name}\n\n" if member_name else ""
    is_plan_mode = teammate_mode == "plan_mode"
    if role == TeamRole.LEADER:
        mode_label_key = "leader_mode_plan" if is_plan_mode else "leader_mode_build"
    else:
        mode_label_key = "teammate_mode_plan" if is_plan_mode else "teammate_mode_build"
    mode_line = f"{labels[mode_label_key]}\n\n"
    body = f"{labels['role_heading']}\n\n{member_line}{mode_line}{role_text}\n"
    return PromptSection(
        name=TeamSectionName.ROLE,
        content={language: body},
        priority=11,
    )


_WORKFLOW_TEMPLATES: dict[str, str] = {
    "default": "leader_workflow",
    "predefined": "leader_workflow_predefined",
    "hybrid": "leader_workflow_hybrid",
}


def build_team_workflow_section(
    *,
    role: TeamRole,
    team_mode: str = "default",
    language: str = "cn",
) -> Optional[PromptSection]:
    """Build the workflow section (LEADER only).

    Args:
        role: LEADER or TEAMMATE.
        team_mode: Workflow variant — "default", "predefined", or "hybrid".
        language: Prompt language.

    Returns:
        PromptSection wrapping the matching ``leader_workflow_*.md``
        under an H1 heading; ``None`` for non-leader roles.
    """
    if role != TeamRole.LEADER:
        return None
    labels = _labels_for(language)
    template_name = _WORKFLOW_TEMPLATES.get(team_mode, "leader_workflow")
    workflow_text = load_template(template_name, language).content.strip()
    body = f"{labels['workflow_heading']}\n\n{workflow_text}\n"
    return PromptSection(
        name=TeamSectionName.WORKFLOW,
        content={language: body},
        priority=13,
    )


def build_team_lifecycle_section(
    *,
    role: TeamRole,
    lifecycle: str,
    language: str = "cn",
) -> Optional[PromptSection]:
    """Build the team lifecycle section (LEADER only).

    Args:
        role: LEADER or TEAMMATE.
        lifecycle: ``"persistent"`` or ``"temporary"``.
        language: Prompt language.

    Returns:
        PromptSection containing the lifecycle template; ``None`` for
        non-leader roles.
    """
    if role != TeamRole.LEADER:
        return None
    labels = _labels_for(language)
    template_name = "lifecycle_persistent" if lifecycle == "persistent" else "lifecycle_temporary"
    lifecycle_text = load_template(template_name, language).content.strip()
    body = f"{labels['lifecycle_heading']}\n\n{lifecycle_text}\n"
    return PromptSection(
        name=TeamSectionName.LIFECYCLE,
        content={language: body},
        priority=14,
    )


def build_team_private_prompt_section(
    *,
    member_prompt: str | None,
    language: str = "cn",
) -> Optional[PromptSection]:
    """Build the member-private working-agreement section.

    The member-private counterpart to the public ``desc``: injected solely into
    this member's own system prompt and never shared into any peer's roster
    or ``list_members`` output. Built once as a static section (fixed at
    spawn time) so the system-prompt prefix stays KV-cache stable. Empty text
    (e.g. the leader, or a member spawned without a private prompt) drops the
    section entirely.

    Returns:
        PromptSection with the private prompt body, or ``None`` when no
        private prompt is set.
    """
    if not member_prompt or not member_prompt.strip():
        return None
    labels = _labels_for(language)
    body = f"{labels['private_prompt_heading']}\n\n{member_prompt.strip()}\n"
    return PromptSection(
        name=TeamSectionName.PRIVATE_PROMPT,
        content={language: body},
        priority=16,
    )


def build_team_extra_section(
    *,
    base_prompt: str | None,
    language: str = "cn",
) -> Optional[PromptSection]:
    """Build the user-supplied extra instructions section.

    No header is added so the user's text reads like a continuation of
    the policy stack.

    Returns:
        PromptSection with the base prompt body, or ``None`` when empty.
    """
    if not base_prompt or not base_prompt.strip():
        return None
    return PromptSection(
        name=TeamSectionName.EXTRA,
        content={language: f"{base_prompt.strip()}\n"},
        priority=17,
    )


def build_team_attachment_notice_section(*, language: str = "cn") -> PromptSection:
    """Build the static notice explaining team-state prompt attachments (§5.1).

    Tells the LLM that roster / team-info / HITT state is delivered as
    ``<prompt-attachment>`` blocks at the message tail (rather than in the
    system prompt) and reflects the current round's latest state. The bilingual
    body lives in ``<lang>/attachment_notice.md``.

    Args:
        language: Prompt language ('cn' or 'en').

    Returns:
        PromptSection with the bilingual attachment-notice body.
    """
    del language  # content carries both languages; selection happens at render
    content = {
        "cn": load_template("attachment_notice", "cn").content,
        "en": load_template("attachment_notice", "en").content,
    }
    return PromptSection(
        name=TeamSectionName.ATTACHMENT_NOTICE,
        content=content,
        priority=17,
    )


def build_team_inbound_tags_section(*, language: str = "cn") -> PromptSection:
    """Build the static notice explaining inbound message XML tags (§5.2).

    Explains the ``<team-inbound>`` / ``<team-note>`` / ``<team-event>`` tag
    system and the ``for="controller"`` marker, so the LLM reads inbound
    messages and framework events with clear boundaries. The bilingual body
    lives in ``<lang>/inbound_tags.md``.

    Args:
        language: Prompt language ('cn' or 'en').

    Returns:
        PromptSection with the bilingual inbound-tags body.
    """
    del language  # content carries both languages; selection happens at render
    content = {
        "cn": load_template("inbound_tags", "cn").content,
        "en": load_template("inbound_tags", "en").content,
    }
    return PromptSection(
        name=TeamSectionName.INBOUND_TAGS,
        content=content,
        priority=18,
    )


def build_team_info_section(
    *,
    team_info: dict[str, Any] | None,
    team_workspace_mount: str | None = None,
    team_workspace_path: str | None = None,
    language: str = "cn",
) -> Optional[PromptSection]:
    """Build the team metadata section.

    Args:
        team_info: Mapping with optional ``team_name``, ``display_name``
            and ``desc`` keys (the shape returned by
            ``TeamBackend.get_team_info``).
        team_workspace_mount: Agent-relative mount point of the team
            shared workspace (e.g. ``.team/{team_name}/``).  When set,
            the section appends a bullet telling the LLM how to
            read/write team-shared files from its own workspace.
        team_workspace_path: Absolute path of the team shared
            workspace on disk.  Purely informational; appended as a
            nested bullet when ``team_workspace_mount`` is provided.
        language: Prompt language.

    Returns:
        PromptSection listing team_name, display_name, goal and (when
        configured) the shared workspace mount, or ``None`` when no
        usable fields are present.
    """
    labels = _labels_for(language)
    team_name = team_info.get("team_name") if team_info else None
    display_name = team_info.get("display_name") if team_info else None
    desc = team_info.get("desc") if team_info else None
    mount = team_workspace_mount.strip() if team_workspace_mount else ""
    if not any([team_name, display_name, desc, mount]):
        return None

    lines = [labels["info_heading"], ""]
    if team_name:
        lines.append(f"- {labels['team_name_label']}: {team_name}")
    if display_name:
        lines.append(f"- {labels['display_name_label']}: {display_name}")
    if desc:
        lines.append(f"- {labels['team_desc']}: {desc}")
    if mount:
        lines.append(f"- {labels['team_workspace']}: `{mount}`")
        lines.append(f"  - {labels['team_workspace_purpose']}")
        if team_workspace_path:
            lines.append(f"  - {labels['team_workspace_abs']}: `{team_workspace_path}`")
    body = "\n".join(lines) + "\n"
    return PromptSection(
        name=TeamSectionName.INFO,
        content={language: body},
        priority=65,
    )


def _format_human_agent_roster(names: list[str], language: str) -> str:
    """Render the list of human-agent member names for inline prompts."""
    quoted = ", ".join(f"`{n}`" for n in names)
    if language == "cn":
        return f"注册的人类成员：{quoted}"
    return f"Registered human members: {quoted}"


def _self_member_line(self_name: str | None, language: str) -> str:
    """Render the 'your member_name is X' line, or empty when unset."""
    if not self_name:
        return ""
    if language == "cn":
        return f"你的 member_name 是 `{self_name}`。\n"
    return f"Your member_name is `{self_name}`.\n"


def _hitt_template_name(role: TeamRole, expose_human_agents_to_teammates: bool) -> str | None:
    """Pick the HITT contract template for a role.

    TEAMMATE defaults to the role-neutral anonymous template; the
    ``expose_human_agents_to_teammates`` flag switches it to the legacy
    roster-exposing variant. Returns ``None`` for roles without a HITT section.
    """
    if role == TeamRole.LEADER:
        return "hitt_leader"
    if role == TeamRole.TEAMMATE:
        return "hitt_teammate" if expose_human_agents_to_teammates else "hitt_teammate_anonymous"
    if role == TeamRole.HUMAN_AGENT:
        return "hitt_human_agent"
    return None


def _hitt_roster_visible(role: TeamRole, expose_human_agents_to_teammates: bool) -> bool:
    """Whether this role lists human-member names.

    The anonymous teammate variant never names anyone, so it carries no roster
    block; every other HITT role does.
    """
    if role == TeamRole.TEAMMATE:
        return expose_human_agents_to_teammates
    return role in (TeamRole.LEADER, TeamRole.HUMAN_AGENT)


def _hitt_contract_body(
    role: TeamRole,
    self_member_name: str | None,
    expose_human_agents_to_teammates: bool,
    language: str,
) -> str | None:
    """Render the static HITT collaboration-contract markdown (rules only)."""
    template_name = _hitt_template_name(role, expose_human_agents_to_teammates)
    if template_name is None:
        return None
    peers = _self_member_line(self_member_name, language)
    return load_template(template_name, language).format({"peers": peers}).content


def _hitt_roster_body(
    role: TeamRole,
    human_agent_names: "list[str] | frozenset[str] | set[str]",
    expose_human_agents_to_teammates: bool,
    language: str,
) -> str | None:
    """Render the dynamic human-member roster markdown (names only)."""
    if not _hitt_roster_visible(role, expose_human_agents_to_teammates):
        return None
    return _format_human_agent_roster(sorted(human_agent_names), language)


def build_team_hitt_contract_section(
    *,
    role: TeamRole,
    human_agent_names: "list[str] | frozenset[str] | set[str] | None" = None,
    language: str = "cn",
    self_member_name: str | None = None,
    expose_human_agents_to_teammates: bool = False,
) -> Optional[PromptSection]:
    """Build the static HITT collaboration-contract section (rules only).

    The human-member roster is delivered separately (see
    :func:`build_team_hitt_roster_section`) so this contract stays byte-stable
    in the system-prompt prefix. Returns ``None`` when no human agent is
    registered or the role has no HITT section.

    Args:
        role: The role whose prompt this section targets.
        human_agent_names: Registered human-agent member names. Empty/None
            means no human members → no section.
        language: "cn" or "en".
        self_member_name: The current member's own name, injected into the
            human-agent contract so the avatar knows which entry is itself.
        expose_human_agents_to_teammates: TEAMMATE-only switch between the
            anonymous (default) and roster-exposing contract templates.

    Returns:
        The contract PromptSection, or ``None``.
    """
    if not human_agent_names:
        return None
    body = _hitt_contract_body(role, self_member_name, expose_human_agents_to_teammates, language)
    if body is None:
        return None
    return PromptSection(
        name=TeamSectionName.HITT,
        content={language: body},
        priority=12,
    )


def build_team_hitt_roster_section(
    *,
    role: TeamRole,
    human_agent_names: "list[str] | frozenset[str] | set[str] | None" = None,
    language: str = "cn",
    expose_human_agents_to_teammates: bool = False,
) -> Optional[PromptSection]:
    """Build the dynamic human-member roster section (names only).

    Delivered as a per-round attachment so roster churn never invalidates the
    system-prompt prefix. Returns ``None`` when no human agent is registered,
    or for the anonymous teammate variant (which never lists names).

    Args:
        role: The role whose prompt this section targets.
        human_agent_names: Registered human-agent member names. Empty/None
            means no human members → no section.
        language: "cn" or "en".
        expose_human_agents_to_teammates: TEAMMATE-only switch; False keeps the
            teammate roster hidden (returns ``None``).

    Returns:
        The roster PromptSection, or ``None``.
    """
    if not human_agent_names:
        return None
    body = _hitt_roster_body(role, human_agent_names, expose_human_agents_to_teammates, language)
    if body is None:
        return None
    return PromptSection(
        name=TeamSectionName.HITT_ROSTER,
        content={language: body},
        priority=12,
    )


def build_team_hitt_section(
    *,
    role: TeamRole,
    human_agent_names: "list[str] | frozenset[str] | set[str] | None" = None,
    language: str = "cn",
    self_member_name: str | None = None,
    expose_human_agents_to_teammates: bool = False,
) -> Optional[PromptSection]:
    """Build the combined HITT section (contract + roster in one block).

    Convenience entry for one-shot snapshots (external CLI members) and tests,
    where there is no attachment channel: concatenates the static contract and
    the human-member roster into a single section. In-process members instead
    split these across the system prompt (contract) and a per-round attachment
    (roster) via :func:`build_team_hitt_contract_section` /
    :func:`build_team_hitt_roster_section`.

    Args:
        role: The role whose prompt this section targets.
        human_agent_names: Member names of every registered human agent.
            Empty/None means no human members → no section.
        language: "cn" or "en".
        self_member_name: The current member's own name, used to tell a
            human-agent reader which entry in the roster is itself.
        expose_human_agents_to_teammates: Only affects the TEAMMATE branch.
            False (default) → anonymous variant. True → legacy roster-exposing
            variant.
    """
    if not human_agent_names:
        return None
    contract_body = _hitt_contract_body(role, self_member_name, expose_human_agents_to_teammates, language)
    if contract_body is None:
        return None
    roster_body = _hitt_roster_body(role, human_agent_names, expose_human_agents_to_teammates, language)
    body = contract_body if roster_body is None else f"{contract_body.rstrip()}\n\n{roster_body}"
    return PromptSection(
        name=TeamSectionName.HITT,
        content={language: body},
        priority=12,
    )


def _format_bridge_agent_roster(names: list[str], language: str) -> str:
    """Render the list of bridge-agent member names for inline prompts."""
    quoted = ", ".join(f"`{n}`" for n in names)
    if language == "cn":
        return f"注册的桥接成员：{quoted}"
    return f"Registered bridge members: {quoted}"


def _bridge_template_name(role: TeamRole) -> str | None:
    """Pick the Bridge template for a role, or ``None`` when not applicable."""
    if role == TeamRole.LEADER:
        return "bridge_leader"
    if role == TeamRole.TEAMMATE:
        return "bridge_teammate"
    if role == TeamRole.BRIDGE_AGENT:
        return "bridge_agent"
    return None


def build_team_bridge_section(
    *,
    role: TeamRole,
    bridge_agent_names: "list[str] | frozenset[str] | set[str] | None" = None,
    language: str = "cn",
    self_member_name: str | None = None,
) -> Optional[PromptSection]:
    """Build the Bridge Agent collaboration-rules section.

    Returns a non-None section only when at least one bridge-agent member is
    registered. Text is role-specific (``bridge_leader`` / ``bridge_teammate``
    / ``bridge_agent``) with the roster of bridge member names injected inline,
    so the leader / other teammates see whom to address through
    ``send_message``, and the bridge avatar itself sees the scheduling
    contract.

    Args:
        role: The role whose prompt this section targets.
        bridge_agent_names: Member names of every registered bridge agent.
            Empty/None means no bridges → no section.
        language: ``"cn"`` or ``"en"``.
        self_member_name: The current member's own name, used to tell a
            bridge-agent reader which entry in the roster is itself.
    """
    if not bridge_agent_names:
        return None
    template_name = _bridge_template_name(role)
    if template_name is None:
        return None
    roster = _format_bridge_agent_roster(sorted(bridge_agent_names), language)
    peers = _self_member_line(self_member_name, language)
    body = load_template(template_name, language).format({"roster": roster, "peers": peers}).content
    return PromptSection(
        name=TeamSectionName.BRIDGE,
        content={language: body},
        priority=12,
    )


def build_team_members_section(
    *,
    team_members: list[dict[str, str]] | None,
    self_member_name: str | None,
    language: str = "cn",
) -> Optional[PromptSection]:
    """Build the team relationships section.

    Args:
        team_members: List of member dicts with ``member_name``,
            ``display_name`` and optional ``desc``.
        self_member_name: Excluded from the listing if present.
        language: Prompt language.

    Returns:
        PromptSection listing peer members, or ``None`` when the list
        is empty after self exclusion.
    """
    if not team_members:
        return None
    labels = _labels_for(language)
    rows = []
    for member in team_members:
        member_name = member.get("member_name", "")
        if member_name == self_member_name:
            continue
        display_name = member.get("display_name", "unknown")
        desc = member.get("desc", "")
        line = f"- member_name={member_name} display_name={display_name}"
        if desc:
            line += f" :: {desc}"
        rows.append(line)
    if not rows:
        return None
    body = labels["members_heading"] + "\n\n" + "\n".join(rows) + "\n"
    return PromptSection(
        name=TeamSectionName.MEMBERS,
        content={language: body},
        priority=66,
    )


def build_team_static_sections(
    *,
    role: TeamRole,
    member_name: str | None,
    member_prompt: str = "",
    lifecycle: str = "temporary",
    teammate_mode: str = "build_mode",
    team_mode: str = "default",
    base_prompt: str | None = None,
    language: str = "cn",
    human_agent_names: list[str] | None = None,
    expose_human_agents_to_teammates: bool = False,
    bridge_agent_names: list[str] | None = None,
) -> list[PromptSection]:
    """Build the never-changing team sections for one member.

    Single source of truth for one-shot static team sections. In-process
    DeepAgent members call this through :class:`TeamPolicyRail` for role /
    bridge / workflow / lifecycle / private-prompt / extra; HITT is refreshed
    by the rail dynamically instead of being passed here. External CLI members
    use this function to build a standalone prompt snapshot, so callers may
    still pass ``human_agent_names`` to include a static HITT section.

    Args:
        role: LEADER or TEAMMATE (other roles get the role-appropriate slices).
        member_name: Semantic member identifier.
        member_prompt: The member's private working agreement (DB ``prompt``),
            injected only into this member's own prompt (empty drops the
            section). The public ``desc`` is intentionally NOT rendered here —
            it belongs only in peers' roster section.
        lifecycle: Team lifecycle ("temporary" / "persistent").
        teammate_mode: Teammate execution mode ("build_mode" / "plan_mode").
        team_mode: Team mode ("default" / "predefined" / "hybrid").
        base_prompt: Optional user-supplied prompt appended as the extra section.
        language: Prompt language ("cn" / "en").
        human_agent_names: Optional registered human-agent member names used
            for one-shot HITT prompt snapshots, mainly external CLI members.
        expose_human_agents_to_teammates: Whether teammates see human agents
            in that one-shot HITT snapshot.
        bridge_agent_names: Registered bridge-agent member names (bridge section).

    Returns:
        The non-None sections, unsorted (the caller orders by priority).
    """
    builders = [
        build_team_role_section(
            role=role,
            member_name=member_name,
            teammate_mode=teammate_mode,
            language=language,
        ),
        build_team_hitt_contract_section(
            role=role,
            human_agent_names=human_agent_names,
            language=language,
            self_member_name=member_name,
            expose_human_agents_to_teammates=expose_human_agents_to_teammates,
        ),
        build_team_hitt_roster_section(
            role=role,
            human_agent_names=human_agent_names,
            language=language,
            expose_human_agents_to_teammates=expose_human_agents_to_teammates,
        ),
        build_team_bridge_section(
            role=role,
            bridge_agent_names=bridge_agent_names,
            language=language,
            self_member_name=member_name,
        ),
        build_team_workflow_section(
            role=role,
            team_mode=team_mode,
            language=language,
        ),
        build_team_lifecycle_section(
            role=role,
            lifecycle=lifecycle,
            language=language,
        ),
        build_team_private_prompt_section(
            member_prompt=member_prompt,
            language=language,
        ),
        build_team_extra_section(
            base_prompt=base_prompt,
            language=language,
        ),
    ]
    return [section for section in builders if section is not None]


def build_team_member_system_prompt(
    *,
    role: TeamRole,
    member_name: str | None,
    member_prompt: str = "",
    lifecycle: str = "temporary",
    teammate_mode: str = "build_mode",
    team_mode: str = "default",
    base_prompt: str | None = None,
    language: str = "cn",
    human_agent_names: list[str] | None = None,
    expose_human_agents_to_teammates: bool = False,
    bridge_agent_names: list[str] | None = None,
) -> str:
    """Render a member's team sections into a single standalone system prompt.

    Used to give an external CLI member (whose brain is not a local DeepAgent)
    the same team-rail sections an in-process member gets, assembled the same
    way (priority-ordered, ``\\n\\n``-joined). It includes ONLY the team
    sections — the harness / other DeepAgent rails do not apply to an external
    CLI, so their prompt contributions are intentionally excluded.

    Args mirror :func:`build_team_static_sections`.

    Returns:
        The rendered system prompt, or ``""`` when no section produced content.
    """
    sections = build_team_static_sections(
        role=role,
        member_name=member_name,
        member_prompt=member_prompt,
        lifecycle=lifecycle,
        teammate_mode=teammate_mode,
        team_mode=team_mode,
        base_prompt=base_prompt,
        language=language,
        human_agent_names=human_agent_names,
        expose_human_agents_to_teammates=expose_human_agents_to_teammates,
        bridge_agent_names=bridge_agent_names,
    )
    builder = SystemPromptBuilder(language=language)
    for section in sections:
        builder.add_section(section)
    return builder.build()


__all__ = [
    "TeamSectionName",
    "build_team_attachment_notice_section",
    "build_team_bridge_section",
    "build_team_extra_section",
    "build_team_hitt_contract_section",
    "build_team_hitt_roster_section",
    "build_team_hitt_section",
    "build_team_inbound_tags_section",
    "build_team_info_section",
    "build_team_lifecycle_section",
    "build_team_member_system_prompt",
    "build_team_members_section",
    "build_team_role_section",
    "build_team_static_sections",
    "build_team_workflow_section",
]

# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.


"""PromptSection builders for the team policy rail.

Each function produces a single ``PromptSection`` covering one slice of
team-specific content (identity, role, workflow, lifecycle, ...). The
rail composes these sections into the shared ``SystemPromptBuilder``
alongside the harness sections (safety, tools, memory, ...).

Section layout (aligned with ``prompt_design.md``):

  P:10  team_identity    — everything specific to this one member: its
                          member_name and its private working agreement.
                          The only per-member content, delivered as a
                          prompt attachment for in-process members (keeping
                          it out of the system prompt lets every member of
                          a team share one cached prefix); inlined into the
                          static prompt only for external CLI members,
                          whose prompt is a standalone snapshot.
  P:11  team_role        — role policy + execution mode (always)
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
  P:15  team_dispatch    — how tasks reach members: autonomous claim vs
                          scheduled assignment (LEADER + TEAMMATE)
  P:17  team_extra       — user-supplied base prompt (when set)

Team *state* (team metadata, peer roster) is not a section at all: it is
delivered into the member's conversation history as it appears, rendered by
``prompts/messages.py`` and driven by ``agent_teams/team_context.py``.
"""

from __future__ import annotations

from typing import Literal, Optional

from openjiuwen.agent_teams.prompts.loader import load_template
from openjiuwen.agent_teams.prompts.messages import build_identity_text
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.core.single_agent.prompts.builder import PromptSection, SystemPromptBuilder

# ---------------------------------------------------------------------------
# Section name constants
# ---------------------------------------------------------------------------


class TeamSectionName:
    """Centralized section names owned by ``TeamPolicyRail``."""

    IDENTITY = "team_identity"
    ROLE = "team_role"
    HITT = "team_hitt"
    BRIDGE = "team_bridge"
    WORKFLOW = "team_workflow"
    DISPATCH = "team_dispatch"
    LIFECYCLE = "team_lifecycle"
    EXTRA = "team_extra"
    INBOUND_TAGS = "team_inbound_tags"


# ---------------------------------------------------------------------------
# Bilingual labels
# ---------------------------------------------------------------------------

_LABELS: dict[str, dict[str, str]] = {
    "cn": {
        "role_heading": "# 团队角色",
        "workflow_heading": "# 工作流程",
        "dispatch_heading": "# 任务下发与获取",
        "lifecycle_heading": "# 团队生命周期",
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
        "role_heading": "# Team Role",
        "workflow_heading": "# Workflow",
        "dispatch_heading": "# Task Dispatch",
        "lifecycle_heading": "# Team Lifecycle",
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


def build_team_identity_section(
    *,
    member_name: str | None,
    display_name: str | None = None,
    member_workspace_path: str | None = None,
    member_prompt: str | None = None,
    language: str = "cn",
) -> Optional[PromptSection]:
    """Build the member's own-identity section (external CLI members only).

    Carries everything specific to this one member: its ``member_name`` and its
    private working agreement (the member-private counterpart to the public
    ``desc``, never shared into any peer's roster or ``list_members`` output).
    Both are fixed at spawn time, but they are the only content that differs
    *between* members of a team, so they must stay out of the shared system
    prompt for in-process members — those receive the same body as a history
    message (see ``prompts/messages.build_identity_text``).

    External CLI members are the exception this section exists for: their prompt
    is a standalone per-member snapshot rather than a prefix shared with sibling
    members, and at launch they have no conversation to write into. They inline
    it via ``build_team_static_sections(include_member_specific=True)``.

    Args:
        member_name: Semantic member identifier.
        display_name: Human-readable member label.
        member_workspace_path: The member's own artifact directory.
        member_prompt: The member's private working agreement; blank (a member
            spawned without one) drops that subsection.
        language: Prompt language ('cn' or 'en').

    Returns:
        PromptSection carrying the member's names and, when set, the private
        working agreement; ``None`` when none of them is set.
    """
    body = build_identity_text(
        member_name=member_name,
        display_name=display_name,
        member_workspace_path=member_workspace_path,
        member_prompt=member_prompt,
        language=language,
    )
    if body is None:
        return None
    return PromptSection(
        name=TeamSectionName.IDENTITY,
        content={language: body},
        priority=10,
    )


def build_team_role_section(
    *,
    role: TeamRole,
    teammate_mode: str = "build_mode",
    workspace_prompt_variant: Literal["native", "external"] = "native",
    language: str = "cn",
) -> PromptSection:
    """Build the role policy + execution mode section.

    The member's own ``member_name`` is deliberately NOT rendered here — it is
    the only per-member value and lives in the ``team_identity`` section, so
    this section stays byte-identical across every member sharing a role.

    Args:
        role: LEADER or TEAMMATE.
        teammate_mode: Execution mode applied to teammates in this team
            (``"plan_mode"`` or ``"build_mode"``). For LEADER, rendered
            as a description of how teammates execute; for TEAMMATE,
            rendered as the member's own execution mode.
        workspace_prompt_variant: Workspace wording variant. Native teammates
            receive the ``.team`` mount instructions; external CLI teammates
            receive path-based shared workspace instructions.
        language: Prompt language ('cn' or 'en').

    Returns:
        PromptSection containing role policy text under a single H1
        heading, led by the execution-mode line.
    """
    labels = _labels_for(language)
    if role == TeamRole.LEADER:
        policy_name = "leader_policy"
    elif workspace_prompt_variant == "external":
        policy_name = "teammate_policy_external"
    else:
        policy_name = "teammate_policy"
    role_text = load_template(policy_name, language).content.strip()

    is_plan_mode = teammate_mode == "plan_mode"
    if role == TeamRole.LEADER:
        mode_label_key = "leader_mode_plan" if is_plan_mode else "leader_mode_build"
    else:
        mode_label_key = "teammate_mode_plan" if is_plan_mode else "teammate_mode_build"
    mode_line = f"{labels[mode_label_key]}\n\n"
    body = f"{labels['role_heading']}\n\n{mode_line}{role_text}\n"
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


_DISPATCH_MODES: frozenset[str] = frozenset({"autonomous", "scheduled"})

# Only LEADER and TEAMMATE take part in task dispatch. HUMAN_AGENT already
# carries the "wait for assignment" contract in its HITT section, and
# BRIDGE_AGENT is a relay avatar that owns no board work.
_DISPATCH_ROLE_SLUGS: dict[TeamRole, str] = {
    TeamRole.LEADER: "leader",
    TeamRole.TEAMMATE: "teammate",
}


def build_team_dispatch_section(
    *,
    role: TeamRole,
    dispatch_mode: str = "autonomous",
    language: str = "cn",
) -> Optional[PromptSection]:
    """Build the task-dispatch section (LEADER + TEAMMATE).

    The dispatch mode is orthogonal to ``team_mode``: ``team_mode`` decides
    whether the roster can grow, this decides how a task reaches the member
    who executes it. Keeping them in separate sections avoids a template
    matrix (``3 x 2``) — each dimension contributes its own file.

    Args:
        role: Team role; roles outside ``_DISPATCH_ROLE_SLUGS`` get None.
        dispatch_mode: ``"autonomous"`` (members claim from the board) or
            ``"scheduled"`` (leader assigns, the scheduler starts members).
        language: Prompt language.

    Returns:
        PromptSection wrapping the matching ``dispatch_<mode>_<role>.md``
        under an H1 heading; ``None`` for roles that own no board work.
    """
    slug = _DISPATCH_ROLE_SLUGS.get(role)
    if slug is None:
        return None
    mode = dispatch_mode if dispatch_mode in _DISPATCH_MODES else "autonomous"
    labels = _labels_for(language)
    dispatch_text = load_template(f"dispatch_{mode}_{slug}", language).content.strip()
    body = f"{labels['dispatch_heading']}\n\n{dispatch_text}\n"
    return PromptSection(
        name=TeamSectionName.DISPATCH,
        content={language: body},
        priority=15,
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
    ``expose_human_agents_to_teammates`` flag switches it to the roster-aware
    variant. Returns ``None`` for roles without a HITT section.
    """
    if role == TeamRole.LEADER:
        return "hitt_leader"
    if role == TeamRole.TEAMMATE:
        return "hitt_teammate" if expose_human_agents_to_teammates else "hitt_teammate_anonymous"
    if role == TeamRole.HUMAN_AGENT:
        return "hitt_human_agent"
    return None


def _hitt_contract_body(
    role: TeamRole,
    self_member_name: str | None,
    expose_human_agents_to_teammates: bool,
    language: str,
) -> str | None:
    """Render the HITT collaboration-contract markdown (rules only).

    The human roster is NOT inlined here — human members appear in the unified
    ``team_members`` roster tagged ``[human]`` (gated by
    ``expose_human_agents_to_teammates``). Only the human-agent contract carries
    a ``{{self_line}}`` placeholder (the avatar's own member_name); the leader /
    teammate templates are plain text and skip the format step.
    """
    template_name = _hitt_template_name(role, expose_human_agents_to_teammates)
    if template_name is None:
        return None
    template = load_template(template_name, language)
    if role == TeamRole.HUMAN_AGENT:
        self_line = _self_member_line(self_member_name, language)
        return template.format({"self_line": self_line}).content
    return template.content


def build_team_hitt_section(
    *,
    role: TeamRole,
    hitt_enabled: bool = False,
    language: str = "cn",
    self_member_name: str | None = None,
    expose_human_agents_to_teammates: bool = False,
) -> Optional[PromptSection]:
    """Build the HITT collaboration-contract section (rules only).

    Present only when HITT is enabled for the team (``hitt_enabled``). The rules
    reference the ``[human]``-tagged entries in the unified ``team_members``
    roster rather than an inline human roster, so the section is static
    (byte-stable in the system-prompt prefix) — gated on the HITT capability
    flag rather than the live roster, so the rules are ready even before any
    human agent is spawned. Text is role-specific: LEADER gets assignment
    rules, TEAMMATE gets role-neutral collaboration habits (anonymous by
    default; ``expose_human_agents_to_teammates`` switches to the roster-aware
    variant), HUMAN_AGENT gets the avatar self-contract.

    Args:
        role: The role whose prompt this section targets.
        hitt_enabled: Whether HITT is enabled for the team. False → no section.
        language: "cn" or "en".
        self_member_name: The current member's own name, injected into the
            human-agent contract so the avatar knows which entry is itself.
        expose_human_agents_to_teammates: TEAMMATE-only switch between the
            anonymous (default) and roster-aware contract templates.

    Returns:
        The contract PromptSection, or ``None``.
    """
    if not hitt_enabled:
        return None
    body = _hitt_contract_body(role, self_member_name, expose_human_agents_to_teammates, language)
    if body is None:
        return None
    return PromptSection(
        name=TeamSectionName.HITT,
        content={language: body},
        priority=12,
    )


def build_team_bridge_section(
    *,
    role: TeamRole,
    language: str = "cn",
    self_member_name: str | None = None,
) -> Optional[PromptSection]:
    """Build the Bridge Agent self-contract section (BRIDGE_AGENT only).

    Bridge members are ordinary teammates from every other member's point of
    view — they appear untagged in the unified ``team_members`` roster and get
    no peer-facing section. Only the bridge avatar itself receives this
    scheduling self-contract (how to relay the remote executor's output). The
    ``{{self_line}}`` placeholder carries the avatar's own member_name.

    Args:
        role: The role whose prompt this section targets. Non-BRIDGE_AGENT
            roles get no section.
        language: ``"cn"`` or ``"en"``.
        self_member_name: The bridge avatar's own name.

    Returns:
        The bridge self-contract PromptSection, or ``None``.
    """
    if role != TeamRole.BRIDGE_AGENT:
        return None
    self_line = _self_member_line(self_member_name, language)
    body = load_template("bridge_agent", language).format({"self_line": self_line}).content
    return PromptSection(
        name=TeamSectionName.BRIDGE,
        content={language: body},
        priority=12,
    )


def build_team_static_sections(
    *,
    role: TeamRole,
    member_name: str | None,
    display_name: str = "",
    member_workspace_path: str | None = None,
    member_prompt: str = "",
    lifecycle: str = "temporary",
    teammate_mode: str = "build_mode",
    team_mode: str = "default",
    dispatch_mode: str = "autonomous",
    base_prompt: str | None = None,
    language: str = "cn",
    hitt_enabled: bool = False,
    expose_human_agents_to_teammates: bool = False,
    include_member_specific: bool = False,
    workspace_prompt_variant: Literal["native", "external"] = "native",
) -> list[PromptSection]:
    """Build the never-changing team sections for one member.

    Single source of truth for the static team sections. In-process DeepAgent
    members call this through :class:`TeamPolicyRail`; external CLI members call
    it directly to build a standalone prompt snapshot. Every section here is
    static — HITT is gated on ``hitt_enabled``, bridge on ``role ==
    BRIDGE_AGENT``. Team state (metadata, peer roster) is NOT built here: it is
    delivered into the member's conversation as it appears (see
    ``agent_teams/team_context.py``). The one per-member section
    (``team_identity``: member_name + private working agreement) is delivered
    the same way for in-process members, and only inlined here when
    ``include_member_specific`` is set.

    Args:
        role: LEADER or TEAMMATE (other roles get the role-appropriate slices).
        member_name: Semantic member identifier. Feeds the HITT / bridge
            self-contracts, and the identity section when
            ``include_member_specific`` is set.
        member_prompt: The member's private working agreement (DB ``prompt``),
            delivered only to this member as part of the identity section;
            rendered here only when ``include_member_specific`` is set. The
            public ``desc`` is intentionally NOT rendered here — it belongs
            only in peers' roster.
        lifecycle: Team lifecycle ("temporary" / "persistent").
        teammate_mode: Teammate execution mode ("build_mode" / "plan_mode").
        team_mode: Team mode ("default" / "predefined" / "hybrid").
        dispatch_mode: How tasks reach members ("autonomous" / "scheduled").
        base_prompt: Optional user-supplied prompt appended as the extra section.
        language: Prompt language ("cn" / "en").
        hitt_enabled: Whether HITT is enabled for the team; gates the static
            HITT collaboration contract.
        expose_human_agents_to_teammates: Whether teammates get the roster-aware
            HITT variant (and, via the caller, the ``[human]`` roster tag).
        include_member_specific: When True, inline the per-member section
            (``team_identity``) as a static section. Only external CLI members
            set this; in-process members receive it as a history message so the
            system-prompt prefix stays identical across the team.
        workspace_prompt_variant: Workspace wording variant forwarded to the
            teammate role policy section.

    Returns:
        The non-None sections, unsorted (the caller orders by priority).
    """
    identity_section = None
    if include_member_specific:
        identity_section = build_team_identity_section(
            member_name=member_name,
            display_name=display_name,
            member_workspace_path=member_workspace_path,
            member_prompt=member_prompt,
            language=language,
        )
    builders = [
        identity_section,
        build_team_role_section(
            role=role,
            teammate_mode=teammate_mode,
            workspace_prompt_variant=workspace_prompt_variant,
            language=language,
        ),
        build_team_hitt_section(
            role=role,
            hitt_enabled=hitt_enabled,
            language=language,
            self_member_name=member_name,
            expose_human_agents_to_teammates=expose_human_agents_to_teammates,
        ),
        build_team_bridge_section(
            role=role,
            language=language,
            self_member_name=member_name,
        ),
        build_team_workflow_section(
            role=role,
            team_mode=team_mode,
            language=language,
        ),
        build_team_dispatch_section(
            role=role,
            dispatch_mode=dispatch_mode,
            language=language,
        ),
        build_team_lifecycle_section(
            role=role,
            lifecycle=lifecycle,
            language=language,
        ),
        build_team_extra_section(
            base_prompt=base_prompt,
            language=language,
        ),
    ]
    sections = [section for section in builders if section is not None]
    # Every team member — in-process or external CLI — receives inbound
    # messages, framework events and team-state updates as <team-inbound> /
    # <team-event> / <team-context> XML, so the inbound tag notice is always
    # included.
    sections.append(build_team_inbound_tags_section(language=language))
    return sections


def build_team_member_system_prompt(
    *,
    role: TeamRole,
    member_name: str | None,
    display_name: str = "",
    member_workspace_path: str | None = None,
    member_prompt: str = "",
    lifecycle: str = "temporary",
    teammate_mode: str = "build_mode",
    team_mode: str = "default",
    dispatch_mode: str = "autonomous",
    base_prompt: str | None = None,
    language: str = "cn",
    hitt_enabled: bool = False,
    expose_human_agents_to_teammates: bool = False,
    workspace_prompt_variant: Literal["native", "external"] = "native",
) -> str:
    """Render a member's team sections into a single standalone system prompt.

    Used to give an external CLI member (whose brain is not a local DeepAgent)
    the same team-rail sections an in-process member gets, assembled the same
    way (priority-ordered, ``\\n\\n``-joined). It includes ONLY the team
    sections — the harness / other DeepAgent rails do not apply to an external
    CLI, so their prompt contributions are intentionally excluded.

    The per-member section IS inlined here (``include_member_specific``):
    an external CLI prompt is a standalone per-member snapshot, not a prefix
    shared with sibling members, so there is no cache to protect — and at launch
    there is no conversation yet to deliver it into.

    Args mirror :func:`build_team_static_sections`.

    Returns:
        The rendered system prompt, or ``""`` when no section produced content.
    """
    sections = build_team_static_sections(
        role=role,
        member_name=member_name,
        display_name=display_name,
        member_workspace_path=member_workspace_path,
        member_prompt=member_prompt,
        lifecycle=lifecycle,
        teammate_mode=teammate_mode,
        team_mode=team_mode,
        dispatch_mode=dispatch_mode,
        base_prompt=base_prompt,
        language=language,
        hitt_enabled=hitt_enabled,
        expose_human_agents_to_teammates=expose_human_agents_to_teammates,
        include_member_specific=True,
        workspace_prompt_variant=workspace_prompt_variant,
    )
    builder = SystemPromptBuilder(language=language)
    for section in sections:
        builder.add_section(section)
    return builder.build()


__all__ = [
    "TeamSectionName",
    "build_team_bridge_section",
    "build_team_dispatch_section",
    "build_team_extra_section",
    "build_team_hitt_section",
    "build_team_identity_section",
    "build_team_inbound_tags_section",
    "build_team_lifecycle_section",
    "build_team_member_system_prompt",
    "build_team_role_section",
    "build_team_static_sections",
    "build_team_workflow_section",
]

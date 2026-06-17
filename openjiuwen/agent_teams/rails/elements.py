# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Manifest declarations for the built-in team rails.

The six team rails (tool / policy / workspace / tool-approval / plan-mode /
reliability) are declared here as ``@harness_element`` factories so they
assemble through the same provider path as every other DeepAgent capability —
no more hand-``new`` + ``TeamHarness.build`` named params + closure ``add_rail``.

Construction split (mirrors swarm's param-vs-context convention):
- **params** (``param_field``): serializable static config the configurator
  bakes into ``RailSpec.params`` (lifecycle / teammate_mode / team_name / ...).
- **context attrs** (``context_field``): per-member environment values that
  ``setup_agent`` fills on the ``BuildContext`` via ``derive`` (role /
  member_name / language).
- **live handles**: ``team_backend`` / ``workspace_manager`` / ``messager`` /
  callbacks — runtime plumbing the configurator injects into
  ``context.extras``; the factories read them directly via the
  ``team_context`` accessors and do NOT model them in the input schema.

Each factory mints a fresh rail on every native rebuild — rails are never
cached. State that must survive a rebuild lives in a reused object injected on
the build context (e.g. ``reliability_components``) and passed into the fresh
rail's constructor. Returning ``None`` gates the rail out for this member.
"""

from __future__ import annotations

from typing import Any, Optional

from openjiuwen.agent_teams.harness.manifest import (
    ConstructionInput,
    ElementKind,
    context_field,
    harness_element,
    param_field,
)
from openjiuwen.agent_teams.rails.team_context import (
    get_messager,
    get_model_allocator,
    get_on_teammate_created,
    get_reliability_components,
    get_swarmflow_model_resolver,
    get_swarmflow_worker_base_spec,
    get_team_backend,
    get_workspace_manager,
)

# Element names (the RailSpec ``type`` values). The team rails live under the
# ``core.team.*`` namespace — the ``core.`` layer prefix plus a ``team`` group —
# parallel to a platform's ``swarm.*`` namespace.
TEAM_TOOL = "core.team.tool"
TEAM_POLICY = "core.team.policy"
TEAM_WORKSPACE = "core.team.workspace"
TEAM_TOOL_APPROVAL = "core.team.tool_approval"
TEAM_PLAN_MODE = "core.team.plan_mode"
TEAM_RELIABILITY = "core.team.reliability"


# ---------------------------------------------------------------------------
# team.tool — TeamToolRail
# ---------------------------------------------------------------------------


class TeamToolInput(ConstructionInput):
    """Construction inputs for the team coordination tool rail."""

    role: str = context_field(attr="role", default="leader", description="Team role value.")
    member_name: str = context_field(attr="member_name", default="", description="Member name.")
    language: str = context_field(attr="language", default="cn", description="Resolved language code.")
    teammate_mode: str = param_field(default="build_mode", description="Member execution mode.")
    lifecycle: str = param_field(default="temporary", description="Team lifecycle (temporary / persistent).")
    exclude_tools: list[str] = param_field(default_factory=list, description="Tool names to exclude.")
    qualify_ids: bool = param_field(default=False, description="Suffix tool ids per member (inprocess spawn).")
    team_name: str = param_field(default="default", description="Team name.")


@harness_element(
    kind=ElementKind.RAIL,
    name=TEAM_TOOL,
    description="Registers role-appropriate team coordination tools on the agent.",
    input_model=TeamToolInput,
)
def build_team_tool_rail(params: dict[str, Any], context: Any) -> Any:
    """Build the team coordination tool rail (gated on a team backend)."""
    backend = get_team_backend(context)
    if backend is None:
        return None
    from openjiuwen.agent_teams.rails.team_tool_rail import TeamToolRail

    inp = TeamToolInput.resolve(params, context)
    allocator = get_model_allocator(context)
    model_config_allocator = allocator.allocate if allocator is not None else None
    return TeamToolRail(
        team_backend=backend,
        role=inp.role,
        teammate_mode=inp.teammate_mode,
        lifecycle=inp.lifecycle,
        language=inp.language,
        on_teammate_created=get_on_teammate_created(context),
        model_config_allocator=model_config_allocator,
        exclude_tools=set(inp.exclude_tools) or None,
        workspace_manager=get_workspace_manager(context),
        qualify_ids=inp.qualify_ids,
        team_name=inp.team_name,
        member_name=inp.member_name,
        messager=get_messager(context),
        swarmflow_model_resolver=get_swarmflow_model_resolver(context),
        swarmflow_worker_base_spec=get_swarmflow_worker_base_spec(context),
    )


# ---------------------------------------------------------------------------
# team.policy — TeamPolicyRail
# ---------------------------------------------------------------------------


class TeamPolicyInput(ConstructionInput):
    """Construction inputs for the team policy / prompt rail."""

    role: str = context_field(attr="role", default="leader", description="Team role value.")
    member_name: str = context_field(attr="member_name", default="", description="Member name.")
    language: str = context_field(attr="language", default="cn", description="Resolved language code.")
    persona: str = param_field(default="", description="Member persona.")
    lifecycle: str = param_field(default="temporary", description="Team lifecycle.")
    teammate_mode: str = param_field(default="build_mode", description="Member execution mode.")
    team_mode: str = param_field(default="default", description="Team operating mode.")
    base_prompt: Optional[str] = param_field(default=None, description="User-supplied base system prompt.")
    team_workspace_mount: Optional[str] = param_field(default=None, description="Team workspace mount path.")
    team_workspace_path: Optional[str] = param_field(default=None, description="Team workspace root path.")
    expose_human_agents_to_teammates: bool = param_field(
        default=False,
        description="Whether teammates see the concrete human-agent roster.",
    )
    enable_swarmflow: bool = param_field(default=False, description="Whether swarmflow is enabled.")


@harness_element(
    kind=ElementKind.RAIL,
    name=TEAM_POLICY,
    description="Injects team-specific PromptSections into the system prompt builder.",
    input_model=TeamPolicyInput,
)
def build_team_policy_rail(params: dict[str, Any], context: Any) -> Any:
    """Build the team policy rail (always mounted)."""
    from openjiuwen.agent_teams.rails.team_policy_rail import TeamPolicyRail
    from openjiuwen.agent_teams.schema.team import TeamRole

    inp = TeamPolicyInput.resolve(params, context)
    return TeamPolicyRail(
        role=TeamRole(inp.role),
        persona=inp.persona,
        member_name=inp.member_name or None,
        lifecycle=inp.lifecycle,
        teammate_mode=inp.teammate_mode,
        language=inp.language,
        team_mode=inp.team_mode,
        base_prompt=inp.base_prompt,
        team_workspace_mount=inp.team_workspace_mount,
        team_workspace_path=inp.team_workspace_path,
        team_backend=get_team_backend(context),
        expose_human_agents_to_teammates=inp.expose_human_agents_to_teammates,
        enable_swarmflow=inp.enable_swarmflow,
    )


# ---------------------------------------------------------------------------
# team.workspace — TeamWorkspaceRail
# ---------------------------------------------------------------------------


class TeamWorkspaceInput(ConstructionInput):
    """Construction inputs for the team workspace rail."""

    member_name: str = context_field(attr="member_name", default="", description="Member name.")


@harness_element(
    kind=ElementKind.RAIL,
    name=TEAM_WORKSPACE,
    description="Shared-workspace prompt + meta rail (gated on a workspace manager).",
    input_model=TeamWorkspaceInput,
)
def build_team_workspace_rail(params: dict[str, Any], context: Any) -> Any:
    """Build the team workspace rail (gated on a workspace manager)."""
    workspace_manager = get_workspace_manager(context)
    if workspace_manager is None:
        return None
    from openjiuwen.agent_teams.team_workspace.rails import TeamWorkspaceRail

    inp = TeamWorkspaceInput.resolve(params, context)
    return TeamWorkspaceRail(workspace_manager, inp.member_name)


# ---------------------------------------------------------------------------
# team.tool_approval — TeamToolApprovalRail
# ---------------------------------------------------------------------------


class TeamToolApprovalInput(ConstructionInput):
    """Construction inputs for the teammate tool-approval rail."""

    member_name: str = context_field(attr="member_name", default="", description="Member name.")
    team_name: str = param_field(default="default", description="Team name.")
    leader_member_name: str = param_field(default="", description="Leader member name.")
    tool_names: list[str] = param_field(default_factory=list, description="Tools that require approval.")


@harness_element(
    kind=ElementKind.RAIL,
    name=TEAM_TOOL_APPROVAL,
    description="Leader-mediated approval gate for teammate tool calls.",
    input_model=TeamToolApprovalInput,
)
def build_team_tool_approval_rail(params: dict[str, Any], context: Any) -> Any:
    """Build the tool-approval rail (gated on backend + messager + tool list)."""
    backend = get_team_backend(context)
    messager = get_messager(context)
    inp = TeamToolApprovalInput.resolve(params, context)
    if backend is None or messager is None or not inp.tool_names:
        return None
    from openjiuwen.agent_teams.rails.tool_approval_rail import TeamToolApprovalRail

    return TeamToolApprovalRail(
        team_name=inp.team_name,
        member_name=inp.member_name,
        db=backend.db,
        messager=messager,
        leader_member_name=inp.leader_member_name,
        tool_names=inp.tool_names,
    )


# ---------------------------------------------------------------------------
# team.plan_mode — TeamPlanModeRail
# ---------------------------------------------------------------------------


class TeamPlanModeInput(ConstructionInput):
    """Construction inputs for the team plan-mode rail."""

    language: str = context_field(attr="language", default="cn", description="Resolved language code.")


@harness_element(
    kind=ElementKind.RAIL,
    name=TEAM_PLAN_MODE,
    description="team.plan leader prompt overlay for the generic plan-mode mechanics.",
    input_model=TeamPlanModeInput,
)
def build_team_plan_mode_rail(params: dict[str, Any], context: Any) -> Any:
    """Build the team plan-mode rail."""
    from openjiuwen.agent_teams.rails.team_plan_mode_rail import TeamPlanModeRail

    inp = TeamPlanModeInput.resolve(params, context)
    return TeamPlanModeRail(language=inp.language)


# ---------------------------------------------------------------------------
# team.reliability — ReliabilityRail
# ---------------------------------------------------------------------------


class TeamReliabilityInput(ConstructionInput):
    """Construction inputs for the proactive reliability rail."""

    member_name: str = context_field(attr="member_name", default="", description="Member name.")
    reliability_cfg: dict = param_field(default_factory=dict, description="Serialized ReliabilityConfig.")
    team_name: str = param_field(default="default", description="Team name.")
    sender_id: str = param_field(default="", description="Anomaly event sender id.")
    is_leader: bool = param_field(default=False, description="Whether this member is the leader.")


@harness_element(
    kind=ElementKind.RAIL,
    name=TEAM_RELIABILITY,
    description="Proactive reliability monitoring rail (gated on a config).",
    input_model=TeamReliabilityInput,
)
def build_team_reliability_rail(params: dict[str, Any], context: Any) -> Any:
    """Build the reliability rail, wrapping the reused components when present."""
    inp = TeamReliabilityInput.resolve(params, context)
    if not inp.reliability_cfg:
        return None
    from openjiuwen.agent_teams.reliability.factory import (
        build_reliability_components,
        reliability_rail_from_components,
    )

    # The stateful core (detector windows, remediator, leader sink) is built once
    # by the configurator and injected; wrap it in a fresh rail each cycle. The
    # fallback rebuilds from params for contexts without injected components
    # (e.g. a cross-process member configured outside this build context).
    components = get_reliability_components(context)
    if components is None:
        from openjiuwen.agent_teams.reliability.config import ReliabilityConfig

        cfg = ReliabilityConfig.model_validate(inp.reliability_cfg)
        components = build_reliability_components(
            cfg,
            member_name=inp.member_name,
            messager=get_messager(context),
            team_name=inp.team_name,
            sender_id=inp.sender_id,
            is_leader=inp.is_leader,
        )
    return reliability_rail_from_components(components)


__all__ = [
    "TEAM_TOOL",
    "TEAM_POLICY",
    "TEAM_WORKSPACE",
    "TEAM_TOOL_APPROVAL",
    "TEAM_PLAN_MODE",
    "TEAM_RELIABILITY",
]

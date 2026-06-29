# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Factory function that assembles role-filtered tool lists for team agents."""

from functools import wraps
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from openjiuwen.agent_teams.tools.team import TeamBackend
from openjiuwen.agent_teams.tools.tool_base import MappedToolOutput, TeamTool
from openjiuwen.agent_teams.tools.tool_async import (
    AsyncTaskCancelTool,
    AsyncTaskOutputTool,
    AsyncTasksListTool,
)
from openjiuwen.agent_teams.tools.tool_member import (
    ApprovePlanTool,
    ApproveToolCallTool,
    ListMembersTool,
    ShutdownMemberTool,
    SpawnBridgeAgentTool,
    SpawnExternalCliTool,
    SpawnHumanAgentTool,
    SpawnTeammateTool,
)
from openjiuwen.agent_teams.tools.tool_message import SendMessageTool
from openjiuwen.agent_teams.tools.tool_permissions import (
    HUMAN_AGENT_TOOLS,
    LEADER_TOOLS,
    MEMBER_TOOLS,
)
from openjiuwen.agent_teams.tools.tool_task import (
    ClaimTaskTool,
    MemberCompleteTaskTool,
    SubmitPlanTool,
    TaskCreateTool,
    UpdateTaskTool,
    ViewTaskToolV2,
)
from openjiuwen.agent_teams.tools.tool_team import BuildTeamTool, CleanTeamTool
from openjiuwen.core.foundation.tool.base import Tool
from openjiuwen.harness.tools.base_tool import ToolOutput

if TYPE_CHECKING:
    from openjiuwen.agent_teams.models.allocator import Allocation


# ========== Tool Factory ==========


def create_team_tools(
    *,
    role: str,
    agent_team: TeamBackend,
    teammate_mode: str = "build_mode",
    lifecycle: str = "temporary",
    on_teammate_created: Callable[[str], Awaitable[None]] | None = None,
    model_config_allocator: Callable[[str | None], "Allocation | None"] | None = None,
    exclude_tools: set[str] | None = None,
    lang: str = "cn",
    parent_agent: Any = None,
    messager: Any = None,
    team_name: str = "default",
    swarmflow_model_resolver: Callable[[str], Any] | None = None,
    swarmflow_worker_base_spec: Any = None,
    swarmflow_human_base_spec: Any = None,
    concurrency_governor: Any = None,
    team_permissions_enabled: bool = False,
) -> list[Tool]:
    """Create role-appropriate tool instances filtered by permission sets.

    Args:
        role: "leader" or "teammate".
        agent_team: AgentTeam instance providing task/message/db/messager.
        teammate_mode: Execution mode for teammates — "build_mode" or
            "plan_mode". Leader's approval tools (approve_plan / approve_tool)
            are only wired when teammate_mode == "plan_mode", since that's the
            only mode where teammates submit plans and tool calls can be held
            for leader sign-off.
        lifecycle: Team lifecycle — "temporary" or "persistent". The
            ``clean_team`` tool is only wired for temporary teams; persistent
            teams are torn down through operator-level SDK facades
            (``delete_agent_team`` etc.), so exposing a leader-callable
            tear-down tool inside a round would race the pool invariants.
        on_teammate_created: Callback invoked when a teammate is created.
        model_config_allocator: Callback that returns the next
            ``Allocation`` for teammate allocation. Receives an
            optional ``model_name`` hint forwarded from the spawn site;
            ``RoundRobinModelAllocator`` ignores the hint while
            ``ByModelNameAllocator`` requires it.
        exclude_tools: Tool names to exclude from the allowed set.
        lang: Locale code ("cn" or "en") for tool descriptions.
        parent_agent: The owning ``NativeHarness`` (a ``DeepAgent``) that the
            leader-only async ``swarmflow`` tool launches background work on.
            Supplied at rail-init time; None for callers without a harness
            (e.g. external CLI members), which never get ``swarmflow`` anyway.
        messager: The member's messager — the ``swarmflow`` tool uses it to
            publish phase-progress events for the spectator leader.
        team_name: Team name used for swarmflow event routing / worker ids.
        swarmflow_model_resolver: Resolves an ``agent(model=...)`` name hint to a
            worker ``Model``. Non-None only for a leader whose spec has
            ``enable_swarmflow``; when None the ``swarmflow`` tool is gated out.
    """
    from openjiuwen.agent_teams.tools.locales import make_translator
    from openjiuwen.agent_teams.workflow.tool_swarmflow import SwarmflowTool

    t = make_translator(lang)
    task_mgr = agent_team.task_manager
    msg_mgr = agent_team.message_manager

    all_tools = {
        # Team management
        "build_team": BuildTeamTool(agent_team, t),
        "clean_team": CleanTeamTool(agent_team, t),
        # Member management — one tool per role_type (flat schema, no role branching)
        "spawn_teammate": SpawnTeammateTool(agent_team, t, model_config_allocator=model_config_allocator),
        "spawn_human_agent": SpawnHumanAgentTool(agent_team, t),
        "spawn_bridge_agent": SpawnBridgeAgentTool(agent_team, t),
        "spawn_external_cli": SpawnExternalCliTool(agent_team, t),
        "shutdown_member": ShutdownMemberTool(agent_team, t),
        "approve_plan": ApprovePlanTool(agent_team, t),
        "approve_tool": ApproveToolCallTool(agent_team, t),
        "list_members": ListMembersTool(agent_team, t),
        # Task management
        "create_task": TaskCreateTool(agent_team, t),
        "update_task": UpdateTaskTool(agent_team, t),
        "view_task": ViewTaskToolV2(task_mgr, t),
        "claim_task": ClaimTaskTool(task_mgr, t),
        "submit_plan": SubmitPlanTool(task_mgr, t),
        "member_complete_task": MemberCompleteTaskTool(task_mgr, t),
        # Messaging
        "send_message": SendMessageTool(
            msg_mgr,
            t,
            team=agent_team,
            on_teammate_created=on_teammate_created,
        ),
        # Swarmflow orchestration (leader-only, gated by swarmflow_model_resolver).
        "swarmflow": SwarmflowTool(
            parent_agent=parent_agent,
            messager=messager,
            team_name=team_name,
            model_resolver=swarmflow_model_resolver,
            worker_base_spec=swarmflow_worker_base_spec,
            human_base_spec=swarmflow_human_base_spec,
            concurrency_governor=concurrency_governor,
            t=t,
            language=lang,
        ),
        # Async-tool control tools (list / output / cancel background tasks).
        # Leader-only (in LEADER_ONLY_TOOLS); always wired — harmless when the
        # registry is empty. ``parent_agent`` is the harness whose runtime they
        # query; None for harness-less callers (filtered out by role anyway).
        "async_tasks_list": AsyncTasksListTool(parent_agent, t, language=lang),
        "async_task_output": AsyncTaskOutputTool(parent_agent, t, language=lang),
        "async_task_cancel": AsyncTaskCancelTool(parent_agent, t, language=lang),
    }

    if role == "human_agent":
        allowed = HUMAN_AGENT_TOOLS
    elif role == "leader":
        allowed = LEADER_TOOLS
    else:
        allowed = MEMBER_TOOLS
    # Plan tools only make sense in plan_mode.
    if teammate_mode != "plan_mode":
        excluded = {"approve_plan", "submit_plan"}
        # Leader must keep approve_tool when team permissions are on,
        # so it can resolve teammate ASK interrupts.
        if not team_permissions_enabled:
            excluded.add("approve_tool")
        allowed = allowed - excluded
    # clean_team is a temporary-team primitive only. Persistent teams are
    # torn down by the operator through SDK facades (delete_agent_team etc.);
    # letting the leader LLM call clean_team mid-round would race the runtime
    # pool invariants and silently de-register a team the operator still considers
    # live. Temporary teams keep the tool — they have no external operator;
    # the leader is the only one who can wind them down.
    if lifecycle == "persistent":
        allowed = allowed - {"clean_team"}
    # Capability gating for the non-teammate spawn tools: a spawn tool the
    # backend would reject is simply never shown to the LLM. spawn_teammate is
    # always available. Symmetric to the plan_mode / persistent gates above.
    # Unconditional set subtraction is idempotent — teammate / human_agent
    # ``allowed`` sets don't contain these leader-only tools anyway.
    if not agent_team.hitt_enabled():
        allowed = allowed - {"spawn_human_agent"}
    if not agent_team.bridge_enabled():
        allowed = allowed - {"spawn_bridge_agent"}
    if not agent_team.external_cli_kinds():
        allowed = allowed - {"spawn_external_cli"}
    # Swarmflow is wired only when the host supplied a worker-model resolver
    # (leader + enable_swarmflow). Same idempotent-subtraction gate as the
    # spawn tools.
    if swarmflow_model_resolver is None:
        allowed = allowed - {"swarmflow"}
    if exclude_tools:
        allowed = allowed - exclude_tools
    tools = [tool for name, tool in all_tools.items() if name in allowed]

    for tool in tools:
        _wrap_invoke_with_logging(tool)

    return tools


def _wrap_invoke_with_logging(tool: Tool) -> None:
    """Wrap a tool's invoke method with debug logging and result mapping.

    For TeamTool instances, the wrapper also calls map_result() to produce
    a MappedToolOutput whose __str__ returns model-optimized text.
    """
    from openjiuwen.core.common.logging import team_logger

    original_invoke = tool.invoke
    tool_name = tool.card.name
    is_team_tool = isinstance(tool, TeamTool)

    @wraps(original_invoke)
    async def logged_invoke(inputs: dict[str, Any], **kwargs: Any) -> ToolOutput:
        team_logger.debug(f"[{tool_name}] invoke start, inputs={inputs}")
        result = await original_invoke(inputs, **kwargs)
        team_logger.debug(f"[{tool_name}] invoke end, output={result}")
        if is_team_tool:
            mapped = tool.map_result(result)  # type: ignore[union-attr]
            return MappedToolOutput.from_output(result, mapped)
        return result

    tool.invoke = logged_invoke

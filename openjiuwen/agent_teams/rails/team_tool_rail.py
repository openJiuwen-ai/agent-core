# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""TeamToolRail — register team coordination tools onto the DeepAgent.

Mirrors the pattern of :class:`SysOperationRail` / :class:`McpRail` /
:class:`LspRail`: the rail's ``init`` constructs the role-appropriate
team tools (coordination, messaging, task, optional workspace and
worktree extras) and wires their ``ToolCard``s into the agent's shared
``ability_manager``, while registering the runtime instances on
``Runner.resource_mgr`` so the dispatcher can resolve invocations.

Centralising team tool registration in a Rail keeps the team tool
surface aligned with how every other DeepAgent capability is mounted,
and lets ``uninit`` cleanly tear the surface back down on rail removal
or hot reconfigure.
"""

from __future__ import annotations

from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Callable,
    Optional,
    Set,
)

from openjiuwen.agent_teams.tools.team_tools import create_team_tools
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.foundation.tool.base import Tool
from openjiuwen.core.runner import Runner
from openjiuwen.harness.rails.base import DeepAgentRail

if TYPE_CHECKING:
    from openjiuwen.agent_teams.models.allocator import Allocation
    from openjiuwen.agent_teams.team_workspace.manager import TeamWorkspaceManager
    from openjiuwen.agent_teams.tools.team import TeamBackend
    from openjiuwen.harness.tools.worktree import WorktreeManager


class TeamToolRail(DeepAgentRail):
    """Register team coordination tools on the DeepAgent.

    The rail is role-aware: ``create_team_tools`` filters the surface for
    leader / teammate / human_agent. Optional extensions are added when
    the corresponding manager is supplied:

      * ``workspace_manager`` -> ``WorkspaceMetaTool``
      * ``worktree_manager`` -> ``EnterWorktreeTool`` / ``ExitWorktreeTool``
        (also primes the worktree session ContextVar)

    When the team runs in ``inprocess`` spawn mode, ``qualify_ids``
    suffixes each ``ToolCard.id`` with ``{team_name}.{member_name}`` so
    multiple in-process members do not collide on the shared
    ``Runner.resource_mgr``.
    """

    priority = 90

    def __init__(
        self,
        *,
        team_backend: "TeamBackend",
        role: str,
        teammate_mode: str = "build_mode",
        lifecycle: str = "temporary",
        language: str = "cn",
        on_teammate_created: Optional[Callable[[str], Awaitable[None]]] = None,
        model_config_allocator: Optional[Callable[[Optional[str]], Optional["Allocation"]]] = None,
        exclude_tools: Optional[Set[str]] = None,
        workspace_manager: Optional["TeamWorkspaceManager"] = None,
        worktree_manager: Optional["WorktreeManager"] = None,
        qualify_ids: bool = False,
        team_name: str = "default",
        member_name: str = "",
    ) -> None:
        super().__init__()
        self._team_backend = team_backend
        self._role = role
        self._teammate_mode = teammate_mode
        self._lifecycle = lifecycle
        self._language = language
        self._on_teammate_created = on_teammate_created
        self._model_config_allocator = model_config_allocator
        self._exclude_tools = exclude_tools
        self._workspace_manager = workspace_manager
        self._worktree_manager = worktree_manager
        self._qualify_ids = qualify_ids
        self._team_name = team_name or "default"
        self._member_name = member_name or "unknown"
        self._tools: list[Tool] | None = None

    def init(self, agent: Any) -> None:
        """Build team tools and register them on the agent.

        Idempotent: callers may invoke ``init`` eagerly during configure
        and the DeepAgent's lazy rail-init pass will then skip the
        already-registered surface.
        """
        if self._tools is not None:
            return
        super().init(agent)

        tools: list[Tool] = create_team_tools(
            role=self._role,
            agent_team=self._team_backend,
            teammate_mode=self._teammate_mode,
            lifecycle=self._lifecycle,
            on_teammate_created=self._on_teammate_created,
            model_config_allocator=self._model_config_allocator,
            exclude_tools=self._exclude_tools,
            lang=self._language,
        )

        if self._workspace_manager is not None:
            from openjiuwen.agent_teams.team_workspace.tools import WorkspaceMetaTool
            from openjiuwen.agent_teams.tools.locales import make_translator

            ws_t = make_translator(self._language)
            tools.append(WorkspaceMetaTool(self._workspace_manager, ws_t))

        if self._worktree_manager is not None:
            from openjiuwen.harness.tools.worktree import (
                EnterWorktreeTool,
                ExitWorktreeTool,
                init_session_state,
            )

            tools.append(EnterWorktreeTool(self._worktree_manager, language=self._language))
            tools.append(ExitWorktreeTool(self._worktree_manager, language=self._language))
            init_session_state()

        if self._qualify_ids:
            qualify_team_tool_ids(
                tools,
                team_name=self._team_name,
                member_name=self._member_name,
            )

        try:
            Runner.resource_mgr.add_tool(tools, refresh=True)
        except Exception:
            team_logger.debug("Runner.resource_mgr not available, skipping tool registration")

        ability_manager = getattr(agent, "ability_manager", None)
        if ability_manager is not None:
            for tool in tools:
                ability_manager.add(tool.card)

        self._tools = tools

    def uninit(self, agent: Any) -> None:
        """Remove team tools from the agent and the shared resource manager."""
        if not self._tools:
            return

        ability_manager = getattr(agent, "ability_manager", None)
        for tool in self._tools:
            name = getattr(tool.card, "name", None)
            if ability_manager is not None and name:
                ability_manager.remove(name)
            tool_id = getattr(tool.card, "id", None)
            if tool_id:
                try:
                    Runner.resource_mgr.remove_tool(tool_id)
                except Exception:
                    team_logger.debug("Runner.resource_mgr removal failed for {}", tool_id)

        self._tools = None


def qualify_team_tool_ids(
    team_tools: list[Tool],
    *,
    team_name: str,
    member_name: str,
) -> None:
    """Suffix tool IDs with team/member to avoid collisions under inprocess spawn.

    ``Runner.resource_mgr`` is process-global; running multiple team
    members in the same process means their tool IDs must be unique
    across members or registrations will overwrite each other.
    """
    team_key = team_name or "default"
    member_key = member_name or "unknown"
    for tool in team_tools:
        if tool.card is None or not tool.card.id:
            continue
        qualified_id = f"{tool.card.id}.{team_key}.{member_key}"
        if tool.card.id != qualified_id:
            tool.card.id = qualified_id


__all__ = [
    "TeamToolRail",
    "qualify_team_tool_ids",
]

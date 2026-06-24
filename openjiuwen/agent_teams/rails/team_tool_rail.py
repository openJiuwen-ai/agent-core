# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""TeamToolRail — register team coordination tools onto the DeepAgent.

Mirrors the pattern of :class:`SysOperationRail` / :class:`McpRail` /
:class:`LspRail`: the rail's ``init`` constructs the role-appropriate
team tools (coordination, messaging, task, optional workspace extras)
and wires their ``ToolCard``s into the agent's shared ``ability_manager``,
while registering the runtime instances on ``Runner.resource_mgr`` so the
dispatcher can resolve invocations.

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

from openjiuwen.agent_teams.paths import async_tool_output_dir
from openjiuwen.agent_teams.tools.team_tools import create_team_tools
from openjiuwen.core.foundation.tool.base import Tool
from openjiuwen.harness.rails.base import DeepAgentRail

if TYPE_CHECKING:
    from pathlib import Path

    from openjiuwen.agent_teams.models.allocator import Allocation
    from openjiuwen.agent_teams.team_workspace.manager import TeamWorkspaceManager
    from openjiuwen.agent_teams.tools.team import TeamBackend


class TeamToolRail(DeepAgentRail):
    """Register team coordination tools on the DeepAgent.

    The rail is role-aware: ``create_team_tools`` filters the surface for
    leader / teammate / human_agent. Optional extensions are added when
    the corresponding manager is supplied:

      * ``workspace_manager`` -> ``WorkspaceMetaTool``

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
        qualify_ids: bool = False,
        team_name: str = "default",
        member_name: str = "",
        messager: Optional[Any] = None,
        swarmflow_model_resolver: Optional[Callable[[str], Any]] = None,
        swarmflow_worker_base_spec: Optional[Any] = None,
        swarmflow_human_base_spec: Optional[Any] = None,
        team_permissions_enabled: bool = False,
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
        self._qualify_ids = qualify_ids
        self._team_name = team_name or "default"
        self._member_name = member_name or "unknown"
        self._messager = messager
        self._swarmflow_model_resolver = swarmflow_model_resolver
        self._swarmflow_worker_base_spec = swarmflow_worker_base_spec
        self._swarmflow_human_base_spec = swarmflow_human_base_spec
        self._team_permissions_enabled = team_permissions_enabled
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
            parent_agent=agent,
            messager=self._messager,
            team_name=self._team_name,
            swarmflow_model_resolver=self._swarmflow_model_resolver,
            swarmflow_worker_base_spec=self._swarmflow_worker_base_spec,
            swarmflow_human_base_spec=self._swarmflow_human_base_spec,
            team_permissions_enabled=self._team_permissions_enabled,
        )

        if self._workspace_manager is not None:
            from openjiuwen.agent_teams.team_workspace.tools import WorkspaceMetaTool
            from openjiuwen.agent_teams.tools.locales import make_translator

            ws_t = make_translator(self._language)
            tools.append(WorkspaceMetaTool(self._workspace_manager, ws_t))

        # Register through the unified ``add_ability`` entry point. It qualifies
        # each stateful tool id to ``{name}_{owner_id}`` (owner_id is this
        # member's agent id, so ids stay unique across members sharing one
        # process — superseding the old ``qualify_ids`` suffixing), binds the
        # instance in the resource manager, and — crucially — registers the tool
        # the same way ``ability_manager.teardown_tools`` expects, so the team
        # tools are dropped at round-end stop. The previous bespoke path
        # (``qualify_team_tool_ids`` + ``add_tool(refresh=True)`` +
        # ``ability_manager.add``) produced ``team.<tool>.<session>.<member>``
        # ids that teardown_tools did not match, so they leaked across native
        # rebuilds and refresh-warned every cycle.
        ability_manager = getattr(agent, "ability_manager", None)
        if ability_manager is not None:
            for tool in tools:
                ability_manager.add_ability(tool.card, tool)

        self._wire_async_spill(agent)

        self._tools = tools

    def _wire_async_spill(self, agent: Any) -> None:
        """Wire the async-tool runtime's spill output-dir resolver (phase B).

        An async tool whose rendered result exceeds the runtime's spill
        threshold writes the full payload to a per-session directory instead of
        inlining it. The directory is resolved lazily — the session id is only
        available once a round runs — and registered for cleanup on first use,
        so ``clean_team`` removes it. No-op when the host exposes no async-tool
        runtime (a non-NativeHarness DeepAgent).
        """
        runtime = getattr(agent, "async_tool_runtime", None)
        if runtime is None:
            return
        team_name = self._team_name
        team_backend = self._team_backend

        def _resolve_output_dir() -> "Path | None":
            session_id = getattr(agent, "session_id", None)
            if not session_id:
                return None
            out_dir = async_tool_output_dir(team_name, session_id)
            team_backend.register_cleanup_path(str(out_dir))
            return out_dir

        runtime.output_dir_resolver = _resolve_output_dir

    def uninit(self, agent: Any) -> None:
        """Remove team tools from the agent and the shared resource manager."""
        if not self._tools:
            return

        ability_manager = getattr(agent, "ability_manager", None)
        if ability_manager is not None:
            for tool in self._tools:
                name = getattr(tool.card, "name", None)
                if name:
                    # Mirror ``add_ability``: removes the agent-qualified id from
                    # both this manager and the shared resource manager.
                    ability_manager.remove_ability(name)

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

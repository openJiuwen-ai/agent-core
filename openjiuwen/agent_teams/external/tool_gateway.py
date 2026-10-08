# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Provider-neutral native-tool gateway for an external team member."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Iterable

from openjiuwen.agent_teams.context import reset_session_id, set_session_id
from openjiuwen.agent_teams.team_workspace.tools import WorkspaceMetaTool
from openjiuwen.agent_teams.tools.locales import make_translator
from openjiuwen.agent_teams.tools.team_tools import create_team_tools
from openjiuwen.core.common.logging import team_logger
from openjiuwen.harness_protocol import (
    ToolDefinition,
    ToolExecutionResult,
    ToolInvocation,
    json_value_to_builtin,
)

if TYPE_CHECKING:
    from openjiuwen.agent_teams.models.allocator import Allocation
    from openjiuwen.agent_teams.team_workspace.manager import TeamWorkspaceManager
    from openjiuwen.agent_teams.tools.team import TeamBackend
    from openjiuwen.core.foundation.tool.base import Tool


class ExternalTeamToolGateway:
    """Expose member-scoped team tools directly through ToolGateway."""

    def __init__(
        self,
        *,
        session_id: str,
        team_backend: TeamBackend,
        tools: Iterable[Tool],
    ) -> None:
        self._session_id = session_id
        self._team_backend = team_backend
        self._tools = {tool.card.name: tool for tool in tools}

    async def definitions(self) -> tuple[ToolDefinition, ...]:
        """Return the definitions of the bound local team tools."""
        return tuple(
            ToolDefinition(
                name=tool.card.name,
                description=tool.card.description,
                input_schema=tool.card.input_params,
            )
            for tool in self._tools.values()
        )

    async def invoke(self, invocation: ToolInvocation) -> ToolExecutionResult:
        """Execute one local team tool and render its model-facing result."""
        token = set_session_id(self._session_id)
        try:
            tool = self._tools.get(invocation.name)
            if tool is None:
                return ToolExecutionResult(content=f"Unknown tool: {invocation.name}", is_error=True)
            try:
                arguments = json_value_to_builtin(invocation.arguments)
                if not isinstance(arguments, dict):
                    raise TypeError("team tool arguments must be an object")
                result = await tool.invoke(
                    arguments,
                    member_name=self._team_backend.member_name,
                    display_name=self._team_backend.member_name,
                )
                return ToolExecutionResult(content=tool.render_for_llm(result))
            except Exception as exc:  # noqa: BLE001 - return tool failures to the calling model
                team_logger.exception("team native tool {} failed", invocation.name)
                return ToolExecutionResult(content=f"Internal error: {exc}", is_error=True)
        finally:
            reset_session_id(token)


def build_external_team_tool_gateway(
    *,
    session_id: str,
    team_backend: TeamBackend,
    role: str,
    teammate_mode: str,
    dispatch_mode: str,
    lifecycle: str,
    language: str,
    workspace_manager: TeamWorkspaceManager | None = None,
    model_config_allocator: Callable[[str | None], Allocation | None] | None = None,
    parent_agent: Any = None,
    messager: Any = None,
    team_name: str = "default",
    swarmflow_model_resolver: Callable[[str], Any] | None = None,
    swarmflow_worker_base_spec: Any = None,
    swarmflow_human_base_spec: Any = None,
    concurrency_governor: Any = None,
    swarmflow_budget: Any = None,
    team_permissions_enabled: bool = False,
) -> ExternalTeamToolGateway:
    """Build a native gateway over the external member's local backend."""
    tools = create_team_tools(
        role=role,
        agent_team=team_backend,
        teammate_mode=teammate_mode,
        dispatch_mode=dispatch_mode,
        lifecycle=lifecycle,
        model_config_allocator=model_config_allocator,
        lang=language,
        parent_agent=parent_agent,
        messager=messager,
        team_name=team_name,
        swarmflow_model_resolver=swarmflow_model_resolver,
        exclude_tools={"checkpoint"},
        swarmflow_worker_base_spec=swarmflow_worker_base_spec,
        swarmflow_human_base_spec=swarmflow_human_base_spec,
        concurrency_governor=concurrency_governor,
        swarmflow_budget=swarmflow_budget,
        team_permissions_enabled=team_permissions_enabled,
    )
    if workspace_manager is not None:
        tools.append(
            WorkspaceMetaTool(
                workspace_manager,
                make_translator(language, ws_cache=team_backend.workspace_cache),
            )
        )
    return ExternalTeamToolGateway(session_id=session_id, team_backend=team_backend, tools=tools)


__all__ = ["ExternalTeamToolGateway", "build_external_team_tool_gateway"]

# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Claude SDK in-process MCP bridge for team collaboration tools."""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, ContextManager, Protocol

from openjiuwen.agent_teams.external.cli_agent.claude.options import load_claude_sdk
from openjiuwen.agent_teams.team_workspace.tools import WorkspaceMetaTool
from openjiuwen.agent_teams.tools.locales import make_translator
from openjiuwen.agent_teams.tools.team_tools import create_team_tools
from openjiuwen.core.common.logging import team_logger

if TYPE_CHECKING:
    from openjiuwen.agent_teams.models.allocator import Allocation
    from openjiuwen.agent_teams.team_workspace.manager import TeamWorkspaceManager
    from openjiuwen.agent_teams.tools.team import TeamBackend
    from openjiuwen.core.foundation.tool.base import Tool


class _ClaudeToolExecutionBridge(Protocol):
    """Bridge that binds observability context around local Claude tools."""

    def tool_execution_context(self) -> ContextManager[None]:
        """Return a context manager for one local tool execution."""
        ...


def text_content(text: str) -> dict[str, Any]:
    """Build a Claude SDK MCP text result."""
    return {"content": [{"type": "text", "text": text}]}


@dataclass(slots=True)
class ClaudeSdkMcpToolSet:
    """In-process MCP tool set backed by the owning TeamAgent shell."""

    server: Any
    tools: dict[str, "Tool"]


def build_claude_sdk_mcp_tool_set(
    *,
    server_name: str,
    team_backend: "TeamBackend",
    role: str,
    teammate_mode: str,
    dispatch_mode: str,
    lifecycle: str,
    language: str,
    workspace_manager: "TeamWorkspaceManager | None" = None,
    model_config_allocator: Callable[[str | None], "Allocation | None"] | None = None,
    parent_agent: Any = None,
    messager: Any = None,
    team_name: str = "default",
    swarmflow_model_resolver: Callable[[str], Any] | None = None,
    swarmflow_worker_base_spec: Any = None,
    swarmflow_human_base_spec: Any = None,
    concurrency_governor: Any = None,
    swarmflow_budget: Any = None,
    team_permissions_enabled: bool = False,
    span_bridge: _ClaudeToolExecutionBridge | None = None,
) -> ClaudeSdkMcpToolSet:
    """Build a Claude SDK MCP server from the current TeamAgent backend.

    Args:
        server_name: Logical MCP server name passed to Claude Code.
        team_backend: Backend owned by the external member's TeamAgent shell.
        role: Team role used to resolve the tool surface.
        teammate_mode: Member execution mode.
        dispatch_mode: Team task dispatch mode.
        lifecycle: Team lifecycle.
        language: Tool description language.
        workspace_manager: Optional shared workspace manager.
        model_config_allocator: Optional model allocation callback for spawn tools.
        parent_agent: Optional parent agent used by leader-only async tools.
        messager: Optional messager used by leader-only async tools.
        team_name: Team name used for event routing.
        swarmflow_model_resolver: Optional Swarmflow model resolver.
        swarmflow_worker_base_spec: Optional Swarmflow worker spec.
        swarmflow_human_base_spec: Optional Swarmflow human spec.
        concurrency_governor: Optional Swarmflow concurrency governor.
        swarmflow_budget: Optional Swarmflow token budget ledger.
        team_permissions_enabled: Whether team permission tools are enabled.
        span_bridge: Optional observability bridge that binds the active Claude
            turn while local team tools execute.

    Returns:
        SDK MCP tool set containing the server config and wrapped tools.
    """
    # external-CLI tool descriptions resolve evolved values through the
    # backend's cache (it delegates to the workspace manager — the single
    # source every consumer uses). ``None`` (no manager attached /
    # evolution disabled) keeps the framework default.
    workspace_cache = team_backend.workspace_cache
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
        tools.append(WorkspaceMetaTool(workspace_manager, make_translator(language, ws_cache=workspace_cache)))

    tools_by_name = {tool.card.name: tool for tool in tools}
    sdk = load_claude_sdk()
    sdk_tools = [_wrap_team_tool(team_backend, tools_by_name, tool, span_bridge=span_bridge) for tool in tools]
    server = sdk.create_sdk_mcp_server(
        name=server_name,
        version="1.0.0",
        tools=sdk_tools,
    )
    return ClaudeSdkMcpToolSet(server=server, tools=tools_by_name)


def _wrap_team_tool(
    team_backend: "TeamBackend",
    tools_by_name: dict[str, "Tool"],
    team_tool: "Tool",
    *,
    span_bridge: _ClaudeToolExecutionBridge | None = None,
) -> Any:
    """Wrap one native TeamTool as a Claude SDK MCP tool."""
    sdk = load_claude_sdk()
    name = team_tool.card.name

    async def _handler(arguments: dict[str, Any]) -> dict[str, Any]:
        tool = tools_by_name.get(name)
        if tool is None:
            return text_content(f"Unknown tool: {name}")
        try:
            with _tool_execution_context(span_bridge):
                result = await tool.invoke(
                    arguments,
                    member_name=team_backend.member_name,
                    display_name=team_backend.member_name,
                )
        except Exception as exc:  # noqa: BLE001 - keep tool failures in-band
            team_logger.exception("claude sdk team tool {} failed", name)
            return text_content(f"Internal error: {exc}")
        return text_content(str(result))

    return sdk.tool(
        name=name,
        description=team_tool.card.description,
        input_schema=team_tool.card.input_params,
    )(_handler)


def _tool_execution_context(span_bridge: _ClaudeToolExecutionBridge | None) -> ContextManager[None]:
    """Return the bridge context for local team tool execution."""
    if span_bridge is None:
        return nullcontext()
    return span_bridge.tool_execution_context()


__all__ = [
    "ClaudeSdkMcpToolSet",
    "build_claude_sdk_mcp_tool_set",
    "text_content",
]

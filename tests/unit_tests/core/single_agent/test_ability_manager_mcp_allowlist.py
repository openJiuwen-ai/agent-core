# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for per-agent MCP schema and execution allowlists."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openjiuwen.core.foundation.llm import ToolCall
from openjiuwen.core.foundation.tool import McpServerConfig, ToolInfo
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.ability_manager import (
    AbilityExecutionError,
    AbilityManager,
)


def _server(
    server_id: str = "playwright_official_stdio",
    server_name: str = "playwright-official",
) -> McpServerConfig:
    return McpServerConfig(
        server_id=server_id,
        server_name=server_name,
        server_path="stdio://playwright",
        client_type="stdio",
    )


def _tool_infos() -> list[ToolInfo]:
    return [
        ToolInfo(name="browser_click", description="click", parameters={}),
        ToolInfo(name="browser_pdf_save", description="pdf", parameters={}),
        ToolInfo(name="browser_mouse_click_xy", description="vision", parameters={}),
    ]


async def _visible_names(manager: AbilityManager, tool_infos: list[ToolInfo]) -> set[str]:
    with patch.object(
        Runner.resource_mgr,
        "get_mcp_tool_infos",
        new=AsyncMock(return_value=tool_infos),
    ):
        visible = await manager.list_tool_info()
    return {tool.name for tool in visible}


@pytest.mark.asyncio
async def test_mcp_allowlist_filters_model_visible_schemas_and_cached_cards() -> None:
    config = _server()
    manager = AbilityManager()
    manager.add(config)
    manager.set_mcp_tool_allowlist(config, ["browser_click", "browser_pdf_save"])

    visible_names = await _visible_names(manager, _tool_infos())

    assert visible_names == {
        "mcp_playwright-official_browser_click",
        "mcp_playwright-official_browser_pdf_save",
    }
    assert "mcp_playwright-official_browser_mouse_click_xy" not in manager._tools


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "blocked_name",
    [
        "mcp_playwright-official_browser_mouse_click_xy",
        "playwright_official_stdio.playwright-official.browser_mouse_click_xy",
    ],
)
async def test_mcp_allowlist_rejects_disallowed_direct_calls(blocked_name: str) -> None:
    config = _server()
    manager = AbilityManager()
    manager.add(config)
    manager.set_mcp_tool_allowlist(config, ["browser_click"])
    tool_call = ToolCall(
        id="blocked-call",
        type="function",
        name=blocked_name,
        arguments="{}",
    )

    with pytest.raises(AbilityExecutionError, match="browser_mouse_click_xy.*not allowed"):
        await manager._execute_single_tool_call(tool_call, MagicMock())


@pytest.mark.asyncio
async def test_allowlist_is_scoped_to_one_mcp_server() -> None:
    playwright = _server()
    other = _server(server_id="other-server", server_name="other")
    manager = AbilityManager()
    manager.add([playwright, other])
    manager.set_mcp_tool_allowlist(playwright, ["browser_click"])

    async def get_infos(*, server_id: str) -> list[ToolInfo]:
        del server_id
        return _tool_infos()

    with patch.object(
        Runner.resource_mgr,
        "get_mcp_tool_infos",
        new=AsyncMock(side_effect=get_infos),
    ):
        visible = await manager.list_tool_info()

    visible_names = {tool.name for tool in visible}
    assert "mcp_playwright-official_browser_click" in visible_names
    assert "mcp_playwright-official_browser_pdf_save" not in visible_names
    assert "mcp_other_browser_click" in visible_names
    assert "mcp_other_browser_pdf_save" in visible_names
    assert "mcp_other_browser_mouse_click_xy" in visible_names


@pytest.mark.asyncio
async def test_none_allowlist_preserves_legacy_unrestricted_visibility() -> None:
    config = _server()
    manager = AbilityManager()
    manager.add(config)
    manager.set_mcp_tool_allowlist(config, None)

    visible_names = await _visible_names(manager, _tool_infos())

    assert visible_names == {
        "mcp_playwright-official_browser_click",
        "mcp_playwright-official_browser_pdf_save",
        "mcp_playwright-official_browser_mouse_click_xy",
    }


@pytest.mark.asyncio
async def test_two_task_allowlists_share_server_identity_but_expose_different_tools() -> None:
    shared_config = _server()
    core_task = AbilityManager()
    pdf_task = AbilityManager()
    core_task.add(shared_config)
    pdf_task.add(shared_config)
    core_task.set_mcp_tool_allowlist(shared_config, ["browser_click"])
    pdf_task.set_mcp_tool_allowlist(shared_config, ["browser_click", "browser_pdf_save"])
    get_infos = AsyncMock(side_effect=lambda **_: _tool_infos())

    with patch.object(Runner.resource_mgr, "get_mcp_tool_infos", new=get_infos):
        core_visible = {tool.name for tool in await core_task.list_tool_info()}
        pdf_visible = {tool.name for tool in await pdf_task.list_tool_info()}

    assert shared_config.server_id == "playwright_official_stdio"
    assert {call.kwargs["server_id"] for call in get_infos.await_args_list} == {
        shared_config.server_id
    }
    assert "mcp_playwright-official_browser_pdf_save" not in core_visible
    assert "mcp_playwright-official_browser_pdf_save" in pdf_visible

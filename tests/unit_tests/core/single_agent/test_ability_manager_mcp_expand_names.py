# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for the enterprise-template MCP expansion path (path A).

Enterprise config.yaml registers static MCP servers via
``Runner.resource_mgr.add_mcp_server``; AbilityManager.list_tool_info expands
each server's tools as ``mcp_{server}_{tool}``. When the server/tool names
carry dots (or other characters OpenAI-compatible providers reject), the
exposed name must be sanitized — and must stay STABLE across repeated
list_tool_info calls, because the model sees one name per conversation turn.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from openjiuwen.core.foundation.tool import McpServerConfig, ToolInfo
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.ability_manager import AbilityManager


def _mcp_config(server_id: str, server_name: str) -> McpServerConfig:
    return McpServerConfig(
        server_id=server_id, server_name=server_name,
        server_path="stdio://mock", client_type="stdio",
    )


def _fake_infos(*names: str) -> list[ToolInfo]:
    return [ToolInfo(name=n, description=f"{n} desc", parameters={}) for n in names]


def test_expand_sanitizes_dotted_tool_name() -> None:
    async def _run():
        await Runner.start()
        am = AbilityManager()
        am.add(_mcp_config("svc1", "aippt"))
        try:
            with patch.object(
                Runner.resource_mgr, "get_mcp_tool_infos",
                AsyncMock(return_value=_fake_infos("doc.beautify", "search")),
            ):
                names = [i.name for i in await am.list_tool_info()]

            assert "mcp_aippt_doc_beautify" in names  # dotted tool sanitized
            assert "mcp_aippt_search" in names        # compliant tool unchanged
            assert "doc.beautify" not in names
            # Registered under the sanitized key with the RAW three-part id.
            card = am.get("mcp_aippt_doc_beautify")
            assert card is not None
            assert card.id == "svc1.aippt.doc.beautify"
            # Execution scope resolution still yields the RAW tool name.
            scope = am._resolve_mcp_tool_scope("mcp_aippt_doc_beautify")
            assert scope == ("svc1", "doc.beautify")
        finally:
            am.remove("aippt")
            await Runner.stop()
    asyncio.run(_run())


def test_expand_names_stable_across_repeated_calls() -> None:
    async def _run():
        await Runner.start()
        am = AbilityManager()
        am.add(_mcp_config("svc1", "aippt"))
        try:
            fake = _fake_infos("doc.beautify")
            with patch.object(
                Runner.resource_mgr, "get_mcp_tool_infos", AsyncMock(return_value=fake)
            ):
                first = [i.name for i in await am.list_tool_info()]
                second = [i.name for i in await am.list_tool_info()]
                third = [i.name for i in await am.list_tool_info()]

            assert first == second == third, f"name drifted: {first} vs {second} vs {third}"
            assert first == ["mcp_aippt_doc_beautify"]
        finally:
            am.remove("aippt")
            await Runner.stop()
    asyncio.run(_run())


def test_expand_handles_dotted_server_name() -> None:
    async def _run():
        await Runner.start()
        am = AbilityManager()
        am.add(_mcp_config("svc2", "my.server"))
        try:
            with patch.object(
                Runner.resource_mgr, "get_mcp_tool_infos",
                AsyncMock(return_value=_fake_infos("search")),
            ):
                names = [i.name for i in await am.list_tool_info()]

            assert "mcp_my_server_search" in names
            card = am.get("mcp_my_server_search")
            assert card is not None
            assert card.id == "svc2.my.server.search"
            scope = am._resolve_mcp_tool_scope("mcp_my_server_search")
            assert scope == ("svc2", "search")
        finally:
            am.remove("my.server")
            await Runner.stop()
    asyncio.run(_run())

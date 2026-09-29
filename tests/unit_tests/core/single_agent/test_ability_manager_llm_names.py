# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for AbilityManager LLM-safe tool name exposure and alias routing.

MCP connector tools may carry names that OpenAI-compatible providers reject
(``400 Invalid 'tools[N].function.name'``), e.g. ``aippt.doc_beautify`` from
dotted MCP naming. list_tool_info must expose only LLM-safe names while
execution keeps routing model tool_calls back to the raw registered keys.
"""

from __future__ import annotations

import asyncio
import re
from unittest.mock import AsyncMock, patch

from openjiuwen.core.foundation.llm import ToolCall
from openjiuwen.core.foundation.tool import LocalFunction, McpServerConfig, ToolCard, ToolInfo
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.ability_manager import AbilityManager
from openjiuwen.core.workflow import WorkflowCard


def _make_tool(name: str, *, tool_id: str | None = None) -> LocalFunction:
    card = ToolCard(id=tool_id or name, name=name, description=f"{name} desc")

    async def _func(**_):
        return "ok"

    return LocalFunction(card=card, func=_func)


def test_list_tool_info_sanitizes_unsafe_name_and_records_alias() -> None:
    async def _run():
        await Runner.start()
        am = AbilityManager()
        dotted = _make_tool("aippt.doc_beautify", tool_id="scope.金山文档.aippt.doc_beautify")
        plain = _make_tool("web_search")
        try:
            am.add_ability(dotted.card, dotted)
            am.add_ability(plain.card, plain)

            infos = {info.name: info for info in await am.list_tool_info()}

            # Dotted name is exposed in a sanitized, provider-safe form.
            assert "aippt_doc_beautify" in infos
            assert "aippt.doc_beautify" not in infos
            # Compliant names pass through untouched.
            assert "web_search" in infos
            # Alias maps sanitized name back to the registered raw key.
            assert am._llm_tool_aliases["aippt_doc_beautify"] == "aippt.doc_beautify"
            # Both raw and sanitized lookups resolve to the same card.
            assert am.get("aippt.doc_beautify") is dotted.card
            assert am.get("aippt_doc_beautify") is dotted.card
            assert am.get("web_search") is plain.card
        finally:
            am.remove_ability("aippt.doc_beautify")
            am.remove_ability("web_search")
            await Runner.stop()
    asyncio.run(_run())


def test_alias_cleanup_after_remove() -> None:
    async def _run():
        await Runner.start()
        am = AbilityManager()
        dotted = _make_tool("aippt.doc_beautify")
        try:
            am.add_ability(dotted.card, dotted)
            # Aliases are created by list_tool_info (the LLM exposure pass).
            exposed = [info.name for info in await am.list_tool_info()]
            assert exposed == ["aippt_doc_beautify"]
            assert am.get("aippt_doc_beautify") is dotted.card
        finally:
            am.remove_ability("aippt.doc_beautify")
            await Runner.stop()

        # Alias and raw key are both gone after removal.
        assert am.get("aippt.doc_beautify") is None
        assert am.get("aippt_doc_beautify") is None
        assert "aippt.doc_beautify" not in am._tools
        assert "aippt_doc_beautify" not in am._llm_tool_aliases
    asyncio.run(_run())


def test_sanitized_names_stay_unique_across_colliding_raw_names() -> None:
    async def _run():
        await Runner.start()
        am = AbilityManager()
        first = _make_tool("note.create")
        second = _make_tool("note_create")
        try:
            am.add_ability(first.card, first)
            am.add_ability(second.card, second)

            names = [info.name for info in await am.list_tool_info()]

            # Both tools survive with distinct, provider-safe names.
            assert len(names) == 2
            assert len(set(names)) == 2
            assert all(re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", name) for name in names)
            assert am.get("note.create") is first.card
            assert am.get("note_create") is second.card
        finally:
            am.remove_ability("note.create")
            am.remove_ability("note_create")
            await Runner.stop()
    asyncio.run(_run())


def test_mcp_server_tool_names_stable_across_list_tool_info_calls() -> None:
    """Regression: the MCP branch must not drift its own name per call.

    list_tool_info runs every ReAct round. The MCP branch writes its exposed
    names back into self._tools; seeding the sanitize taken-set with those
    self-produced keys made every later round treat its own previous name as
    a collision (digest branch), drifting the model-visible name per round
    and leaking one dead self._tools entry per round — for compliant
    ``mcp_{server}_{tool}`` names too, not only dotted ones.
    """

    async def _run():
        await Runner.start()
        am = AbilityManager()
        cfg = McpServerConfig(server_name="aippt-server", server_path="stdio://mock")
        try:
            am.add(cfg)
            dotted = ToolInfo(name="aippt.doc_beautify", description="d", parameters={})
            compliant = ToolInfo(name="doc_outline", description="d", parameters={})
            with patch.object(
                Runner.resource_mgr,
                "get_mcp_tool_infos",
                AsyncMock(return_value=[dotted, compliant]),
            ):
                names_per_call = [
                    [info.name for info in await am.list_tool_info()]
                    for _ in range(3)
                ]

            # Same model-visible names on every call — no per-round drift.
            assert names_per_call[0] == names_per_call[1] == names_per_call[2]
            assert names_per_call[0] == [
                "mcp_aippt-server_aippt_doc_beautify",
                "mcp_aippt-server_doc_outline",
            ]
            # self._tools re-derives and overwrites the same keys — no growth.
            assert sorted(am._tools.keys()) == [
                "mcp_aippt-server_aippt_doc_beautify",
                "mcp_aippt-server_doc_outline",
            ]
        finally:
            am.remove("aippt-server")
            await Runner.stop()
    asyncio.run(_run())


def test_raw_key_wins_over_stale_alias_in_routing() -> None:
    """A later registration under an alias name must win everywhere.

    dotted tool ``aippt.doc_beautify`` is exposed under alias
    ``aippt_doc_beautify``; a tool later registered with that exact raw key
    must win both in get() and in the execute() name resolution — the stale
    alias must not hijack the call to the old dotted tool.
    """

    async def _run():
        await Runner.start()
        am = AbilityManager()
        dotted = _make_tool("aippt.doc_beautify")
        plain = _make_tool("aippt_doc_beautify")
        try:
            am.add_ability(dotted.card, dotted)
            await am.list_tool_info()  # creates alias aippt_doc_beautify
            am.add_ability(plain.card, plain)  # later raw registration

            # get(): the raw key wins over the stale alias.
            assert am.get("aippt_doc_beautify") is plain.card

            # execute() name resolution: no alias rewrite, original kept.
            tc = ToolCall(id="call-1", type="function", name="aippt_doc_beautify", arguments="{}")
            resolved = am._resolve_model_tool_call_names([tc])
            assert resolved[0] is tc
            assert resolved[0].name == "aippt_doc_beautify"

            # Next exposure pass GCs the colliding alias and re-derives a
            # digest-disambiguated name for the dotted tool.
            visible = [info.name for info in await am.list_tool_info()]
            assert "aippt_doc_beautify" not in am._llm_tool_aliases
            assert "aippt.doc_beautify" not in visible
            assert any(
                raw == "aippt.doc_beautify" for raw in am._llm_tool_aliases.values()
            )
        finally:
            am.remove_ability("aippt.doc_beautify")
            am.remove_ability("aippt_doc_beautify")
            await Runner.stop()
    asyncio.run(_run())


def test_alias_resolution_does_not_mutate_caller_tool_calls() -> None:
    """History shares the caller's ToolCall objects; keep sanitized names.

    react_agent stores the assistant message (with the same tool_calls list)
    before passing it to execute(); rewriting in place would put the raw
    dotted name back into history and later turns would replay an illegal
    name to the provider.
    """

    async def _run():
        await Runner.start()
        am = AbilityManager()
        dotted = _make_tool("aippt.doc_beautify")
        try:
            am.add_ability(dotted.card, dotted)
            await am.list_tool_info()

            tc = ToolCall(id="call-1", type="function", name="aippt_doc_beautify", arguments="{}")
            resolved = am._resolve_model_tool_call_names([tc])

            # Caller/history object keeps the model-visible sanitized name.
            assert tc.name == "aippt_doc_beautify"
            # The execution copy carries the raw key and is a distinct object.
            assert resolved[0].name == "aippt.doc_beautify"
            assert resolved[0] is not tc
        finally:
            am.remove_ability("aippt.doc_beautify")
            await Runner.stop()
    asyncio.run(_run())


def test_workflow_name_sanitized_and_alias_routed() -> None:
    """Workflows (and agents) get the same sanitize+alias treatment as tools."""

    async def _run():
        await Runner.start()
        am = AbilityManager()
        card = WorkflowCard(id="wf-1", name="report.generate", description="d", input_params={})
        try:
            am.add(card)

            visible = [info.name for info in await am.list_tool_info()]

            assert visible == ["report_generate"]
            assert am._llm_tool_aliases["report_generate"] == "report.generate"
            # get() resolves the sanitized name back to the workflow card.
            assert am.get("report_generate") is card
            # execute() name resolution routes the sanitized name to the raw key.
            tc = ToolCall(id="call-1", type="function", name="report_generate", arguments="{}")
            assert am._resolve_model_tool_call_names([tc])[0].name == "report.generate"
            # Raw key lookups keep working.
            assert am.get("report.generate") is card
        finally:
            am.remove("report.generate")
            await Runner.stop()
    asyncio.run(_run())


def test_mcp_server_expansion_sanitizes_dotted_names() -> None:
    """Enterprise path: McpServerConfig expansion (mcp_{server}_{tool})."""
    from unittest.mock import AsyncMock, patch

    from openjiuwen.core.foundation.tool import McpServerConfig, ToolInfo

    async def _run():
        await Runner.start()
        am = AbilityManager()
        # Server name with CJK + a tool with dots: both illegal for OpenAI
        # function calling; the expansion name must come out sanitized while
        # the card id keeps the raw segments for MCP routing.
        config = McpServerConfig(
            server_id="svc-e2e",
            server_name="金山文档",
            server_path="stdio://mock",
            client_type="stdio",
        )
        tool_infos = [
            ToolInfo(name="aippt.doc_beautify", description="d", parameters={}),
            ToolInfo(name="web_search_mock", description="d", parameters={}),
        ]
        try:
            am.add(config)
            with patch.object(
                Runner.resource_mgr,
                "get_mcp_tool_infos",
                new=AsyncMock(return_value=tool_infos),
            ):
                visible = [info.name for info in await am.list_tool_info()]

            assert all(re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", name) for name in visible)
            # Dotted tool name sanitized; compliant tool keeps the prefixed form.
            assert "mcp_aippt_doc_beautify" in visible
            assert "mcp_金山文档_web_search_mock" not in visible
            assert "mcp_web_search_mock" in visible
            # Registered under the sanitized key; id keeps raw server/tool names.
            card = am.get("mcp_aippt_doc_beautify")
            assert card is not None
            assert card.id == "svc-e2e.金山文档.aippt.doc_beautify"
            # MCP scope resolution maps the sanitized model name back to the
            # raw underlying tool name (allowlist enforcement input).
            scope = am._resolve_mcp_tool_scope("mcp_aippt_doc_beautify")
            assert scope == ("svc-e2e", "aippt.doc_beautify")
        finally:
            am.remove("金山文档")
            await Runner.stop()
    asyncio.run(_run())

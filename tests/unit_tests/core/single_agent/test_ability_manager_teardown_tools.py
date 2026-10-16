# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for AbilityManager.teardown_tools owner-domain scoping.

dev-review CR-001: the owner-tag sweep must stay in the ``ability-owner:``
namespace. The bare owner id equals ``card.id`` by default, which is also the
tag domain DeepAgent._register_pending_mcps uses for the MCP server and every
one of its tools — a bare-tag sweep would rip shared/cross-cycle MCP tools out
of the process-global registry with no re-registration path
(``refresh_mcp_server`` is an empty stub).
"""

from __future__ import annotations

import asyncio

from openjiuwen.core.foundation.tool import LocalFunction, ToolCard
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.ability_manager import AbilityManager


def _make_tool(name: str, *, stateless: bool = False, marker: str = "x") -> LocalFunction:
    card = ToolCard(id=name, name=name, description=f"{name} desc", stateless=stateless)

    async def _func(**_):
        return marker

    return LocalFunction(card=card, func=_func)


def test_stateful_add_ability_tags_namespaced_owner_domain() -> None:
    """add_ability must tag with ability-owner:{owner}, never the bare owner id."""

    async def _run():
        await Runner.start()
        am = AbilityManager(owner_id="card-1")
        tool = _make_tool("calc")
        try:
            am.add_ability(tool.card, tool)

            namespaced = Runner.resource_mgr.get_resource_by_tag("ability-owner:card-1")
            assert tool.card in namespaced
            # The card.id tag domain (used by MCP registration) stays untouched.
            assert not Runner.resource_mgr.get_resource_by_tag("card-1")
        finally:
            am.teardown_tools()
            await Runner.stop()

    asyncio.run(_run())


def test_teardown_tools_sweeps_only_own_owner_domain() -> None:
    """A teardown must not affect another owner's registrations."""

    async def _run():
        await Runner.start()
        am_a = AbilityManager(owner_id="agent-a")
        am_b = AbilityManager(owner_id="agent-b")
        tool_a = _make_tool("greet", marker="a")
        tool_b = _make_tool("greet", marker="b")
        try:
            am_a.add_ability(tool_a.card, tool_a)
            am_b.add_ability(tool_b.card, tool_b)

            am_a.teardown_tools()

            assert Runner.resource_mgr.get_tool("greet_agent-a") is None
            assert Runner.resource_mgr.get_tool("greet_agent-b") is tool_b
        finally:
            am_b.teardown_tools()
            await Runner.stop()

    asyncio.run(_run())


def test_teardown_tools_preserves_card_id_tag_domain() -> None:
    """CR-001: tools in the card.id tag domain (MCP registration shape) survive.

    DeepAgent._register_pending_mcps registers the MCP server and each of its
    tools with ``tag=self.card.id``, and the default ability owner is the same
    ``card.id`` — the sweep must therefore not touch that domain.
    """

    async def _run():
        await Runner.start()
        # Owner equals the MCP-registration tag value, as in the default config.
        am = AbilityManager(owner_id="card-9")
        owned = _make_tool("greet")
        mcp_tool = _make_tool("mcp_lookup")
        mcp_tool.card.id = "srv1.mcp_lookup"
        Runner.resource_mgr.add_tool(mcp_tool, tag="card-9")
        try:
            am.add_ability(owned.card, owned)

            am.teardown_tools()

            # Ability-owned stateful tool is gone (exact id + namespaced sweep).
            assert Runner.resource_mgr.get_tool("greet_card-9") is None
            # The card.id-tagged (MCP-domain) tool survives and stays resolvable.
            assert Runner.resource_mgr.get_tool("srv1.mcp_lookup") is mcp_tool
            assert Runner.resource_mgr.get_resource_by_tag("card-9") == [mcp_tool.card]
        finally:
            Runner.resource_mgr.remove_tool("srv1.mcp_lookup")
            await Runner.stop()

    asyncio.run(_run())

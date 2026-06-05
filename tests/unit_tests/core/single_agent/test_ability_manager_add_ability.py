# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for AbilityManager.add_ability / remove_ability id qualification."""

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


def test_stateful_add_ability_qualifies_id_and_resolves() -> None:
    async def _run():
        await Runner.start()
        am = AbilityManager(owner_id="agent-1")
        tool = _make_tool("greet")
        try:
            result = am.add_ability(tool.card, tool)

            assert result.added is True
            # The card id is rewritten to ``<name>_<owner>`` and stays consistent
            # between the ability manager and the resource manager.
            assert tool.card.id == "greet_agent-1"
            assert am.get("greet").id == "greet_agent-1"
            assert Runner.resource_mgr.get_tool("greet_agent-1") is tool
        finally:
            am.remove_ability("greet")
            await Runner.stop()

    asyncio.run(_run())


def test_stateful_add_ability_refreshes_on_same_id() -> None:
    async def _run():
        await Runner.start()
        am = AbilityManager(owner_id="agent-2")
        first = _make_tool("calc", marker="first")
        second = _make_tool("calc", marker="second")
        try:
            am.add_ability(first.card, first)
            # A second instance under the same name+owner rebinds (refresh), no raise.
            am.add_ability(second.card, second)

            assert Runner.resource_mgr.get_tool("calc_agent-2") is second
        finally:
            am.remove_ability("calc")
            await Runner.stop()

    asyncio.run(_run())


def test_stateless_add_ability_keeps_bare_id_and_is_shared() -> None:
    async def _run():
        await Runner.start()
        am1 = AbilityManager(owner_id="agent-a")
        am2 = AbilityManager(owner_id="agent-b")
        shared = _make_tool("clock", stateless=True)
        try:
            am1.add_ability(shared.card, shared)
            # Second owner registering the same stateless singleton is a no-op
            # skip; the bare id is never qualified.
            am2.add_ability(shared.card, shared)

            assert shared.card.id == "clock"
            assert am1.get("clock").id == "clock"
            assert am2.get("clock").id == "clock"
            assert Runner.resource_mgr.get_tool("clock") is shared

            # Removing from one owner leaves the shared registration for the other.
            am1.remove_ability("clock")
            assert am1.get("clock") is None
            assert Runner.resource_mgr.get_tool("clock") is shared
        finally:
            Runner.resource_mgr.remove_tool("clock")
            await Runner.stop()

    asyncio.run(_run())

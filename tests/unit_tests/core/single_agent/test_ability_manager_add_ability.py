# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for AbilityManager.add_ability / remove_ability id qualification."""

from __future__ import annotations

import asyncio
import json

from openjiuwen.core.foundation.llm import ToolCall
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


def test_teardown_tools_removes_only_owned_stateful_tools() -> None:
    async def _run():
        await Runner.start()
        am = AbilityManager(owner_id="agent-3")
        stateful = _make_tool("write_file")
        shared = _make_tool("clock", stateless=True)
        # An externally-scoped tool the manager did not qualify (e.g. MCP id).
        external = _make_tool("ext")
        try:
            am.add_ability(stateful.card, stateful)
            am.add_ability(shared.card, shared)
            am.add(external.card)  # registered as a bare reference, id stays "ext"

            am.teardown_tools()

            # Owned stateful tool is dropped from both the manager and resource_mgr.
            assert am.get("write_file") is None
            assert Runner.resource_mgr.get_tool("write_file_agent-3") is None
            # Stateless shared tool and the non-qualified reference are left intact.
            assert am.get("clock").id == "clock"
            assert Runner.resource_mgr.get_tool("clock") is shared
            assert am.get("ext").id == "ext"
        finally:
            Runner.resource_mgr.remove_tool("clock")
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


def _file_call(call_id: str, name: str, file_path: str) -> ToolCall:
    return ToolCall(
        id=call_id,
        type="function",
        name=name,
        arguments=json.dumps({"file_path": file_path}),
    )


def test_parallel_file_calls_serialize_same_path_and_keep_result_order() -> None:
    async def _run():
        calls = [
            _file_call("c1", "edit_file", "config/settings.json"),
            _file_call("c2", "edit_file", "config/settings.json"),
        ]
        first_entered = asyncio.Event()
        release_first = asyncio.Event()
        second_entered = asyncio.Event()

        async def first():
            first_entered.set()
            await release_first.wait()
            return "first"

        async def second():
            second_entered.set()
            return "second"

        run_task = asyncio.create_task(
            AbilityManager._execute_parallel_tool_tasks(calls, [first(), second()])
        )
        await first_entered.wait()
        await asyncio.sleep(0)
        assert not second_entered.is_set()

        release_first.set()
        assert await run_task == ["first", "second"]
        assert second_entered.is_set()

    asyncio.run(_run())


def test_parallel_file_calls_keep_different_paths_concurrent() -> None:
    async def _run():
        calls = [
            _file_call("c1", "edit_file", "config/database.yml"),
            _file_call("c2", "edit_file", "config/settings.json"),
        ]
        both_entered = asyncio.Event()
        entered = 0

        async def edit(marker: str):
            nonlocal entered
            entered += 1
            if entered == 2:
                both_entered.set()
            await asyncio.wait_for(both_entered.wait(), timeout=1)
            return marker

        results = await AbilityManager._execute_parallel_tool_tasks(
            calls,
            [edit("database"), edit("settings")],
        )
        assert results == ["database", "settings"]

    asyncio.run(_run())


def test_parallel_safe_tool_calls_run_concurrently() -> None:
    async def _run():
        calls = [
            ToolCall(id="c1", type="function", name="read_file", arguments="{}"),
            ToolCall(id="c2", type="function", name="grep", arguments="{}"),
        ]
        cards = {
            "read_file": ToolCard(id="read_file", name="read_file", parallel_safe=True),
            "grep": ToolCard(id="grep", name="grep", parallel_safe=True),
        }
        both_entered = asyncio.Event()
        entered = 0

        async def tool(marker: str):
            nonlocal entered
            entered += 1
            if entered == 2:
                both_entered.set()
            await asyncio.wait_for(both_entered.wait(), timeout=1)
            return marker

        results = await AbilityManager._execute_parallel_tool_tasks(
            calls,
            [tool("read"), tool("grep")],
            tool_cards=cards,
        )
        assert results == ["read", "grep"]

    asyncio.run(_run())


def test_non_parallel_safe_tool_call_runs_as_exclusive_barrier() -> None:
    async def _run():
        calls = [
            ToolCall(id="c1", type="function", name="read_file", arguments="{}"),
            ToolCall(id="c2", type="function", name="write_file", arguments="{}"),
            ToolCall(id="c3", type="function", name="grep", arguments="{}"),
        ]
        cards = {
            "read_file": ToolCard(id="read_file", name="read_file", parallel_safe=True),
            "write_file": ToolCard(id="write_file", name="write_file", parallel_safe=False),
            "grep": ToolCard(id="grep", name="grep", parallel_safe=True),
        }
        order = []
        read_started = asyncio.Event()
        release_read = asyncio.Event()

        async def read():
            order.append("read:start")
            read_started.set()
            await release_read.wait()
            order.append("read:end")
            return "read"

        async def write():
            order.append("write")
            return "write"

        async def grep():
            order.append("grep")
            return "grep"

        run_task = asyncio.create_task(
            AbilityManager._execute_parallel_tool_tasks(
                calls,
                [read(), write(), grep()],
                tool_cards=cards,
            )
        )
        await asyncio.wait_for(read_started.wait(), timeout=1)
        assert order == ["read:start"]

        release_read.set()
        assert await run_task == ["read", "write", "grep"]
        assert order == ["read:start", "read:end", "write", "grep"]

    asyncio.run(_run())


def test_read_and_edit_equivalent_paths_share_one_execution_lane(tmp_path) -> None:
    direct_path = tmp_path / "config" / "settings.json"
    equivalent_path = direct_path.parent / ".." / "config" / "settings.json"
    read_call = _file_call("c1", "read_file", str(direct_path))
    edit_call = _file_call("c2", "edit_file", str(equivalent_path))

    assert (
        AbilityManager._tool_execution_resource_key(read_call)
        == AbilityManager._tool_execution_resource_key(edit_call)
    )

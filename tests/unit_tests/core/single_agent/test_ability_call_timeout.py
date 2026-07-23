# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for AbilityManager call-level tool timeout.

Verifies that ``AbilityManager._execute_single_tool_call`` wraps
``tool.invoke`` in ``anyio.fail_after`` so a hanging tool cannot block the
task loop indefinitely.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict

import pytest

from openjiuwen.core.foundation.llm import ToolCall
from openjiuwen.core.foundation.tool import LocalFunction, ToolCard
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent import ability_manager as am_mod
from openjiuwen.core.single_agent.ability_manager import AbilityManager
from openjiuwen.core.single_agent.ability_manager import AbilityExecutionError
from openjiuwen.core.sys_operation import (
    LocalWorkConfig,
    OperationMode,
    SysOperation,
    SysOperationCard,
)


def _tool_card(
        name: str,
        *,
        timeout_s: Any = "unset",
        stateless: bool = False,
) -> ToolCard:
    """Build a ToolCard carrying a ``resilience.timeout_s`` property.

    ``timeout_s="unset"`` means the resilience block is omitted entirely,
    so the manager falls back to ``DEFAULT_TOOL_CALL_TIMEOUT``.
    """
    properties: Dict[str, Any] = {}
    if timeout_s != "unset":
        properties["resilience"] = {"timeout_s": timeout_s}
    return ToolCard(
        id=name,
        name=name,
        description=f"{name} desc",
        stateless=stateless,
        properties=properties,
    )


def _make_hang_tool(name: str, *, timeout_s: Any = "unset") -> LocalFunction:
    """A tool whose invoke awaits an Event that is never set."""
    card = _tool_card(name, timeout_s=timeout_s)

    async def _func(**_):  # noqa: ANN202
        await asyncio.Event().wait()  # never set → hangs forever
        return "unreachable"

    return LocalFunction(card=card, func=_func)


def _make_fast_tool(name: str, *, timeout_s: Any = "unset", delay: float = 0.0) -> LocalFunction:
    """A tool that returns a marker after an optional short delay."""
    card = _tool_card(name, timeout_s=timeout_s)

    async def _func(**_):  # noqa: ANN202
        if delay:
            await asyncio.sleep(delay)
        return f"{name}:ok"

    return LocalFunction(card=card, func=_func)


def _tool_call(name: str, *, args: str = "{}") -> ToolCall:
    return ToolCall(id=f"tc-{name}", type="function", name=name, arguments=args)


def _run_async(coro):
    return asyncio.run(coro)


# Lazily-imported real tool classes. Construction is deferred so importing this
# test module does not pull the full harness tool tree unless a test that
# needs a real card actually runs.
_REAL_TOOL_CLS = {
    "read_file": ("openjiuwen.harness.tools.filesystem", "ReadFileTool"),
    "write_file": ("openjiuwen.harness.tools.filesystem", "WriteFileTool"),
    "edit_file": ("openjiuwen.harness.tools.filesystem", "EditFileTool"),
    "glob": ("openjiuwen.harness.tools.filesystem", "GlobTool"),
    "list_files": ("openjiuwen.harness.tools.filesystem", "ListDirTool"),
    "grep": ("openjiuwen.harness.tools.filesystem", "GrepTool"),
    "bash": ("openjiuwen.harness.tools.shell.bash._tool", "BashTool"),
    "powershell": ("openjiuwen.harness.tools.shell.powershell._tool", "PowerShellTool"),
    "code": ("openjiuwen.harness.tools.code", "CodeTool"),
    "free_search": ("openjiuwen.harness.tools.web.free_search", "WebFreeSearchTool"),
    "fetch_webpage": ("openjiuwen.harness.tools.web.fetch_webpage", "WebFetchWebpageTool"),
    "lsp": ("openjiuwen.harness.tools.lsp_tool._tool", "LspTool"),
}


def _make_sys_operation() -> SysOperation:
    """Build a minimal LOCAL SysOperation for tools that need one to construct."""
    return SysOperation(
        SysOperationCard(
            id="ability-timeout-test",
            mode=OperationMode.LOCAL,
            work_config=LocalWorkConfig(shell_allowlist=None, restrict_to_sandbox=False),
        )
    )


def _real_tool_card(expected_name: str, *, timeout_s: Any = "unset") -> ToolCard:
    """Return the real ToolCard from an *instantiated* system tool.

    Constructs the actual tool subclass registered in the codebase and returns
    its ``card`` (with an optional ``resilience.timeout_s`` overlay), so the
    test gates on the tool's true ``card.name`` — a drift between the real
    name and the rail/inventory would surface here instead of a stale string.

    Only the lightweight-tools path (free_search / fetch_webpage / lsp) skips
    the SysOperation; the rest receive a shared LOCAL SysOperation. No
    Runner.start() is needed: Tool construction only sets up the lifecycle
    invoke wrapper, it does not fire events.
    """
    import importlib

    if expected_name not in _REAL_TOOL_CLS:
        raise KeyError(f"no real tool mapping for {expected_name!r}")
    module_name, cls_name = _REAL_TOOL_CLS[expected_name]
    cls = getattr(importlib.import_module(module_name), cls_name)

    if expected_name in ("free_search", "fetch_webpage"):
        tool = cls()
    elif expected_name == "lsp":
        tool = cls(operation=None)
    else:
        tool = cls(operation=_make_sys_operation())

    card = tool.card
    assert card.name == expected_name, (
        f"real {cls_name}.card.name is {card.name!r}, expected {expected_name!r} "
        f"— the test inventory is stale"
    )
    if timeout_s != "unset":
        # Overlay a declared timeout on the real card for tests that need to
        # prove the name exemption *overrides* a declared value.
        card.properties = {**card.properties, "resilience": {"timeout_s": timeout_s}}
    return card


def _real_tool_name(expected_name: str) -> str:
    """Return ``card.name`` from an instantiated real tool (for the fallback
    ``tool_name`` kwarg path, where a card is not available)."""
    return _real_tool_card(expected_name).name


def test_resolve_call_timeout_reads_resilience_property() -> None:
    # Declared positive value is used verbatim.
    assert AbilityManager._resolve_call_timeout(_tool_card("t", timeout_s=42.0)) == 42.0
    # Declared None → exempt (no outer timeout).
    assert AbilityManager._resolve_call_timeout(_tool_card("t", timeout_s=None)) is None
    # Non-positive → exempt.
    assert AbilityManager._resolve_call_timeout(_tool_card("t", timeout_s=0)) is None
    assert AbilityManager._resolve_call_timeout(_tool_card("t", timeout_s=-1)) is None
    # Unset resilience block → default.
    assert AbilityManager._resolve_call_timeout(_tool_card("t", timeout_s="unset")) == am_mod.DEFAULT_TOOL_CALL_TIMEOUT
    # No card at all (fallback path) → default.
    assert AbilityManager._resolve_call_timeout(None) == am_mod.DEFAULT_TOOL_CALL_TIMEOUT


def test_resolve_call_timeout_garbage_value_falls_back_to_default() -> None:
    # Non-numeric declared value must not crash; fall back to default.
    card = ToolCard(
        id="t", name="t", description="d",
        properties={"resilience": {"timeout_s": "not-a-number"}},
    )
    assert AbilityManager._resolve_call_timeout(card) == am_mod.DEFAULT_TOOL_CALL_TIMEOUT


def test_resolve_call_timeout_exempts_non_idempotent_by_name() -> None:
    """Layer 0: a non-idempotent tool is exempt by *name* regardless of any
    declared timeout_s, so the outer fail_after never fires on a side-
    effecting tool (its timeout would feed the rail a retryable marker and
    re-run the side effect). Returns None → anyio.fail_after(None) is a no-op.

    Uses the real ``WriteFileTool`` / ``BashTool`` cards so the exemption
    gates on the tools' actual ``card.name``, not a stale string.
    """
    # Real WriteFileTool card, overlaid with a declared timeout_s the name
    # exemption must override.
    card = _real_tool_card("write_file", timeout_s=10.0)
    assert AbilityManager._resolve_call_timeout(card) is None

    # Real BashTool card with no resilience block at all.
    assert AbilityManager._resolve_call_timeout(_real_tool_card("bash")) is None


def test_resolve_call_timeout_exempts_non_idempotent_via_tool_name_kwarg() -> None:
    """The fallback execution path has no registered card, so the name
    exemption is reached via the ``tool_name`` kwarg instead of card.name.

    ``tool_name`` is taken from the real EditFileTool / PowerShellTool cards.
    """
    assert (
        AbilityManager._resolve_call_timeout(None, tool_name=_real_tool_name("edit_file"))
        is None
    )
    assert (
        AbilityManager._resolve_call_timeout(None, tool_name=_real_tool_name("powershell"))
        is None
    )
    # A real idempotent tool (ReadFileTool) on the fallback path still gets
    # the default timeout.
    assert (
        AbilityManager._resolve_call_timeout(None, tool_name=_real_tool_name("read_file"))
        == am_mod.DEFAULT_TOOL_CALL_TIMEOUT
    )


def test_resolve_call_timeout_idempotent_tool_still_uses_property() -> None:
    """Layer 0 must not over-reach: an idempotent tool's declared timeout_s
    is still honored (regression guard against the name check swallowing
    tools that legitimately want a timeout).

    Uses the real FreeSearchTool / GlobTool cards.
    """
    # Real FreeSearchTool card, overlaid with a declared 42s timeout that must
    # be honored (free_search is not in the non-idempotent set).
    card = _real_tool_card("free_search", timeout_s=42.0)
    assert AbilityManager._resolve_call_timeout(card) == 42.0
    # Real GlobTool card with no resilience block → default.
    assert (
        AbilityManager._resolve_call_timeout(_real_tool_card("glob"))
        == am_mod.DEFAULT_TOOL_CALL_TIMEOUT
    )


def test_hanging_tool_times_out_via_explicit_property() -> None:
    async def _run():
        await Runner.start()
        am = AbilityManager(owner_id="to-1")
        tool = _make_hang_tool("hang", timeout_s=0.2)
        try:
            am.add_ability(tool.card, tool)
            tc = _tool_call("hang")

            t0 = time.monotonic()
            with pytest.raises(AbilityExecutionError, match="timed out"):
                # Outer wait_for is a scaffolding guard only; the SUT's own
                # 0.2s fail_after is what must break the hang.
                await asyncio.wait_for(
                    am._execute_single_tool_call(tc, session=None),
                    timeout=2.0,
                )
            elapsed = time.monotonic() - t0
            assert elapsed < 1.0, f"timeout did not fire promptly: {elapsed:.2f}s"
        finally:
            am.remove_ability("hang")
            await Runner.stop()

    _run_async(_run())


def test_default_timeout_used_when_no_resilience_property(monkeypatch) -> None:
    monkeypatch.setattr(am_mod, "DEFAULT_TOOL_CALL_TIMEOUT", 0.2)

    async def _run():
        await Runner.start()
        am = AbilityManager(owner_id="to-2")
        # No resilience property → falls back to (monkeypatched) default 0.2s.
        tool = _make_hang_tool("hang_default")
        try:
            am.add_ability(tool.card, tool)
            tc = _tool_call("hang_default")

            t0 = time.monotonic()
            with pytest.raises(AbilityExecutionError, match="timed out"):
                await asyncio.wait_for(
                    am._execute_single_tool_call(tc, session=None),
                    timeout=2.0,
                )
            elapsed = time.monotonic() - t0
            assert elapsed < 1.0
        finally:
            am.remove_ability("hang_default")
            await Runner.stop()

    _run_async(_run())


def test_fast_tool_unaffected_by_short_timeout() -> None:
    async def _run():
        await Runner.start()
        am = AbilityManager(owner_id="to-4")
        # Short 0.2s budget but the tool returns in 0.01s → must succeed.
        tool = _make_fast_tool("quick", timeout_s=0.2, delay=0.01)
        try:
            am.add_ability(tool.card, tool)
            tc = _tool_call("quick")
            result, _ = await asyncio.wait_for(
                am._execute_single_tool_call(tc, session=None),
                timeout=2.0,
            )
            assert result == "quick:ok"
        finally:
            am.remove_ability("quick")
            await Runner.stop()

    _run_async(_run())


def test_timeout_error_carries_tool_message_for_llm() -> None:
    """The ``AbilityExecutionError`` raised on timeout must carry a
    ``tool_message`` (content says 'timed out', correct tool_call_id), so
    ``execute()``'s existing error->ToolMessage rewrite path can hand the
    LLM a self-correcting message instead of leaving the round stuck.
    """

    async def _run():
        await Runner.start()
        am = AbilityManager(owner_id="to-5")
        tool = _make_hang_tool("hang_e2e", timeout_s=0.5)
        try:
            am.add_ability(tool.card, tool)
            tc = _tool_call("hang_e2e")
            raised: AbilityExecutionError | None = None
            try:
                await asyncio.wait_for(
                    am._execute_single_tool_call(tc, session=None),
                    timeout=2.0,
                )
            except AbilityExecutionError as e:
                raised = e
            assert raised is not None
            # the timeout error carries a tool_message that
            # execute()'s rewrite path will return to the LLM.
            assert raised.tool_message is not None
            assert "timed out" in str(raised.tool_message.content)
            assert raised.tool_message.tool_call_id == "tc-hang_e2e"
        finally:
            am.remove_ability("hang_e2e")
            await Runner.stop()

    _run_async(_run())

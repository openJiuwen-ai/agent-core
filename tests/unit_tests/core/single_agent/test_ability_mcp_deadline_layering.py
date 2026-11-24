# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Regression tests for the MCP deadline layering through ``AbilityManager``.

Issue #1976 review: the outer ``anyio.fail_after`` used to be armed with the
very budget it forwards to the MCP client's ``asyncio.timeout``. Having
started earlier with an identical budget, the outer ceiling always expired
first and cancelled the client-side wait, so the caller received only the
generic ``Tool 'x' timed out after Ns`` instead of the client's precise error,
and the documented "client raises the precise error, outer layer is only a
hard ceiling" contract did not hold.

These tests drive the real ``MCPTool`` through
``AbilityManager._execute_single_tool_call`` with a fake client that applies
the forwarded budget the way ``StdioClient.call_tool`` does.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Optional

import pytest

from openjiuwen.core.foundation.llm import ToolCall
from openjiuwen.core.foundation.tool import McpToolCard, ToolCard
from openjiuwen.core.foundation.tool.mcp.base import MCPTool
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent import ability_manager as am_mod
from openjiuwen.core.single_agent.ability_manager import AbilityExecutionError, AbilityManager

_CARD_TIMEOUT_SECONDS = 0.3
#: Budget an exempt tool (``timeout_s: null``) falls back to in these tests.
_HARD_LIMIT_SECONDS = 0.3
#: Slack a fake session takes to unwind after being cancelled.
_UNWIND_SECONDS = 0.15


class _HangingSession:
    """Fake MCP session whose tool call never returns."""

    async def call_tool(self, tool_name: str, arguments: Optional[dict] = None, **_kwargs: Any) -> Any:
        await asyncio.sleep(3600)
        raise AssertionError("unreachable")


class _SlowUnwindingSession:
    """Fake session that keeps cleaning up for a while after cancellation.

    Mirrors a real transport whose ``__aexit__`` drains a pipe or closes a
    stream before the cancelled call actually finishes: the client-side
    deadline has expired, but that cleanup must not be cut off by the outer
    scope.
    """

    async def call_tool(self, tool_name: str, arguments: Optional[dict] = None, **_kwargs: Any) -> Any:
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            # Cleanup that survives the cancellation it belongs to. Run it in a
            # helper task and shield it: awaiting a bare sleep here would be
            # interrupted again by the pending cancellation.
            cleanup = asyncio.ensure_future(asyncio.sleep(_UNWIND_SECONDS))
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                await cleanup
            raise
        raise AssertionError("unreachable")


class _BoundaryRecordingClient:
    """Fake MCP client with the ``StdioClient.call_tool`` timeout contract.

    Records the budget the manager forwarded and which deadline expired. An
    ``asyncio.timeout`` context turns its own cancellation into a
    ``TimeoutError``; a cancellation owned by an outer anyio scope surfaces
    as ``CancelledError``. Which one is observed tells the two deadlines
    apart.
    """

    __client_name__ = "boundary-recording"

    def __init__(self, session: Any, *, timeout_message: Optional[str]) -> None:
        self._session = session
        self._timeout_message = timeout_message
        self.forwarded_timeout: Optional[float] = None
        self.expired: Optional[str] = None

    async def call_tool(self, tool_name, arguments, *, timeout: float = -1) -> Any:  # noqa: ASYNC109 -- client API
        self.forwarded_timeout = timeout
        try:
            async with asyncio.timeout(timeout):
                await self._session.call_tool(tool_name, arguments=arguments)
        except TimeoutError:
            self.expired = "client"
            if self._timeout_message is None:
                raise
            raise TimeoutError(self._timeout_message.format(budget=timeout)) from None
        except asyncio.CancelledError:
            self.expired = "outer"
            raise
        raise AssertionError("unreachable")


def _mcp_tool(client: Any, *, timeout_s: Any = _CARD_TIMEOUT_SECONDS) -> MCPTool:
    properties: Dict[str, Any] = {"resilience": {"timeout_s": timeout_s}}
    return MCPTool(
        client,
        McpToolCard(
            id="solve",
            name="solve",
            description="",
            input_params={"type": "object", "properties": {}},
            server_name="sim",
            properties=properties,
        ),
    )


def _tool_call() -> ToolCall:
    return ToolCall(id="tc-solve", type="function", name="solve", arguments="{}")


async def _execute(client: Any, *, registered: bool, timeout_s: Any = _CARD_TIMEOUT_SECONDS) -> Any:
    """Run one MCP tool call through the manager and return what it raised."""
    await Runner.start()
    manager = AbilityManager(owner_id="deadline-test")
    tool = _mcp_tool(client, timeout_s=timeout_s)
    original_get_tool = Runner.resource_mgr.get_tool
    Runner.resource_mgr.get_tool = lambda tool_id=None, **_kwargs: tool  # type: ignore[method-assign]
    try:
        if registered:
            manager.add_ability(tool.card, tool)
        try:
            await asyncio.wait_for(
                manager._execute_single_tool_call(_tool_call(), session=None),  # noqa: SLF001
                timeout=30.0,
            )
        except BaseException as exc:  # noqa: BLE001 - the raised object is the test subject
            return exc
        raise AssertionError("the hanging MCP call must not succeed")
    finally:
        Runner.resource_mgr.get_tool = original_get_tool  # type: ignore[method-assign]
        if registered:
            manager.remove_ability("solve")
        await Runner.stop()


def _timeout_text(raised: Any) -> str:
    """The model-facing text of an ``AbilityExecutionError`` raised on timeout."""
    assert isinstance(raised, AbilityExecutionError), f"unexpected exception: {raised!r}"
    assert raised.tool_message is not None
    return str(raised.tool_message.content)


def _raise_grace_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the added grace clearly exceed the session's unwinding time.

    The property under test (the client deadline wins) must not depend on the
    default grace size, so the tests pin the floor explicitly.

    ``raising=False`` keeps a pre-fix tree (no such constant yet) failing on
    behaviour rather than on the monkeypatch itself.
    """
    monkeypatch.setattr(am_mod, "MCP_OUTER_TIMEOUT_GRACE_MIN_SECONDS", 1.0, raising=False)


_CLIENT_MESSAGE = "stdio call_tool 'solve' did not complete within {budget:g}s"


@pytest.mark.parametrize("registered", [True, False], ids=["registered", "resource_mgr_fallback"])
def test_client_deadline_expires_before_outer_ceiling(monkeypatch, registered: bool) -> None:
    """The forwarded budget is enforced by the client, not by the outer ceiling.

    A session that unwinds slowly after cancellation must still reach the
    client's own ``TimeoutError``: the outer ``fail_after`` has to outlast
    the client-side deadline plus the transport's cleanup.
    """
    _raise_grace_floor(monkeypatch)
    client = _BoundaryRecordingClient(_SlowUnwindingSession(), timeout_message=_CLIENT_MESSAGE)

    async def _run() -> Any:
        return await _execute(client, registered=registered)

    asyncio.run(_run())

    assert client.forwarded_timeout == _CARD_TIMEOUT_SECONDS
    assert client.expired == "client"


@pytest.mark.parametrize("registered", [True, False], ids=["registered", "resource_mgr_fallback"])
def test_timeout_error_carries_client_message(monkeypatch, registered: bool) -> None:
    """A timeout error names the client-side message, not only the generic text.

    The card budget stays the reported duration, so the message still reads
    "timed out" with the declared budget for callers and the retry rail.
    """
    _raise_grace_floor(monkeypatch)
    client = _BoundaryRecordingClient(_HangingSession(), timeout_message=_CLIENT_MESSAGE)

    async def _run() -> Any:
        return await _execute(client, registered=registered)

    text = _timeout_text(asyncio.run(_run()))

    assert client.expired == "client"
    assert f"Tool 'solve' timed out after {_CARD_TIMEOUT_SECONDS}s" in text
    assert "stdio call_tool 'solve' did not complete within 0.3s" in text


@pytest.mark.parametrize("registered", [True, False], ids=["registered", "resource_mgr_fallback"])
def test_timeout_error_falls_back_when_client_message_is_empty(monkeypatch, registered: bool) -> None:
    """A bare ``TimeoutError`` (streamable-http, SSE) must not yield an empty text.

    The generic text alone keeps the model informed and keeps the retry
    rail's "timed out" marker intact.
    """
    _raise_grace_floor(monkeypatch)
    client = _BoundaryRecordingClient(_HangingSession(), timeout_message=None)

    async def _run() -> Any:
        return await _execute(client, registered=registered)

    text = _timeout_text(asyncio.run(_run()))

    assert client.expired == "client"
    assert text == f"Tool 'solve' timed out after {_CARD_TIMEOUT_SECONDS}s"


@pytest.mark.parametrize("registered", [True, False], ids=["registered", "resource_mgr_fallback"])
def test_business_error_after_a_handled_timeout_is_not_reported_as_timeout(monkeypatch, registered: bool) -> None:
    """A real MCP failure keeps its own text even when a stale timeout sits
    in its ``__context__``.

    Python chains the exception being handled onto the new one as the
    implicit ``__context__``. A client that recovers from a transport timeout
    and then raises a business error therefore carries that old timeout in
    its chain; classifying by "a TimeoutError somewhere in the chain" would
    swallow the business error and tell the model the call budget expired.
    """

    class _RecoversThenBusinessErrorClient:
        async def call_tool(self, tool_name, arguments, *, timeout: float = -1) -> Any:  # noqa: ASYNC109 -- client API
            try:
                async with asyncio.timeout(0.01):
                    await asyncio.sleep(1)
            except TimeoutError:
                raise ValueError("tool 'solve' rejected arguments: missing 'case_id'")

    monkeypatch.setattr(am_mod, "MCP_OUTER_TIMEOUT_GRACE_MIN_SECONDS", 1.0, raising=False)

    async def _run() -> Any:
        return await _execute(_RecoversThenBusinessErrorClient(), registered=registered)

    raised = asyncio.run(_run())

    assert isinstance(raised, AbilityExecutionError)
    assert raised.tool_message is not None
    text = str(raised.tool_message.content)
    assert text.startswith("Tool execution error: ")
    assert "missing 'case_id'" in text
    assert "timed out" not in text


def test_only_the_mcp_tool_wrapper_is_recognised_as_a_client_timeout() -> None:
    """Only ``MCPTool.invoke``'s own wrapper on an MCP tool is a client timeout.

    That wrapper (status ``TOOL_MCP_EXECUTION_ERROR``) has ``__cause__ = the
    client failure``. Any other error around a tool call that merely carries
    a ``TimeoutError`` as its cause, e.g. raised by the lifecycle wrapper or
    a rail, must keep its own text instead of reading "timed out".
    """
    from openjiuwen.core.common.exception.codes import StatusCode
    from openjiuwen.core.common.exception.errors import build_error

    tool = _mcp_tool(_BoundaryRecordingClient(_HangingSession(), timeout_message=None))
    client_timeout = TimeoutError("stdio call_tool 'solve' did not complete within 300s")
    unwrap = AbilityManager._wrapped_timeout_cause  # noqa: SLF001

    mcp_wrapper = build_error(
        StatusCode.TOOL_MCP_EXECUTION_ERROR,
        cause=client_timeout,
        reason=str(client_timeout),
        method="invoke",
    )
    assert unwrap(tool, mcp_wrapper) is client_timeout

    unrelated = RuntimeError("rail hook failed")
    unrelated.__cause__ = client_timeout
    assert unwrap(tool, unrelated) is None

    # A wrapper of a different kind is not recognised either, even with the
    # same cause.
    other_wrapper = build_error(StatusCode.TOOL_EXECUTION_ERROR, cause=client_timeout)
    assert unwrap(tool, other_wrapper) is None

    # Only tools whose budget was forwarded are unwrapped: an ordinary tool
    # raising the same wrapper keeps its error contract.
    class _PlainTool:
        card = ToolCard(id="plain", name="plain", description="", idempotent=True)

    assert unwrap(_PlainTool(), mcp_wrapper) is None


#: Scheduling jitter allowed when timing the outer ceiling. Far below the
#: grace floor the tests pin (1.0s), so a wrongly enlarged ceiling is caught.
_JITTER_SECONDS = 0.25


@pytest.mark.parametrize(
    ("timeout_s", "ceiling", "grace_floor", "expected_text"),
    [
        (
            _CARD_TIMEOUT_SECONDS,
            _CARD_TIMEOUT_SECONDS,
            0.05,
            f"Tool 'solve' timed out after {_CARD_TIMEOUT_SECONDS}s",
        ),
        (None, _HARD_LIMIT_SECONDS, 1.0, f"Tool 'solve' timed out after {_HARD_LIMIT_SECONDS}s"),
    ],
    ids=["declared_budget", "exempt_hard_limit"],
)
def test_outer_ceiling_still_guards_a_client_that_ignores_its_budget(
    monkeypatch: pytest.MonkeyPatch,
    timeout_s: Any,
    ceiling: float,
    grace_floor: float,
    expected_text: str,
) -> None:
    """The outer scope remains a hard ceiling when the client never times out.

    A third-party MCP client that drops the ``timeout`` kwarg must not hang
    the round: the outer ``fail_after`` still fires, with the generic message.

    For a declared budget the ceiling includes the cleanup grace. For an
    exempt tool the budget is ``MAX_TOOL_CALL_TIMEOUT_HARD_LIMIT``, which is
    absolute: no grace is added, so the call ends at the hard limit itself
    even though the grace floor here would add a full second.
    """
    monkeypatch.setattr(am_mod, "MAX_TOOL_CALL_TIMEOUT_HARD_LIMIT", _HARD_LIMIT_SECONDS)
    monkeypatch.setattr(am_mod, "MCP_OUTER_TIMEOUT_GRACE_MIN_SECONDS", grace_floor, raising=False)
    seen_budgets: list = []

    class _BudgetIgnoringClient:
        async def call_tool(self, tool_name, arguments, *, timeout: float = -1) -> Any:  # noqa: ASYNC109 -- client API
            seen_budgets.append(timeout)
            await asyncio.sleep(3600)

    async def _timed_run() -> tuple:
        started = time.monotonic()
        raised = await _execute(_BudgetIgnoringClient(), registered=True, timeout_s=timeout_s)
        return raised, time.monotonic() - started

    raised, elapsed = asyncio.run(_timed_run())
    text = _timeout_text(raised)

    assert seen_budgets == [ceiling]
    if timeout_s is None:
        # The exempt case is the one that proves no slack was added.
        assert elapsed < _HARD_LIMIT_SECONDS + _JITTER_SECONDS, f"call outlived the hard limit: {elapsed:.2f}s"
    assert text == expected_text


def test_outer_timeout_keeps_declared_budget_for_ordinary_tools() -> None:
    """Ordinary tools keep the card budget as their outer timeout (no grace)."""

    class _PlainTool:
        card = ToolCard(
            id="plain",
            name="plain",
            description="",
            properties={"resilience": {"timeout_s": 120.0}},
            idempotent=True,
        )

    assert AbilityManager._resolve_outer_timeout(_PlainTool(), 120.0) == 120.0  # noqa: SLF001


def test_outer_timeout_adds_bounded_grace_for_mcp_tools() -> None:
    """MCP tools get cleanup grace above the forwarded budget, within bounds.

    The grace is a share of the budget, clamped to a floor and a ceiling: a
    floor so tiny budgets can still be enforced by the client, a ceiling so
    huge budgets gain only bounded slack.
    """
    tool = _mcp_tool(_BoundaryRecordingClient(_HangingSession(), timeout_message=None))
    floor = am_mod.MCP_OUTER_TIMEOUT_GRACE_MIN_SECONDS
    ceiling = am_mod.MCP_OUTER_TIMEOUT_GRACE_MAX_SECONDS
    share = am_mod.MCP_OUTER_TIMEOUT_GRACE_SHARE

    def _outer(budget: float) -> float:
        return AbilityManager._resolve_outer_timeout(tool, budget)  # noqa: SLF001

    # The share falls below the floor: the floor applies.
    assert _outer(floor / share / 2) == pytest.approx(floor / share / 2 + floor)
    # The share lies inside the range: it applies as is.
    inside = (floor + ceiling) / 2 / share
    assert _outer(inside) == pytest.approx(inside * (1.0 + share))
    # The share exceeds the ceiling: the ceiling applies.
    assert _outer(ceiling / share * 2) == pytest.approx(ceiling / share * 2 + ceiling)
    # The outer timeout is never shorter than the forwarded budget.
    for budget in (0.01, 1.0, 30.0, 600.0):
        assert _outer(budget) > budget


@pytest.mark.parametrize("registered", [True, False], ids=["registered", "resource_mgr_fallback"])
def test_real_stdio_client_reports_its_own_timeout_through_the_manager(monkeypatch, registered) -> None:
    """End to end with the real ``StdioClient``: its messaged ``TimeoutError``
    (re-raised ``from`` the original) is what the caller finally reads."""
    from openjiuwen.core.foundation.tool import McpServerConfig
    from openjiuwen.core.foundation.tool.mcp.client.stdio_client import StdioClient

    monkeypatch.setattr(am_mod, "MCP_OUTER_TIMEOUT_GRACE_MIN_SECONDS", 1.0, raising=False)
    client = StdioClient(McpServerConfig(server_name="sim", server_path="python server.py", client_type="stdio"))
    client._session = _HangingSession()  # noqa: SLF001 - the only seam a connected transport exposes

    async def _run() -> Any:
        return await _execute(client, registered=registered)

    text = _timeout_text(asyncio.run(_run()))

    assert text == (
        f"Tool 'solve' timed out after {_CARD_TIMEOUT_SECONDS}s "
        f"(stdio call_tool 'solve' did not complete within {_CARD_TIMEOUT_SECONDS:g}s)"
    )

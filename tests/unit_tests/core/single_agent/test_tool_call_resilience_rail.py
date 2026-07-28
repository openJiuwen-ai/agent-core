# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for ToolCallResilienceRail retry decision.

Verifies the rail's retryability + budget logic that runs in
``on_tool_exception``: it must call ``ctx.request_retry()`` exactly when
the exception is a retryable transport/timeout failure *and* the per-call
attempt budget remains. The actual re-execution is performed by the
``@rail`` decorator's loop (rail/base.py:616-682) and is exercised
end-to-end elsewhere; these tests target the decision logic only.
"""
from __future__ import annotations

import asyncio

from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    RetryRecord,
    ToolCallInputs,
)
from openjiuwen.harness.rails.tool_call_resilience_rail import (
    ToolCallResilienceRail,
)


def _ctx(*, tool_name: str = "t", exception=None, retry_attempt: int = 0) -> AgentCallbackContext:
    """Build a minimal AgentCallbackContext for a tool-call exception hook.

    ``agent`` is None: the rail cannot resolve ``ToolCard.idempotent`` on this
    ctx, so the tool is treated as retryable. Use ``_ctx_with_card`` for tests
    that exercise the non-idempotent short-circuit.
    """
    ctx = AgentCallbackContext(
        agent=None,
        inputs=ToolCallInputs(tool_name=tool_name, tool_call=None, tool_args="{}"),
        config=None,
        session=None,
        context=None,
        extra={},
    )
    ctx.exception = exception
    ctx.retry_attempt = retry_attempt
    return ctx


class _FakeAbilityManager:
    """Minimal stand-in returning a registered ``ToolCard`` by name.

    The rail reads ``ctx.agent.ability_manager.get(tool_name)`` to resolve
    ``ToolCard.idempotent``; this stand-in lets tests inject a card with the
    desired idempotency without spinning up the full AbilityManager.
    """

    def __init__(self, cards: dict[str, ToolCard]) -> None:
        self._cards = cards

    def get(self, name: str):
        return self._cards.get(name)


class _FakeAgent:
    def __init__(self, cards: dict[str, ToolCard]) -> None:
        self.ability_manager = _FakeAbilityManager(cards)


def _ctx_with_card(
    *, tool_name: str, idempotent: bool, exception, retry_attempt: int = 0,
) -> AgentCallbackContext:
    """ctx whose agent exposes a registered ToolCard with the given idempotency."""
    from openjiuwen.core.foundation.tool import ToolCard

    card = ToolCard(
        id=tool_name, name=tool_name, description=f"{tool_name} desc",
        idempotent=idempotent,
    )
    ctx = AgentCallbackContext(
        agent=_FakeAgent({tool_name: card}),
        inputs=ToolCallInputs(tool_name=tool_name, tool_call=None, tool_args="{}"),
        config=None, session=None, context=None, extra={},
    )
    ctx.exception = exception
    ctx.retry_attempt = retry_attempt
    return ctx


def test_on_tool_exception_requests_retry_within_budget() -> None:
    rail = ToolCallResilienceRail(max_attempts=3)

    async def _run():
        ctx = _ctx(
            tool_name="search",
            exception=RuntimeError("connection reset"),
            retry_attempt=0,  # first failure; next attempt (1) < 3
        )
        await rail.on_tool_exception(ctx)
        assert ctx.consume_retry_request() is not None
        assert rail._invoke_retry_count == 1

    asyncio.run(_run())


def test_on_tool_exception_requests_second_retry_still_in_budget() -> None:
    rail = ToolCallResilienceRail(max_attempts=3)

    async def _run():
        ctx = _ctx(
            tool_name="search",
            exception=TimeoutError("timed out"),
            retry_attempt=1,  # second failure; next attempt (2) < 3
        )
        await rail.on_tool_exception(ctx)
        assert ctx.consume_retry_request() is not None

    asyncio.run(_run())


def test_on_tool_exception_no_retry_when_budget_exhausted() -> None:
    rail = ToolCallResilienceRail(max_attempts=3)

    async def _run():
        ctx = _ctx(
            tool_name="search",
            exception=TimeoutError("timed out"),
            retry_attempt=2,  # third failure; next attempt (3) == max → stop
        )
        await rail.on_tool_exception(ctx)
        assert ctx.consume_retry_request() is None

    asyncio.run(_run())


def test_on_tool_exception_budget_exhausted_builds_retry_summary_without_crash() -> None:
    """When the budget is exhausted AND retry_history is non-empty (the real
    runtime shape — @rail appends a RetryRecord per failed attempt), the rail
    must build the [Retry Summary] tool message without crashing on a None
    ``tool_call``. ``tool_call`` is guaranteed non-None on the production
    path, but the rail must degrade gracefully here instead of raising
    AttributeError.
    """
    rail = ToolCallResilienceRail(max_attempts=3)

    async def _run():
        ctx = _ctx(
            tool_name="search",
            exception=TimeoutError("timed out"),
            retry_attempt=2,
        )
        # Simulate two prior failed attempts recorded by the @rail decorator.
        ctx.retry_history.append(RetryRecord(0, "TimeoutError", "timed out", 1.0))
        ctx.retry_history.append(RetryRecord(1, "TimeoutError", "timed out", 2.0))
        ctx.invoke_start_time = 0.5
        await rail.on_tool_exception(ctx)
        # Did not crash, and produced a recoverable summary for the LLM.
        assert ctx.consume_retry_request() is None
        assert ctx.inputs.tool_msg is not None
        assert "Retry Summary" in ctx.inputs.tool_msg.content
        assert ctx.inputs.tool_msg.tool_call_id == ""

    asyncio.run(_run())


def test_on_tool_exception_no_retry_for_business_error() -> None:
    rail = ToolCallResilienceRail(max_attempts=3)

    async def _run():
        ctx = _ctx(
            tool_name="edit_file",
            exception=ValueError("malformed arguments JSON"),
            retry_attempt=0,
        )
        await rail.on_tool_exception(ctx)
        assert ctx.consume_retry_request() is None
        assert rail._invoke_retry_count == 0

    asyncio.run(_run())


def test_on_tool_exception_no_retry_for_non_idempotent_even_if_retryable() -> None:
    """Layer 0 gates *before* the retryable-exception check: a non-idempotent
    tool that raises a transport/timeout marker (which Layer 1 would
    otherwise retry) must still NOT be retried — re-running the side effect
    (a write / shell / subagent-spawn) after a failure is worse than
    surfacing the error. Budget and exception type are irrelevant here.
    """
    rail = ToolCallResilienceRail(max_attempts=3)

    async def _run():
        # write_file with a registered card whose idempotent=False, plus a
        # retryable TimeoutError, within budget. The card's idempotent flag —
        # not a hard-coded name set — must suppress the retry.
        ctx = _ctx_with_card(
            tool_name="write_file",
            idempotent=False,
            exception=TimeoutError("timed out"),
            retry_attempt=0,
        )
        await rail.on_tool_exception(ctx)
        assert ctx.consume_retry_request() is None
        assert rail._invoke_retry_count == 0

    asyncio.run(_run())


def test_on_tool_exception_retries_idempotent_tool_with_timeout_marker() -> None:
    """Regression guard: Layer 0 must not over-reach. An *idempotent* tool
    raising the same retryable marker within budget IS retried — proving
    the non-idempotent short-circuit is card-scoped, not a blanket suppression.
    """
    rail = ToolCallResilienceRail(max_attempts=3)

    async def _run():
        ctx = _ctx_with_card(
            tool_name="free_search",
            idempotent=True,
            exception=TimeoutError("timed out"),
            retry_attempt=0,
        )
        await rail.on_tool_exception(ctx)
        assert ctx.consume_retry_request() is not None

    asyncio.run(_run())


def test_on_tool_exception_no_retry_for_permission_error() -> None:
    rail = ToolCallResilienceRail(max_attempts=3)

    async def _run():
        ctx = _ctx(
            tool_name="bash",
            exception=PermissionError("sudo: a password is required"),
            retry_attempt=0,
        )
        await rail.on_tool_exception(ctx)
        assert ctx.consume_retry_request() is None

    asyncio.run(_run())


def test_on_tool_exception_no_retry_when_max_attempts_is_one() -> None:
    rail = ToolCallResilienceRail(max_attempts=1)

    async def _run():
        ctx = _ctx(
            tool_name="search",
            exception=TimeoutError("timed out"),
            retry_attempt=0,  # next attempt (1) == max(1) → stop immediately
        )
        await rail.on_tool_exception(ctx)
        assert ctx.consume_retry_request() is None

    asyncio.run(_run())


def test_on_tool_exception_no_retry_for_http_status_in_message() -> None:
    # An HTTP 503 surfaced as a bare RuntimeError must not be retried by the
    # transport markers; it belongs on the StatusCode axis (P2).
    rail = ToolCallResilienceRail(max_attempts=3)

    async def _run():
        ctx = _ctx(
            tool_name="fetch_webpage",
            exception=RuntimeError("upstream returned 503"),
            retry_attempt=0,
        )
        await rail.on_tool_exception(ctx)
        assert ctx.consume_retry_request() is None

    asyncio.run(_run())


def test_before_invoke_resets_counter() -> None:
    rail = ToolCallResilienceRail(max_attempts=3)

    async def _run():
        rail._invoke_retry_count = 5
        await rail.before_tool_call(_ctx())
        assert rail._invoke_retry_count == 0

    asyncio.run(_run())

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
    ToolCallInputs,
)
from openjiuwen.harness.rails.tool_call_resilience_rail import (
    ToolCallResilienceRail,
)


def _ctx(*, tool_name: str = "t", exception=None, retry_attempt: int = 0) -> AgentCallbackContext:
    """Build a minimal AgentCallbackContext for a tool-call exception hook."""
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
        # write_file + a retryable TimeoutError, within budget (retry_attempt=0).
        ctx = _ctx(
            tool_name="write_file",
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
    the non-idempotent short-circuit is name-scoped, not a blanket suppression.
    """
    rail = ToolCallResilienceRail(max_attempts=3)

    async def _run():
        ctx = _ctx(
            tool_name="free_search",
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
        await rail.before_invoke(_ctx())
        assert rail._invoke_retry_count == 0

    asyncio.run(_run())

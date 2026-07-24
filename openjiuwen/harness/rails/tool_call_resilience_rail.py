# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Rail for retrying retryable tool-call failures with a bounded budget.

Hangs onto the existing ``@rail`` retry loop (``rail/base.py:616-682``):
on ``ON_TOOL_EXCEPTION`` it decides whether the failure is retryable
(transport/timeout markers, not business/logic errors) and whether the
per-invoke retry budget remains; if so it calls ``ctx.request_retry()``
and the decorator transparently re-runs the tool call. This recovers
transient transport/timeout failures (e.g. the P0 call-level timeout
firing on a half-dead MCP transport) instead of leaving the round stuck.
"""
from __future__ import annotations

from typing import Optional

from openjiuwen.core.common.logging import logger
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.base import DeepAgentRail
from openjiuwen.harness.rails.tool_inventory import NON_IDEMPOTENT_TOOL_NAMES


class ToolCallResilienceRail(DeepAgentRail):
    """Retry retryable tool-call failures with a bounded per-invoke budget.

    The retry mechanism itself is provided by the ``@rail`` decorator that
    wraps ``AbilityManager._railed_execute_single_tool_call``: when this
    rail calls ``ctx.request_retry()`` in ``on_tool_exception``, the
    decorator consumes it and re-runs the call. This rail only owns the
    *decision* (is the exception retryable? is the budget exhausted?) and
    *bookkeeping* (per-invoke counter reset in ``before_invoke``).

    Retryability is decided in layers: non-idempotent tools are never retried
    (re-running a write / shell / subagent-spawn after a failure would repeat
    the side effect); then transport/timeout markers are retryable;
    argument/logic/permission errors are not.
    """

    priority = 70

    #: Retryable transport/timeout markers — a hit in the exception class
    #: name *or* message text means retryable.
    _RETRYABLE_EXC_MARKERS = (
        # anyio fail_after / asyncio timeouts (call-level timeout path)
        "timed out",
        "timeout",
        # aiohttp server-side timeout class name
        "servertimeouterror",
        # anyio / MCP transport markers (align with reconnect.py)
        "session terminated",
        "closedresourceerror",
        "brokenresourceerror",
        "endofstream",
        "stream closed",
        "connection closed",
        "remoteprotocolerror",
        "readerror",
        "writeerror",
        "not connected",
        # socket-level resets / aborts
        "connection reset",
        "connectionreseterror",
        "connectionaborted",
        "connectionabortederror",
        "broken pipe",
        "brokenpipeerror",
    )

    #: Non-retryable exception types: argument/logic/permission errors where
    #: retrying the same call is pointless or harmful.
    _NON_RETRYABLE_EXC_TYPES = (
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        PermissionError,
        FileNotFoundError,
        IsADirectoryError,
    )

    DEFAULT_MAX_ATTEMPTS = 3

    def __init__(self, *, max_attempts: int = DEFAULT_MAX_ATTEMPTS) -> None:
        super().__init__()
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        self._default_max_attempts = max_attempts
        # Per-invoke cumulative retry counter; reset in before_invoke.
        self._invoke_retry_count = 0

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        """Reset the per-invoke retry counter at the start of each invoke."""
        self._invoke_retry_count = 0

    async def on_tool_exception(self, ctx: AgentCallbackContext) -> None:
        """Decide retryability + budget on a tool-call failure.

        Only requests a retry when the exception is retryable *and* the
        per-call attempt budget remains; otherwise leaves the exception
        for the ``@rail`` decorator to propagate.
        """
        exc = ctx.exception
        tool_name = self._tool_name(ctx)
        max_attempts = self._resolve_max_attempts(ctx, tool_name)

        # Layer 0 — non-idempotent tools never retry. This runs *before*
        # the retryable-exception check on purpose: even a transport/timeout
        # marker (which Layer 1 would otherwise retry) must not re-run a
        # side-effecting tool (write / shell / subagent-spawn / UI action).
        # The set is shared with AbilityManager's timeout exemption so a
        # non-idempotent tool is never timed out AND never retried — closing
        # the "timeout fires -> retry repeats side effect" loop.
        if tool_name and tool_name in NON_IDEMPOTENT_TOOL_NAMES:
            logger.info(
                "[ToolResilience] '%s' is non-idempotent, skipping retry",
                tool_name,
            )
            return

        # Layer 1 — exception type retryable?
        if not self._is_retryable_exception(exc):
            return

        # Layer 3 — per-call budget. retry_attempt is the 0-based index of
        # the *current* failed attempt set by the @rail decorator
        # (rail/base.py:623), so the next attempt would be retry_attempt+1;
        # we need that to stay < max_attempts.
        if ctx.retry_attempt + 1 >= max_attempts:
            logger.warning(
                "[ToolResilience] '%s' still failing after %d attempts, "
                "giving up",
                tool_name, ctx.retry_attempt + 1,
            )
            return

        self._invoke_retry_count += 1
        logger.warning(
            "[ToolResilience] retrying '%s' after %s "
            "(attempt %d/%d)",
            tool_name, type(exc).__name__,
            ctx.retry_attempt + 2, max_attempts,
        )
        ctx.request_retry()

    @classmethod
    def _is_retryable_exception(
            cls, exc: Optional[BaseException],
    ) -> bool:
        """Return True if ``exc`` looks like a retryable transport/timeout failure."""
        if exc is None:
            return False
        if isinstance(exc, cls._NON_RETRYABLE_EXC_TYPES):
            return False
        name = exc.__class__.__name__.lower()
        text = str(exc).lower()
        return any(marker in name or marker in text for marker in cls._RETRYABLE_EXC_MARKERS)

    @staticmethod
    def _tool_name(ctx: AgentCallbackContext) -> str:
        inputs = getattr(ctx, "inputs", None)
        return getattr(inputs, "tool_name", "") or ""

    def _resolve_max_attempts(
            self,
            ctx: AgentCallbackContext,
            tool_name: str,
    ) -> int:
        """Resolve the max-attempts budget for this tool call.

        Solve the problem of the maximum attempt count budget for this tool invocation.
        Currently, it returns the default value of the entire track.
        In the future, it will read the override settings for each tool from
        ``ToolCard.properties["resilience"]["max_attempts"]`` through the agent's capability manager.
        """
        return self._default_max_attempts


__all__ = ["ToolCallResilienceRail"]

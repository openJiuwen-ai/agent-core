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
from openjiuwen.core.foundation.tool.schema import ToolTimeoutResult
from openjiuwen.core.single_agent.ability_manager import AbilityExecutionError
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ToolCallInputs
from openjiuwen.harness.rails.base import DeepAgentRail


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

    #: Retryable exception types: transport/timeout failures where
    #: retrying is safe and likely to succeed. Used as a type-based
    #: fast-path before falling back to string matching.
    _RETRYABLE_EXC_TYPES = (
        TimeoutError,
        ConnectionResetError,
        BrokenPipeError,
        ConnectionAbortedError,
    )

    DEFAULT_MAX_ATTEMPTS = 3

    def __init__(self, *, max_attempts: int = DEFAULT_MAX_ATTEMPTS) -> None:
        super().__init__()
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        self._default_max_attempts = max_attempts
        # Per-invoke cumulative retry counter; reset in before_invoke.
        self._invoke_retry_count = 0

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
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

        if ctx.inputs.tool_result is None:
            error_text: str
            if isinstance(exc, AbilityExecutionError) and exc.tool_message is not None:
                error_text = str(exc.tool_message.content)
            else:
                error_text = str(exc)
            ctx.inputs.tool_result = ToolTimeoutResult(
                success=False,
                data=None,
                error=error_text,
            )
        # Layer 0 — non-idempotent tools never retry. Whether a tool is
        # exempt is owned by this rail (via ToolCard.idempotent on the
        # registered card), decoupled from the call-level timeout in
        # AbilityManager._resolve_call_timeout.
        is_non_idempotent = self._is_non_idempotent(ctx, tool_name)
        if is_non_idempotent:
            logger.info(
                "[ToolResilience] '%s' is non-idempotent, skipping retry",
                tool_name,
            )
            return

        # Layer 1 — exception type retryable?
        if not self._is_retryable_exception(exc):
            return

        # Layer 2 — per-call budget.
        if ctx.retry_attempt + 1 >= max_attempts:
            logger.warning(
                "[ToolResilience] '%s' still failing after %d attempts, "
                "giving up",
                tool_name, ctx.retry_attempt + 1,
            )

            # Build retry summary so _execute_tool_call_batch can enrich
            # error messages and the LLM knows how many attempts were made.
            if not ctx.retry_history:
                return
            history_lines = [
                f"  Attempt {r.attempt_index + 1}: {r.exception_type} - {r.exception_message}"
                for r in ctx.retry_history
            ]
            total_elapsed = 0.0
            if len(ctx.retry_history) >= 1:
                start_ts = (
                        ctx.invoke_start_time
                        or ctx.retry_history[0].timestamp
                )
                total_elapsed = (
                        ctx.retry_history[-1].timestamp - start_ts
                )
            retry_summary = (
                    f"\n\n[Retry Summary] {len(ctx.retry_history)} attempt(s) failed "
                    f"(total elapsed: {total_elapsed:.1f}s):\n"
                    + "\n".join(history_lines)
            )
            from openjiuwen.core.foundation.llm import ToolMessage

            tool_call = ctx.inputs.tool_call
            tool_call_id = tool_call.id if tool_call is not None else ""
            ctx.inputs.tool_msg = ToolMessage(
                content=retry_summary,
                tool_call_id=tool_call_id
            )
            ctx.inputs.tool_result = ToolTimeoutResult(
                success=False,
                data=None,
                error=retry_summary,
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
        # Fast path: type-based retryability (covers anyio.TimeoutError,
        # asyncio.TimeoutError on Python 3.11+, and socket-level resets).
        if isinstance(exc, cls._RETRYABLE_EXC_TYPES):
            return True
        # Fallback: string matching for wrapped exceptions (e.g.
        # AbilityExecutionError with "timed out" in its message).
        name = exc.__class__.__name__.lower()
        text = str(exc).lower()
        return any(marker in name or marker in text for marker in cls._RETRYABLE_EXC_MARKERS)

    @staticmethod
    def _tool_name(ctx: AgentCallbackContext) -> str:
        inputs = getattr(ctx, "inputs", None)
        return getattr(inputs, "tool_name", "") or ""

    @classmethod
    def _is_non_idempotent(cls, ctx: AgentCallbackContext, tool_name: str) -> bool:
        """Return True if the tool is side-effecting and must not be retried.

        Decided solely by the explicit ``ToolCard.idempotent`` field on the
        registered card (secure-by-default: ``False``). A tool whose card is
        not registered under ``tool_name`` is treated as retryable — callers
        that need suppression must register a card with ``idempotent=False``.
        """
        if not tool_name:
            return False

        agent = getattr(ctx, "agent", None)
        if agent is None:
            return False
        ability_mgr = getattr(agent, "ability_manager", None)
        if ability_mgr is None:
            return False
        ability = ability_mgr.get(tool_name)
        from openjiuwen.core.foundation.tool import ToolCard
        return isinstance(ability, ToolCard) and ability.idempotent is False

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

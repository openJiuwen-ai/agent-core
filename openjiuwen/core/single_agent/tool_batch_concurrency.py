# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Batch-scoped tool concurrency control for AbilityManager tool execution.

Requires Python >= 3.11 (project baseline). Relies on asyncio.Semaphore
behaviour under task cancellation (3.9+).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, TypeVar

from openjiuwen.core.common.logging import logger

T = TypeVar("T")

_LOG_PREFIX = "[tool_batch_concurrency]"


@dataclass(frozen=True)
class ToolConcurrencyRule:
    limit: int


@dataclass(frozen=True)
class ToolBatchConcurrencyPolicy:
    enabled: bool = True
    tools: dict[str, ToolConcurrencyRule] = field(default_factory=dict)

    @property
    def limits(self) -> dict[str, int]:
        return {name: rule.limit for name, rule in self.tools.items()}

    def rule_for(self, tool_name: str) -> ToolConcurrencyRule | None:
        return self.tools.get(normalize_tool_name(tool_name))

    def as_log_text(self) -> str:
        if not self.tools:
            return "{}"
        parts = [f"{name}={rule.limit}" for name, rule in sorted(self.tools.items())]
        return "{" + ", ".join(parts) + "}"


ToolBatchConcurrencyPolicyProvider = Callable[[], ToolBatchConcurrencyPolicy]


def normalize_tool_name(name: str | None) -> str:
    return str(name or "").strip().lower()


def _counts_text(counts: Mapping[str, int]) -> str:
    if not counts:
        return "-"
    return ",".join(f"{name}:{count}" for name, count in sorted(counts.items()))


def _tool_call_label(tool_call: Any) -> str:
    tc_id = getattr(tool_call, "id", None)
    if tc_id:
        return str(tc_id)[:16]
    return normalize_tool_name(getattr(tool_call, "name", "?")) or "?"


def _redact_session_label(session_id: str) -> str:
    if not session_id or session_id == "-":
        return session_id
    if len(session_id) <= 12:
        return session_id
    return f"{session_id[:12]}..."


@dataclass
class _BatchExecutionContext:
    session_id: str
    max_concurrent: dict[str, int]
    semaphores: dict[str, asyncio.Semaphore]
    in_flight: dict[str, int] = field(default_factory=dict)
    # Serializes in_flight counter updates with acquire/release logging (same event loop).
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    started_at: float = field(default_factory=time.monotonic)

    @classmethod
    def build_from_policy(
        cls,
        *,
        session_id: str,
        policy: ToolBatchConcurrencyPolicy,
    ) -> _BatchExecutionContext | None:
        if not policy.enabled or not policy.limits:
            return None

        max_concurrent: dict[str, int] = {}
        semaphores: dict[str, asyncio.Semaphore] = {}
        for name, rule in policy.tools.items():
            norm = normalize_tool_name(name)
            if rule.limit < 1:
                logger.warning(
                    "%s ignore tool %r with limit < 1: %r",
                    _LOG_PREFIX,
                    norm,
                    rule.limit,
                )
                continue
            limit = rule.limit
            max_concurrent[norm] = limit
            semaphores[norm] = asyncio.Semaphore(limit)

        if not semaphores:
            return None

        return cls(
            session_id=session_id,
            max_concurrent=max_concurrent,
            semaphores=semaphores,
            in_flight={name: 0 for name in semaphores},
        )

    @asynccontextmanager
    async def slot(self, tool_call: Any):
        tool_name = normalize_tool_name(getattr(tool_call, "name", ""))
        sem = self.semaphores.get(tool_name)
        if sem is None:
            yield
            return

        limit = self.max_concurrent[tool_name]
        label = _tool_call_label(tool_call)
        t0 = time.monotonic()
        await sem.acquire()
        try:
            async with self.lock:
                self.in_flight[tool_name] = self.in_flight.get(tool_name, 0) + 1
                cur = self.in_flight[tool_name]
            logger.debug(
                "%s slot acquire tool=%s id=%s in_flight=%d/%d",
                _LOG_PREFIX,
                tool_name,
                label,
                cur,
                limit,
            )
            yield
        finally:
            elapsed = time.monotonic() - t0
            async with self.lock:
                self.in_flight[tool_name] = max(0, self.in_flight.get(tool_name, 0) - 1)
                cur = self.in_flight[tool_name]
            sem.release()
            logger.debug(
                "%s slot release tool=%s id=%s elapsed=%.2fs in_flight=%d/%d",
                _LOG_PREFIX,
                tool_name,
                label,
                elapsed,
                cur,
                limit,
            )


def count_limited_tool_calls(
    tool_calls: Sequence[Any],
    policy: ToolBatchConcurrencyPolicy,
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for tc in tool_calls:
        name = normalize_tool_name(getattr(tc, "name", ""))
        if name in policy.limits:
            counts[name] = counts.get(name, 0) + 1
    return counts


class ToolBatchConcurrencyController:
    """Queue-based batch concurrency controller driven by an injectable policy provider."""

    def __init__(self, policy_provider: ToolBatchConcurrencyPolicyProvider) -> None:
        self._policy_provider = policy_provider
        self._batch_ctx: ContextVar[_BatchExecutionContext | None] = ContextVar(
            "_openjiuwen_tool_batch_concurrency_state",
            default=None,
        )

    def current_policy(self) -> ToolBatchConcurrencyPolicy:
        return self._policy_provider()

    def active(self) -> bool:
        return self._batch_ctx.get() is not None

    @asynccontextmanager
    async def batch_scope(
        self,
        *,
        session_id: str = "-",
        policy: ToolBatchConcurrencyPolicy | None = None,
    ):
        """Enter a re-entrant batch scope keyed by session_id.

        Semaphores are pre-created from the policy snapshot at enter time so
        streaming early tool dispatch works without knowing N upfront.
        """
        if self._batch_ctx.get() is not None:
            yield
            return

        policy = policy or self.current_policy()
        if not policy.enabled or not policy.limits:
            yield
            return

        ctx = _BatchExecutionContext.build_from_policy(
            session_id=session_id,
            policy=policy,
        )
        if ctx is None:
            yield
            return

        logger.info(
            "%s batch enter session=%s policy=%s",
            _LOG_PREFIX,
            _redact_session_label(session_id),
            policy.as_log_text(),
        )

        token = self._batch_ctx.set(ctx)
        try:
            yield
        finally:
            logger.info(
                "%s batch done session=%s elapsed=%.2fs policy=%s",
                _LOG_PREFIX,
                _redact_session_label(session_id),
                time.monotonic() - ctx.started_at,
                policy.as_log_text(),
            )
            self._batch_ctx.reset(token)

    async def run_with_slot(self, tool_call: Any, run: Callable[[], Awaitable[T]]) -> T:
        ctx = self._batch_ctx.get()
        if ctx is None:
            return await run()

        tool_name = normalize_tool_name(getattr(tool_call, "name", ""))
        if tool_name not in ctx.semaphores:
            return await run()

        async with ctx.slot(tool_call):
            return await run()

    async def gather_with_limit(
        self,
        tool_calls: Sequence[Any],
        run_one: Callable[[Any], Awaitable[T]],
        *,
        session_id: str = "-",
        policy: ToolBatchConcurrencyPolicy | None = None,
    ) -> list[T | BaseException]:
        """Batch-limited gather helper (used by tests and direct execute() callers)."""
        if not tool_calls:
            return []

        policy = policy or self.current_policy()
        batch_t0 = time.monotonic()

        async def _wrapped(tc: Any) -> T:
            async def _run() -> T:
                return await run_one(tc)

            return await self.run_with_slot(tc, _run)

        async with self.batch_scope(session_id=session_id, policy=policy):
            results = list(
                await asyncio.gather(
                    *(_wrapped(tc) for tc in tool_calls),
                    return_exceptions=True,
                )
            )

        batch_counts = count_limited_tool_calls(tool_calls, policy)
        if batch_counts:
            errors = sum(1 for r in results if isinstance(r, BaseException))
            logger.info(
                "%s gather done session=%s tools=%d batch_counts=%s elapsed=%.2fs errors=%d",
                _LOG_PREFIX,
                _redact_session_label(session_id),
                len(tool_calls),
                _counts_text(batch_counts),
                time.monotonic() - batch_t0,
                errors,
            )

        return results

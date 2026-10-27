"""Distill schedule: due decision, single-home lease tick, and poll loop.

Auto-trigger predicate:

    volume_ok   = pending >= message_threshold   # hard min new eligible msgs
    cooldown_ok = (last_attempt <= 0) OR (age >= interval_ms)
    due         = enabled AND volume_ok AND cooldown_ok

Field names ``period_due`` / ``volume_due`` are kept for callers; only ``due``
means the tick should run.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from openjiuwen.core.common.logging import logger
from openjiuwen.harness.personal_context.distill.corpus import CorpusPort
from openjiuwen.harness.personal_context.distill.runner import DistillRunResult
from openjiuwen.harness.personal_context.distill.store import (
    complete_distill_lease,
    get_cursor_ms,
    get_last_attempt_at_ms,
    recover_expired_distill_lease,
    set_last_attempt_at_ms,
    try_claim_distill_lease,
)


@dataclass(frozen=True, slots=True)
class DistillScheduleConfig:
    enabled: bool = False
    interval_ms: int = 86_400_000
    message_threshold: int = 50
    lease_ms: int = 3_600_000
    poll_seconds: float = 60.0
    learning_since_ms: int | None = None
    max_messages: int = 800


@dataclass(frozen=True, slots=True)
class DistillDueDecision:
    due: bool
    period_due: bool  # cooldown_ok: age >= interval (or never attempted)
    volume_due: bool  # volume_ok: pending >= message_threshold
    pending_count: int


@dataclass(frozen=True, slots=True)
class DistillTickResult:
    action: str
    reason: str | None = None
    run: DistillRunResult | None = None


class DistillRunnerPort(Protocol):
    async def __call__(
        self,
        home: str,
        *,
        window_end_ms: int,
        learning_since_ms: int | None = None,
        max_messages: int = 800,
        force_full_window: bool = False,
    ) -> DistillRunResult:
        ...


def evaluate_distill_due(
    *,
    enabled: bool,
    now_ms: int,
    last_attempt_at_ms: int,
    interval_ms: int,
    pending_count: int,
    message_threshold: int,
) -> DistillDueDecision:
    """Return due iff enabled AND min-volume AND cooldown (AND, not OR)."""
    last = int(last_attempt_at_ms)
    if last <= 0:
        period_due = True  # cooldown_ok
    else:
        age_ms = max(0, int(now_ms) - last)
        period_due = age_ms >= int(interval_ms)
    volume_due = int(pending_count) >= int(message_threshold)
    due = bool(enabled) and volume_due and period_due
    return DistillDueDecision(
        due=due,
        period_due=period_due,
        volume_due=volume_due,
        pending_count=int(pending_count),
    )


def _skip_reason_when_not_due(
    *,
    enabled: bool,
    volume_due: bool,
    period_due: bool,
) -> str:
    if not enabled:
        return "disabled"
    if not volume_due:
        return "below_threshold"
    if not period_due:
        return "cooldown"
    return "not_due"


async def tick_distill_schedule(
    home: str,
    *,
    now_ms: int,
    corpus: CorpusPort,
    run_job: DistillRunnerPort,
    config: DistillScheduleConfig,
) -> DistillTickResult:
    recover_expired_distill_lease(home, now_ms=now_ms)
    cursor_ms = get_cursor_ms(home)
    pending = corpus.count_eligible_since(cursor_ms=cursor_ms, until_ms=now_ms)
    decision = evaluate_distill_due(
        enabled=config.enabled,
        now_ms=now_ms,
        last_attempt_at_ms=get_last_attempt_at_ms(home),
        interval_ms=config.interval_ms,
        pending_count=pending,
        message_threshold=config.message_threshold,
    )
    if not decision.due:
        return DistillTickResult(
            action="skipped",
            reason=_skip_reason_when_not_due(
                enabled=config.enabled,
                volume_due=decision.volume_due,
                period_due=decision.period_due,
            ),
        )

    token = try_claim_distill_lease(home, now_ms=now_ms, lease_ms=config.lease_ms)
    if token is None:
        return DistillTickResult(action="skipped", reason="lease_held")

    try:
        result = await run_job(
            home,
            window_end_ms=int(now_ms),
            learning_since_ms=config.learning_since_ms,
            max_messages=config.max_messages,
        )
    finally:
        complete_distill_lease(home, token)
        set_last_attempt_at_ms(home, int(now_ms))

    return DistillTickResult(action="ran", run=result)


async def run_distill_scheduler_loop(
    home: str,
    stop_event: asyncio.Event,
    *,
    get_corpus: Callable[[], CorpusPort],
    get_runner: Callable[[], DistillRunnerPort],
    config: DistillScheduleConfig,
    now_ms: Callable[[], int],
) -> None:
    """Poll until stop_event; each wake runs one tick when due."""

    poll = max(float(config.poll_seconds), 0.01)
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=poll)
            return
        except asyncio.TimeoutError:
            pass
        if stop_event.is_set():
            return
        try:
            await tick_distill_schedule(
                home,
                now_ms=now_ms(),
                corpus=get_corpus(),
                run_job=get_runner(),
                config=config,
            )
        except Exception:
            logger.exception("distill schedule tick failed home=%s", home)

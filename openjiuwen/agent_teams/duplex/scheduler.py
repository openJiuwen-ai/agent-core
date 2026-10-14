# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Per-target judgment scheduling for the duplex router.

One scheduler serves one target member. It holds the state the pure policy
must not hold:

- **one judgment at a time**: a single worker task asks the classifier; messages
  that arrive meanwhile wait and form the next batch;
- **batching**: items of one batch that need the same questions share one
  classifier request; groups with different questions run concurrently and
  the whole batch is handed to the executor in arrival order;
- **broadcast rate limit**: one broadcast per sender per 10 s;
- **current-task summary**: the context string the classifier sees.

Classifier failure is not an error here: the batch is resolved with no
answers, which the policy turns into "deliver as usual".
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from openjiuwen.agent_teams.duplex.classifier import ChoiceClassifier
from openjiuwen.agent_teams.duplex.policy import Decision, MessageSource, Plan, broadcast_allowed, resolve
from openjiuwen.agent_teams.harness.protocol import WorkSummarySource
from openjiuwen.core.common.logging import team_logger


@dataclass
class RoutedMessage:
    """One mailbox row handed to the router.

    Attributes:
        row: The team message row (marked read through the message manager).
        text: The SDK rendering delivered to the target, unchanged.
        source: Scenario of the row.
        sender: Sending member name.
        body: Raw message text for the classifier (no envelope, no excerpt).
        use_steer: The drain's original steer flag (used by PASSTHROUGH).
    """

    row: Any
    text: str
    source: MessageSource
    sender: str
    body: str
    use_steer: bool = True

    @property
    def message_id(self) -> str:
        """Row identity."""
        return str(self.row.message_id)

    def classifier_text(self) -> str:
        """Message text with its sender, so the classifier can tell user from teammate."""
        return f"from {self.sender}: {self.body}"


@dataclass
class Judged:
    """A batch item with its plan-time state and resolved decision."""

    item: RoutedMessage
    plan: Plan
    idle: bool
    decision: Decision | None = field(default=None)


def work_context(runtime: Any, member_name: str, *, running: bool) -> str:
    """Describe the recipient's current work without asking the model."""
    summary: dict[str, str] = {"recipient": member_name}
    if isinstance(runtime, WorkSummarySource):
        summary.update(runtime.work_summary())
    else:
        summary["phase"] = "running" if running else "idle"
    return json.dumps(summary, ensure_ascii=False)


class DuplexScheduler:
    """Single in-flight judgment per target, with batching and rate limit."""

    def __init__(
        self,
        *,
        classifier_factory: Callable[[], ChoiceClassifier],
        context: Callable[[], str],
        execute: Callable[[list[Judged]], Awaitable[None]],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._classifier_factory = classifier_factory
        self._context = context
        self._execute = execute
        self._clock = clock
        self._pending: list[Judged] = []
        self._worker: asyncio.Task | None = None
        self._broadcast_at: dict[str, tuple[float, str]] = {}
        self._classifier: ChoiceClassifier | None = None

    def allow_broadcast(self, sender: str, message_id: str) -> bool:
        """Consume the sender's broadcast slot; False when over the limit.

        The row that holds the slot keeps it, so a redelivery after a failure
        is not rate limited by its own first attempt.
        """
        now = self._clock()
        last = self._broadcast_at.get(sender)
        if last is not None and last[1] == message_id:
            return True
        if not broadcast_allowed(last[0] if last else None, now):
            return False
        self._broadcast_at[sender] = (now, message_id)
        return True

    def enqueue(self, judged: Judged) -> None:
        """Queue an item; starts the worker when none is running."""
        self._pending.append(judged)
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run(), name="duplex_scheduler")

    async def wait_idle(self) -> None:
        """Wait until the queue is drained (tests and shutdown)."""
        while self._worker is not None and not self._worker.done():
            await asyncio.shield(self._worker)

    async def aclose(self) -> None:
        """Cancel the worker and close the classifier; pending rows stay unread for the next drain."""
        worker, self._worker = self._worker, None
        self._pending.clear()
        if worker is not None and not worker.done():
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        classifier, self._classifier = self._classifier, None
        close = getattr(classifier, "aclose", None)
        if close is not None:
            try:
                await close()
            except Exception:  # noqa: BLE001 - shutdown must not fail on a connection close
                team_logger.warning("duplex: classifier close failed", exc_info=True)

    async def _run(self) -> None:
        while self._pending:
            batch, self._pending = self._pending, []
            try:
                answers = await self._judge(batch)
                for judged in batch:
                    judged.decision = resolve(
                        judged.item.source, judged.plan, answers.get(judged.plan.questions), idle=judged.idle
                    )
                await self._execute(batch)
            except Exception:  # noqa: BLE001 - one bad batch must not stop the worker
                team_logger.error("duplex: batch execution failed", exc_info=True)

    async def _judge(self, batch: list[Judged]) -> dict[tuple[str, ...], Any]:
        """One classifier request per question set; a failed group yields None."""
        groups: dict[tuple[str, ...], list[Judged]] = {}
        for judged in batch:
            groups.setdefault(judged.plan.questions, []).append(judged)
        context = self._context()
        try:
            if self._classifier is None:
                self._classifier = self._classifier_factory()  # built once, reused, closed in aclose
            classifier = self._classifier
        except Exception as exc:  # noqa: BLE001 - e.g. missing API key: deliver as usual
            team_logger.warning("duplex: classifier unavailable ({}); delivering as usual", exc)
            return {}

        async def ask(questions: tuple[str, ...], items: list[Judged]) -> Any:
            ids = [judged.item.message_id for judged in items]
            try:
                return await classifier.classify(
                    questions, context, [judged.item.classifier_text() for judged in items]
                )
            except Exception as exc:  # noqa: BLE001 - failure means "deliver as usual"
                team_logger.warning("duplex: classify failed message_ids={} error={}; delivering as usual", ids, exc)
                return None

        results = await asyncio.gather(*(ask(questions, items) for questions, items in groups.items()))
        return dict(zip(groups, results))

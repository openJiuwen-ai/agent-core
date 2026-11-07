# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2025.
# All rights reserved.
"""Dual-queue buffer for steer/follow_up messages.

Bridges EventHandler -> Executor/Loop by providing two
async-safe queues:
- steering: drained by the executor before each
  inner invoke.
- follow_up: drained by outer task loop after
  each iteration completes.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Awaitable, Callable, List, Optional

from openjiuwen.core.common.logging import logger


@dataclass(slots=True)
class DeferredInput:
    """A follow-up input whose text is rendered at drain time, not at enqueue time.

    A queued snapshot rendered eagerly goes stale while the member is busy:
    by drain time it describes a state that no longer exists. Deferring the
    render makes the text reflect the moment it is actually consumed.

    Attributes:
        kind: Classification for policy rails (e.g. ``task-board``) so they
            can reason about the item without paying the render.
        renderer: Produces the text to deliver, or returns None when the
            state it would describe no longer needs saying — the item is
            then dropped silently.
        collapse_key: When set, drained deferred items sharing the key are
            idempotent snapshots of the same underlying state; only the
            latest survives the drain.
    """

    kind: str
    renderer: Callable[[], Awaitable[Optional[str]]]
    collapse_key: Optional[str] = None


async def resolve_follow_ups(items: list) -> List[str]:
    """Render deferred inputs and collapse idempotent snapshots.

    Plain strings pass through untouched. A :class:`DeferredInput` renders
    at this moment — consumption time — and drops out silently when its
    renderer returns None. Rendered items sharing a ``collapse_key`` are
    idempotent snapshots of the same state: they would all render the same
    text, so only the latest is rendered at all (a failing or slow render
    is paid once per key, not once per queued event) and kept at its own
    position in the batch. A renderer that raises drops its item with a
    warning — one bad render must not kill the whole drain (the queue
    drains on the round-done path; an escaping exception would stall the
    harness there).
    """
    # Collapse first: only the latest item per collapse_key gets rendered.
    latest_pos_by_key = {}
    for idx, item in enumerate(items):
        if isinstance(item, DeferredInput) and item.collapse_key is not None:
            latest_pos_by_key[item.collapse_key] = idx
    resolved: List[str] = []
    for idx, item in enumerate(items):
        if not isinstance(item, DeferredInput):
            resolved.append(item)
            continue
        if item.collapse_key is not None and latest_pos_by_key[item.collapse_key] != idx:
            continue
        try:
            text = await item.renderer()
        except Exception as e:
            logger.warning(
                "deferred follow-up render failed, item dropped (kind=%s): %s",
                item.kind,
                e,
            )
            continue
        if text is not None:
            resolved.append(text)
    return resolved


@dataclass
class LoopQueues:
    """Buffer between EventHandler and Executor/Loop.

    Attributes:
        steering: Queue for steer messages, drained
            by the executor before each invoke.
        follow_up: Queue for follow-up messages (plain text or
            :class:`DeferredInput`), drained by the outer task loop.
    """

    steering: asyncio.Queue = field(
        default_factory=asyncio.Queue
    )
    follow_up: asyncio.Queue = field(
        default_factory=asyncio.Queue
    )

    def push_steer(self, msg: str) -> None:
        """Push a steering message.

        Args:
            msg: Steering instruction text.
        """
        self.steering.put_nowait(msg)

    def push_follow_up(self, msg) -> None:
        """Push a follow-up message.

        Args:
            msg: Follow-up content text, or a :class:`DeferredInput`
                rendered at drain time.
        """
        self.follow_up.put_nowait(msg)

    def has_follow_up(self) -> bool:
        """Return whether follow-up messages are pending."""
        return not self.follow_up.empty()

    def drain_steering(self) -> List[str]:
        """Drain all pending steering messages.

        Returns:
            List of steering message strings.
        """
        msgs: List[str] = []
        while not self.steering.empty():
            try:
                msgs.append(self.steering.get_nowait())
            except asyncio.QueueEmpty:
                break
        return msgs

    def drain_follow_up(self) -> List:
        """Drain all pending follow-up messages, unresolved.

        Items are plain strings or :class:`DeferredInput` objects; callers
        resolve through ``TaskLoopController.drain_follow_up`` (which applies
        :func:`resolve_follow_ups`), never through this raw drain.
        """
        msgs: List = []
        while not self.follow_up.empty():
            try:
                msgs.append(self.follow_up.get_nowait())
            except asyncio.QueueEmpty:
                break
        return msgs


__all__ = ["DeferredInput", "LoopQueues", "resolve_follow_ups"]

# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Priority work queues for one session-scoped interaction supervisor."""
from __future__ import annotations

import logging
from collections import deque
from typing import Optional

from openjiuwen.harness.schema.interaction import RoundWorkItem

logger = logging.getLogger(__name__)


class RoundWorkQueue:
    """Keep user and goal work in one scheduling domain.

    The supervisor is the only pop consumer.  Separate deques implement user
    priority without exposing separate host streaming APIs.
    """

    def __init__(self) -> None:
        self._user_queue: deque[RoundWorkItem] = deque()
        self._goal_queue: deque[RoundWorkItem] = deque()
        self._dequeued: Optional[RoundWorkItem] = None
        self._active: Optional[RoundWorkItem] = None

    def push_user(self, work: RoundWorkItem) -> None:
        if work.kind != "user":
            raise ValueError("push_user requires a user RoundWorkItem")
        self._user_queue.append(work)

    def push_goal(self, work: RoundWorkItem) -> bool:
        """Queue a goal item unless its goal id/revision already exists.

        ``False`` means pending, dequeued, or active work already represents
        the same goal generation.  The caller must not create a duplicate
        attempt in that case.
        """
        if work.kind != "goal":
            raise ValueError("push_goal requires a goal RoundWorkItem")
        goal_id = work.context.get("goal_id")
        revision = work.context.get("revision")
        if self.has_goal_work(goal_id=goal_id, revision=revision):
            return False
        self._goal_queue.append(work)
        logger.debug("[RoundWorkQueue] goal work queued: goal_id=%s revision=%s", goal_id, revision)
        return True

    def next_work(self) -> Optional[RoundWorkItem]:
        if self._user_queue:
            self._dequeued = self._user_queue.popleft()
            return self._dequeued
        if self._goal_queue:
            self._dequeued = self._goal_queue.popleft()
            return self._dequeued
        return None

    def mark_started(self, work: RoundWorkItem) -> None:
        if self._dequeued == work:
            self._dequeued = None
        self._active = work

    def mark_finished(self, work: RoundWorkItem) -> None:
        if self._active == work:
            self._active = None

    def has_pending_work(self) -> bool:
        return bool(self._user_queue or self._goal_queue)

    def has_pending_user_work(self) -> bool:
        return bool(self._user_queue)

    def has_goal_work(self, *, goal_id: object, revision: object) -> bool:
        def matches(work: Optional[RoundWorkItem]) -> bool:
            return (
                work is not None
                and work.kind == "goal"
                and work.context.get("goal_id") == goal_id
                and work.context.get("revision") == revision
            )

        return (
            any(matches(work) for work in self._goal_queue)
            or matches(self._dequeued)
            or matches(self._active)
        )

    def discard_goal_work(
        self,
        *,
        session_id: str,
        goal_id: Optional[str] = None,
    ) -> int:
        """Discard pending goal work owned by a cleared or replaced record."""
        before = len(self._goal_queue)
        self._goal_queue = deque(
            work
            for work in self._goal_queue
            if not (
                work.context.get("session_id") == session_id
                and (goal_id is None or work.context.get("goal_id") == goal_id)
            )
        )
        discarded = before - len(self._goal_queue)
        if discarded:
            logger.info(
                "[RoundWorkQueue] discarded %d pending goal work items: session=%s goal=%s",
                discarded,
                session_id,
                goal_id,
            )
        return discarded

    def discard_all_work(self) -> None:
        """Drop queued work when its output lease disconnects.

        The durable GoalRecord deliberately survives.  A later GOAL send
        recreates its next work item after a frontend has attached again.
        """
        self._user_queue.clear()
        self._goal_queue.clear()

    @property
    def active_work(self) -> Optional[RoundWorkItem]:
        return self._active


__all__ = ["RoundWorkQueue"]

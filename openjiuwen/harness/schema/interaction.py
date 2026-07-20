# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Interaction domain objects used by the session-scoped DeepAgent loop.

The objects in this module deliberately do not contain JiuwenSwarm protocol
types.  A session receives either a complete user ``inputs`` mapping or an
instruction to resume the current persistent goal.  It returns an output
consumer only when the calling request acquired the single consumer lease.
"""
from __future__ import annotations

import asyncio
import copy
import uuid
from dataclasses import dataclass, field
from enum import Enum
from contextlib import suppress
from typing import TYPE_CHECKING, Any, Dict, Literal, Optional

from openjiuwen.core.session.stream import OutputSchema

if TYPE_CHECKING:
    from openjiuwen.harness.deep_agent import DeepAgent

_OUTPUT_END = object()


class InteractionPhase(str, Enum):
    """Internal lifecycle phase of one session-scoped interaction loop."""

    IDLE = "idle"
    RUNNING = "running"
    TERMINATED = "terminated"


class InputDispatchMode(str, Enum):
    """Routing policy for a user input while a round is active."""

    FOLLOW_UP = "follow_up"
    STEER = "steer"


@dataclass(frozen=True)
class SendInputRequest:
    """A host request to dispatch user text through the interaction loop.

    ``inputs[\"query\"]`` is the only source of user text.  The complete
    mapping is retained so existing DeepAgent invocation metadata (for example
    conversation id and trusted directories) travels with the work item.
    """

    request_id: str
    inputs: Dict[str, object]
    mode: Optional[InputDispatchMode] = None


@dataclass(frozen=True)
class RoundWorkItem:
    """One unit of work consumed by the single interaction supervisor."""

    kind: Literal["user", "goal"]
    request_id: Optional[str]
    inputs: Dict[str, object]
    context: Dict[str, object] = field(default_factory=dict)
    is_follow_up: bool = False

    @classmethod
    def user(
        cls,
        *,
        request_id: Optional[str],
        inputs: Dict[str, object],
        is_follow_up: bool = False,
        reset_loop: bool = True,
    ) -> "RoundWorkItem":
        """Build an isolated user work item from host supplied inputs."""
        return cls(
            kind="user",
            request_id=request_id,
            inputs=copy.deepcopy(inputs),
            context={"reset_loop": reset_loop},
            is_follow_up=is_follow_up,
        )

    @classmethod
    def goal(
        cls,
        *,
        inputs: Dict[str, object],
        goal_id: str,
        revision: int,
        session_id: str,
    ) -> "RoundWorkItem":
        """Build a goal work item wholly owned by openjiuwen."""
        return cls(
            kind="goal",
            request_id=None,
            inputs=copy.deepcopy(inputs),
            context={
                "goal_id": goal_id,
                "revision": revision,
                "session_id": session_id,
                "reset_loop": True,
            },
        )

    @property
    def query(self) -> object:
        """The task-loop query; intentionally derived from ``inputs`` only."""
        return self.inputs["query"]

    @property
    def reset_loop(self) -> bool:
        return bool(self.context.get("reset_loop", True))


@dataclass
class ActiveInteractionRound:
    """Bookkeeping for the work item currently executed by the supervisor."""

    work: RoundWorkItem
    task_id: Optional[str] = None

    @property
    def run_kind(self) -> Literal["user", "goal"]:
        return self.work.kind

    @property
    def run_context(self) -> Dict[str, object]:
        return self.work.context

    @property
    def goal_id(self) -> Optional[str]:
        return self.work.context.get("goal_id")

    @property
    def revision(self) -> Optional[int]:
        return self.work.context.get("revision")


class InteractionEventType(str, Enum):
    """Interaction-originated events transported through an output stream."""

    GOAL_UPDATED = "goal.updated"
    EXECUTION_ERROR = "execution.error"


@dataclass(frozen=True)
class InteractionEvent:
    """Structured event emitted by the interaction output producer."""

    type: InteractionEventType
    payload: Dict[str, object] = field(default_factory=dict)

    def to_output_schema(self) -> OutputSchema:
        return OutputSchema(type=self.type.value, index=0, payload=copy.deepcopy(self.payload))

    @classmethod
    def goal_updated(cls, goal: Optional[Dict[str, object]]) -> InteractionEvent:
        return cls(
            type=InteractionEventType.GOAL_UPDATED,
            payload={"goal": copy.deepcopy(goal)},
        )

    @classmethod
    def execution_error(
        cls,
        *,
        code: str,
        message: str,
        goal: Optional[Dict[str, object]] = None,
    ) -> InteractionEvent:
        payload: Dict[str, object] = {"code": code, "message": message}
        if goal is not None:
            payload["goal"] = copy.deepcopy(goal)
        return cls(type=InteractionEventType.EXECUTION_ERROR, payload=payload)


@dataclass(frozen=True)
class RoundOutcome:
    """Result of one agent-executed interaction round.

    ``next_work`` is decided by the agent; DeepAgent is responsible for
    enqueueing it.
    """

    next_work: Optional[RoundWorkItem] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None


@dataclass
class OutputLease:
    """The one host currently allowed to consume interaction output."""

    token: str
    closed: asyncio.Event
    finishing: bool = False


class InteractionOutputStream:
    """Opaque, closeable handle returned to the owner of an output lease."""

    def __init__(self, agent: DeepAgent, lease: OutputLease) -> None:
        self._agent = agent
        self._lease = lease
        self._closed = False

    def __aiter__(self) -> InteractionOutputStream:
        return self

    async def __anext__(self) -> Any:
        if self._closed:
            raise StopAsyncIteration
        item = await self._agent.next_output(self._lease)
        if item is None:
            self._closed = True
            raise StopAsyncIteration
        return item

    async def aclose(self) -> None:
        await self.close()

    async def close(self, *, abort_active_round: bool = True) -> None:
        """Release this stream only; stale streams cannot release a newer one."""
        if self._closed:
            return
        self._closed = True
        await self._agent.detach_output(
            self._lease.token,
            abort_active_round=abort_active_round,
        )


class OutputLeaseManager:
    """Own the queue and atomically attach/detach its single consumer."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[Any] = asyncio.Queue()
        self._lock = asyncio.Lock()
        self._lease: Optional[OutputLease] = None
        self._closed = False

    def has_consumer(self) -> bool:
        return self._lease is not None and not self._lease.closed.is_set()

    def current_lease(self) -> Optional[OutputLease]:
        lease = self._lease
        if lease is None or lease.closed.is_set():
            return None
        return lease

    def current_token(self) -> Optional[str]:
        lease = self.current_lease()
        return lease.token if lease is not None else None

    async def attach(self) -> Optional[OutputLease]:
        async with self._lock:
            if self._closed or self.has_consumer():
                return None
            lease = OutputLease(token=uuid.uuid4().hex, closed=asyncio.Event())
            self._lease = lease
            return lease

    async def detach(self, token: str, *, discard_buffer: bool = True) -> bool:
        async with self._lock:
            lease = self._lease
            if lease is None or lease.token != token:
                return False
            self._lease = None
            lease.closed.set()
            if discard_buffer:
                self._drain_queue()
            return True

    async def finish_current(self) -> None:
        """End the current stream after already-forwarded chunks drain."""
        async with self._lock:
            lease = self._lease
            if lease is None or lease.closed.is_set() or lease.finishing:
                return
            lease.finishing = True
            self._queue.put_nowait(_OUTPUT_END)

    async def emit(self, item: Any, *, expected_token: Optional[str] = None) -> None:
        async with self._lock:
            if self._closed or not self.has_consumer():
                return
            if expected_token is not None and self.current_token() != expected_token:
                return
            if self._lease is not None and self._lease.finishing:
                return
            self._queue.put_nowait(item)

    async def next_item(self, lease: OutputLease) -> Optional[Any]:
        async with self._lock:
            if self._lease is not lease or lease.closed.is_set():
                return None

        queue_task = asyncio.create_task(self._queue.get())
        close_task = asyncio.create_task(lease.closed.wait())
        wait_tasks = {queue_task, close_task}
        try:
            done, pending = await asyncio.wait(
                wait_tasks, return_when=asyncio.FIRST_COMPLETED
            )
        except asyncio.CancelledError:
            # The caller that owned this lease has disappeared.  A detached
            # queue_task must not survive and steal the next lease's first
            # output item.
            for task in wait_tasks:
                if not task.done():
                    task.cancel()
            for task in wait_tasks:
                with suppress(asyncio.CancelledError, Exception):
                    await task
            raise
        for task in pending:
            task.cancel()
        for task in pending:
            with suppress(asyncio.CancelledError):
                await task

        if close_task in done or lease.closed.is_set():
            if queue_task in done:
                with suppress(Exception):
                    queue_task.result()
            return None
        item = queue_task.result()
        if item is _OUTPUT_END:
            async with self._lock:
                if self._lease is lease:
                    self._lease = None
                    lease.closed.set()
            return None
        return item

    async def shutdown(self) -> None:
        async with self._lock:
            self._closed = True
            if self._lease is not None:
                self._lease.closed.set()
                self._lease = None
            self._drain_queue()

    def _drain_queue(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return


__all__ = [
    "ActiveInteractionRound",
    "InteractionPhase",
    "RoundOutcome",
    "InputDispatchMode",
    "InteractionOutputStream",
    "OutputLease",
    "OutputLeaseManager",
    "SendInputRequest",
    "InteractionEvent",
    "InteractionEventType",
    "RoundWorkItem",
]

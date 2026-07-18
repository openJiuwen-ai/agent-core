# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Interaction domain objects used by the session-scoped DeepAgent loop.

The objects in this module deliberately do not contain JiuwenSwarm protocol
types.  A session receives either a complete user ``inputs`` mapping or an
instruction to resume the current persistent goal.  It returns an output
consumer only when the calling request acquired the single consumer lease.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Literal, Optional

from openjiuwen.core.session.stream import OutputSchema


class InteractionPhase(str, Enum):
    """Internal lifecycle phase of one session-scoped interaction loop."""

    IDLE = "idle"
    RUNNING = "running"
    TERMINATED = "terminated"


class InputDispatchKind(str, Enum):
    """The two public input dispatch operations."""

    USER = "user"
    GOAL = "goal"


class InputDispatchMode(str, Enum):
    """Routing policy for a user input while a round is active."""

    AUTO = "auto"
    FOLLOW_UP = "follow_up"
    STEER = "steer"


@dataclass(frozen=True)
class SendInputRequest:
    """A host request to dispatch work through the interaction loop.

    ``inputs[\"query\"]`` is the only source of user text.  The complete
    mapping is retained so existing DeepAgent invocation metadata (for example
    conversation id and trusted directories) travels with the work item.
    Goal dispatch never accepts host supplied inputs.
    """

    kind: InputDispatchKind
    request_id: str
    inputs: Optional[Dict[str, object]] = None
    mode: InputDispatchMode = InputDispatchMode.AUTO


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
    def goal_updated(cls, goal: Optional[Dict[str, object]]) -> "InteractionEvent":
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
    ) -> "InteractionEvent":
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


__all__ = [
    "ActiveInteractionRound",
    "InteractionPhase",
    "RoundOutcome",
    "InputDispatchKind",
    "InputDispatchMode",
    "SendInputRequest",
    "InteractionEvent",
    "InteractionEventType",
    "RoundWorkItem",
]

# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Pure delivery policy: the F_117 scenario table. No I/O, no runtime imports.

Flow for a caller::

    p = plan(source, idle=...)
    answers = ask_classifier(p.questions) if p.questions else None  # None on failure
    decision = resolve(source, p, answers, idle=...)
    action = final_action(decision.action, idle_now=...)

Two principles:

1. The urgency question only makes an action lighter. A directed message to a
   busy target keeps INTERRUPT only for INTERRUPT with p >= INTERRUPT_THRESHOLD;
   a broadcast keeps STEER only on the same condition. Otherwise APPEND.
   The relevance question is asked for broadcasts only; SKIP with
   p >= SKIP_THRESHOLD records the message without waking the target.
2. A failed or missing answer delivers as usual: idle -> DELIVER_NOW, busy -> APPEND.

User messages and leader broadcasts are never judged, never downgraded and
never rate limited.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

INTERRUPT_THRESHOLD = 0.9
SKIP_THRESHOLD = 0.8
BROADCAST_WINDOW_SECONDS = 10.0

URGENCY = "urgency"
RELEVANCE = "relevance"
INTERRUPT = "INTERRUPT"
APPEND = "APPEND"
DELIVER = "DELIVER"
SKIP = "SKIP"


class MessageSource(str, Enum):
    """Scenario of one inbound mailbox row, from SDK signals only."""

    USER = "user"
    DIRECT = "direct"
    GROUP_MENTION = "group_mention"
    GROUP_PLAIN = "group_plain"
    BROADCAST = "broadcast"
    LEADER_BROADCAST = "leader_broadcast"
    SYSTEM = "system"


class DeliveryAction(str, Enum):
    """How a message reaches the target (F_117 terminology)."""

    INTERRUPT = "interrupt"  # 打断重规划
    STEER = "steer"  # 插入
    APPEND = "append"  # 排队
    DELIVER_NOW = "deliver_now"  # 直接处理
    VISIBLE_ONLY = "visible_only"  # 只记录
    PASSTHROUGH = "passthrough"  # 原样投递


class AnswerLike(Protocol):
    """Structural match for ``system_one.ChoiceAnswer``."""

    @property
    def choice(self) -> str:
        """Selected label."""
        ...

    @property
    def probabilities(self) -> Mapping[str, float]:
        """Probability of every label."""
        ...


@dataclass(frozen=True)
class Plan:
    """Questions to ask, or the fixed action when none is needed."""

    questions: tuple[str, ...]
    action: DeliveryAction | None


@dataclass(frozen=True)
class Decision:
    """The resolved action plus a log-friendly reason."""

    action: DeliveryAction
    reason: str


_DIRECTED = frozenset({MessageSource.DIRECT, MessageSource.GROUP_MENTION})
_UNJUDGED = frozenset({MessageSource.USER, MessageSource.LEADER_BROADCAST})


def plan(source: MessageSource, *, idle: bool) -> Plan:
    """Decide whether the classifier is needed and which questions it gets."""
    if source is MessageSource.SYSTEM:
        return Plan((), DeliveryAction.PASSTHROUGH)
    if source is MessageSource.GROUP_PLAIN:
        return Plan((), DeliveryAction.VISIBLE_ONLY)
    if source is MessageSource.BROADCAST:
        return Plan((RELEVANCE,) if idle else (RELEVANCE, URGENCY), None)
    if idle:
        return Plan((), DeliveryAction.DELIVER_NOW)
    if source in _UNJUDGED:
        return Plan((), DeliveryAction.STEER)
    return Plan((URGENCY,), None)


def _p(answer: AnswerLike | None, label: str) -> float | None:
    return None if answer is None else float(answer.probabilities.get(label, 0.0))


def _confident(answer: AnswerLike | None, label: str, threshold: float) -> bool:
    p = _p(answer, label)
    return answer is not None and answer.choice == label and p is not None and p >= threshold


def resolve(
    source: MessageSource,
    planned: Plan,
    answers: Mapping[str, AnswerLike] | None,
    *,
    idle: bool,
) -> Decision:
    """Turn the classifier answers (or their absence) into one action."""
    if planned.action is not None:
        return Decision(planned.action, f"scene={source.value} idle={int(idle)} rule=fixed")
    answers = answers or {}
    urgency = answers.get(URGENCY)
    if source in _DIRECTED:
        keep = _confident(urgency, INTERRUPT, INTERRUPT_THRESHOLD)
        action = DeliveryAction.INTERRUPT if keep else DeliveryAction.APPEND
        return Decision(action, _reason(source, idle, urgency=urgency))
    relevance = answers.get(RELEVANCE)
    if _confident(relevance, SKIP, SKIP_THRESHOLD):
        action = DeliveryAction.VISIBLE_ONLY
    elif idle:
        action = DeliveryAction.DELIVER_NOW
    elif _confident(urgency, INTERRUPT, INTERRUPT_THRESHOLD):
        action = DeliveryAction.STEER
    else:
        action = DeliveryAction.APPEND
    return Decision(action, _reason(source, idle, urgency=urgency, relevance=relevance))


def final_action(action: DeliveryAction, *, idle_now: bool) -> DeliveryAction:
    """Re-read rule: a waking action on a target that is idle now becomes DELIVER_NOW;
    DELIVER_NOW on a target that became busy is queued (APPEND), never steered."""
    if idle_now and action in (DeliveryAction.INTERRUPT, DeliveryAction.STEER, DeliveryAction.APPEND):
        return DeliveryAction.DELIVER_NOW
    if not idle_now and action is DeliveryAction.DELIVER_NOW:
        return DeliveryAction.APPEND
    return action


def broadcast_allowed(last_sent_at: float | None, now: float) -> bool:
    """One broadcast per sender per BROADCAST_WINDOW_SECONDS."""
    return last_sent_at is None or now - last_sent_at >= BROADCAST_WINDOW_SECONDS


def _reason(source: MessageSource, idle: bool, **answers: AnswerLike | None) -> str:
    parts = [f"scene={source.value}", f"idle={int(idle)}"]
    for name, answer in answers.items():
        if answer is None:
            parts.append(f"{name}=failed")
        else:
            probs = ",".join(f"{k}:{v:.3f}" for k, v in answer.probabilities.items())
            parts.append(f"{name}={answer.choice}({probs})")
    return " ".join(parts)

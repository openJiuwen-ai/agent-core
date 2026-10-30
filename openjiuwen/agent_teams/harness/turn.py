# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Trajectory turn identity for one Team member.

A turn is one piece of work a member takes on: it opens when a round starts
from idle (or drains follow-ups into a fresh round) and it survives everything
that keeps working on the same input — a steer folded into the running round, a
pause and its resume, an ``InteractiveInput`` answering the member's own
question, the one-shot retry of a crashed round, and a task-plan continuation.

A Team run cycle shares one ``team.<name>`` trace across every member, so the
trace cannot tell one member turn from the next. ``openjiuwen.turn.id`` is what
does, and ``openjiuwen.turn.number`` is what the turn is shown as. Both live in
the member's session state rather than on the harness, because the harness is
rebuilt every run cycle and a resume may land on a rebuilt one; numbering keeps
climbing across restarts as long as the session state is committed.

This module is free of OpenTelemetry on purpose: the harness mints the identity
whether or not the optional observability extra is installed, and the
observability layer maps it onto span attributes.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Protocol

from openjiuwen.core.common.logging import logger

# Session-state key holding the member's latest turn. Namespaced so it cannot
# collide with the harness's own ``deepagent`` blob, which pause / abort roll
# back — a rolled-back counter would hand out a number twice.
MEMBER_TURN_STATE_KEY = "trajectory_member_turn"

_TURN_ID_FIELD = "turn_id"
_TURN_NUMBER_FIELD = "turn_number"


class TurnStateStore(Protocol):
    """The session-state surface the turn identity is kept in."""

    def get_state(self, key: str) -> Any:
        """Return the value stored under ``key``, or None when absent."""
        ...

    def update_state(self, data: dict) -> None:
        """Merge ``data`` into the session state."""
        ...


@dataclass(frozen=True, slots=True)
class MemberTurn:
    """Identity of one trajectory turn of a Team member.

    Attributes:
        turn_id: Stable identity shared by every span carrying part of this
            turn, across rounds and traces.
        turn_number: 1-based turn number within the member's session.
    """

    turn_id: str
    turn_number: int

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-friendly dict for session state.

        Returns:
            Dict with the turn id and turn number.
        """
        return {
            _TURN_ID_FIELD: self.turn_id,
            _TURN_NUMBER_FIELD: self.turn_number,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "MemberTurn | None":
        """Restore from a dict previously produced by :meth:`to_dict`.

        Args:
            data: Serialized identity. Anything malformed reads as absent, so a
                corrupt snapshot degrades to "open a new turn" instead of
                failing the round.

        Returns:
            The restored identity, or None when ``data`` holds no usable one.
        """
        if not isinstance(data, dict):
            return None
        turn_id = str(data.get(_TURN_ID_FIELD) or "").strip()
        raw_number = data.get(_TURN_NUMBER_FIELD)
        if not turn_id or not isinstance(raw_number, int) or raw_number < 1:
            return None
        return cls(turn_id=turn_id, turn_number=raw_number)


def load_member_turn(store: TurnStateStore | None) -> MemberTurn | None:
    """Read the member's latest turn, treating any failure as absent.

    Args:
        store: Session state holding the identity; None reads as absent.

    Returns:
        The latest persisted turn, or None.
    """
    if store is None:
        return None
    try:
        return MemberTurn.from_dict(store.get_state(MEMBER_TURN_STATE_KEY))
    except Exception as exc:
        logger.debug("[MemberTurn] turn state read failed: %s", exc)
        return None


def resolve_member_turn(store: TurnStateStore | None, *, continues_turn: bool) -> tuple[MemberTurn, bool]:
    """Resolve the turn a round that is about to start belongs to.

    Args:
        store: Session state to read and stage the identity in. None resolves
            without persistence, which still yields a usable identity.
        continues_turn: Whether the round keeps working on the input of the
            member's latest turn (steer / resume / interrupt answer / retry /
            task-plan continuation) instead of taking on a new one. The caller
            owns that decision; a continuation with no turn on record opens one.

    Returns:
        The turn to stamp on the round, and whether the round opened it. Only an
        opened turn advanced the counter, so only it needs a durable commit.
    """
    known = load_member_turn(store)
    if continues_turn and known is not None:
        return known, False
    previous_number = known.turn_number if known is not None else 0
    opened = MemberTurn(turn_id=uuid.uuid4().hex, turn_number=previous_number + 1)
    if store is not None:
        try:
            store.update_state({MEMBER_TURN_STATE_KEY: opened.to_dict()})
        except Exception as exc:
            # Failing a round over an observability counter would cost the
            # member its work; the identity is still stamped this round.
            logger.debug("[MemberTurn] turn state write failed: %s", exc)
    return opened, True


__all__ = [
    "MEMBER_TURN_STATE_KEY",
    "MemberTurn",
    "TurnStateStore",
    "load_member_turn",
    "resolve_member_turn",
]

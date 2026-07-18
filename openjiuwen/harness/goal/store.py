# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""SessionGoalStore — persist GoalRecord via Session state.

Uses ``Session.get_state()`` / ``Session.update_state()`` as the single
source of truth for goal state. JiuwenSwarm must not write sidecar JSON,
SQLite, or history as an alternative goal state source.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from openjiuwen.harness.goal.schema import GoalRecord

if TYPE_CHECKING:
    from openjiuwen.core.session.agent import Session

logger = logging.getLogger(__name__)

SESSION_GOAL_RECORD_KEY = "harness.goal.record"


class SessionGoalStore:
    """Session-scoped goal persistence backed by Session state API."""

    def __init__(self, session: Session) -> None:
        self._session = session

    @property
    def session_id(self) -> str:
        return self._session.get_session_id()

    def load(self) -> Optional[GoalRecord]:
        """Load the current GoalRecord from session state."""
        data = self._session.get_state(SESSION_GOAL_RECORD_KEY)
        if data is None:
            return None
        if not isinstance(data, dict):
            logger.warning(
                "[GoalStore] Invalid goal state type %s in session %s, clearing",
                type(data).__name__,
                self.session_id,
            )
            self.clear()
            return None
        try:
            return GoalRecord.from_dict(data)
        except (KeyError, ValueError, TypeError) as exc:
            logger.warning(
                "[GoalStore] Failed to deserialize goal state in session %s: %s, clearing",
                self.session_id,
                exc,
            )
            self.clear()
            return None

    def save(self, record: GoalRecord) -> None:
        """Persist a GoalRecord to session state."""
        self._session.update_state({SESSION_GOAL_RECORD_KEY: record.to_dict()})

    def clear(self) -> None:
        """Remove the GoalRecord from session state."""
        self._session.update_state({SESSION_GOAL_RECORD_KEY: None})


class DictGoalStore:
    """Lightweight store backed by a shared dict[session_id, GoalRecord].

    Used when the caller owns the goal records dict directly (e.g. a
    GoalManager managing multiple sessions) instead of going through
    Session.state.
    """

    def __init__(self, records: dict[str, GoalRecord], session_id: str) -> None:
        self._records = records
        self._session_id = session_id

    @property
    def session_id(self) -> str:
        return self._session_id

    def load(self) -> Optional[GoalRecord]:
        return self._records.get(self._session_id)

    def save(self, record: GoalRecord) -> None:
        self._records[self._session_id] = record

    def clear(self) -> None:
        self._records.pop(self._session_id, None)


__all__ = ["DictGoalStore", "SESSION_GOAL_RECORD_KEY", "SessionGoalStore"]

# coding: utf-8
"""Tests for GoalRecord persistence stores."""
from __future__ import annotations

from typing import Any

from openjiuwen.core.session.state.base import InMemoryStateLike
from openjiuwen.harness.goal.schema import GoalRecord, GoalStatus
from openjiuwen.harness.goal.store import SESSION_GOAL_RECORD_KEY, SessionGoalStore


class FakeSession:
    """Dict-backed session used for simple store plumbing tests."""

    def __init__(self, session_id: str = "test-session") -> None:
        self._session_id = session_id
        self._state: dict[str, Any] = {}

    def get_session_id(self) -> str:
        return self._session_id

    def get_state(self, key: str) -> Any:
        return self._state.get(key)

    def update_state(self, value: dict[str, Any]) -> None:
        self._state.update(value)


class MergeSession:
    """Session state that mirrors production None-as-delete merge semantics."""

    def __init__(self, session_id: str = "test-session") -> None:
        self._session_id = session_id
        self._state = InMemoryStateLike()

    def get_session_id(self) -> str:
        return self._session_id

    def get_state(self, key: str) -> Any:
        return self._state.get(key)

    def update_state(self, value: dict[str, Any]) -> None:
        self._state.update(value)


def test_session_store_save_load_and_clear() -> None:
    session = FakeSession()
    store = SessionGoalStore(session)
    record = GoalRecord.create(session_id=session.get_session_id(), objective="write a report")

    store.save(record)
    loaded = store.load()

    assert loaded is not None
    assert loaded.goal_id == record.goal_id
    assert loaded.objective == "write a report"

    store.clear()
    assert store.load() is None
    assert session.get_state(SESSION_GOAL_RECORD_KEY) is None


def test_session_store_drops_malformed_persistence_data() -> None:
    session = FakeSession()
    store = SessionGoalStore(session)

    session.update_state({SESSION_GOAL_RECORD_KEY: "not-a-record"})
    assert store.load() is None
    assert session.get_state(SESSION_GOAL_RECORD_KEY) is None

    session.update_state({SESSION_GOAL_RECORD_KEY: {"goal_id": "g"}})
    assert store.load() is None
    assert session.get_state(SESSION_GOAL_RECORD_KEY) is None


def test_session_store_pause_round_trip_survives_none_as_delete() -> None:
    """Session merge deletes None keys; from_dict must still load paused goals."""
    session = MergeSession()
    store = SessionGoalStore(session)
    record = GoalRecord.create(session_id=session.get_session_id(), objective="write a report")
    store.save(record)

    loaded = store.load()
    assert loaded is not None
    loaded.settle_active_time(keep_active=False)
    loaded.status = GoalStatus.PAUSED
    store.save(loaded)

    raw = session.get_state(SESSION_GOAL_RECORD_KEY)
    assert raw is not None
    # Production session treats None as deletion — key may be absent after pause.
    assert raw.get("active_started_at") is None

    paused = store.load()
    assert paused is not None
    assert paused.status is GoalStatus.PAUSED
    assert paused.active_started_at is None
    assert paused.time_used_seconds >= 0

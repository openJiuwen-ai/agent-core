# coding: utf-8
"""Tests for GoalRecord persistence stores."""
from __future__ import annotations

from typing import Any

from openjiuwen.harness.goal.schema import GoalRecord
from openjiuwen.harness.goal.store import DictGoalStore, SESSION_GOAL_RECORD_KEY, SessionGoalStore


class FakeSession:
    def __init__(self, session_id: str = "test-session") -> None:
        self._session_id = session_id
        self._state: dict[str, Any] = {}

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


def test_dict_store_is_scoped_to_its_session() -> None:
    records: dict[str, GoalRecord] = {}
    first = DictGoalStore(records, "first")
    second = DictGoalStore(records, "second")

    first_record = GoalRecord.create(session_id="first", objective="first objective")
    second_record = GoalRecord.create(session_id="second", objective="second objective")
    first.save(first_record)
    second.save(second_record)

    first.clear()

    assert first.load() is None
    assert second.load() is second_record

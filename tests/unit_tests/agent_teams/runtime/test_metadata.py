# coding: utf-8
"""Tests for the per-team session state namespace helpers."""

from __future__ import annotations

from openjiuwen.agent_teams.runtime.metadata import (
    TEAM_DB_STATE_CREATED,
    TEAM_DB_STATE_KEY,
    TEAMS_KEY,
    merge_team_db_state,
    merge_team_namespace,
    read_team_db_state,
    read_team_names_in_session,
    read_team_namespace,
    read_teams_bucket,
    remove_team_namespace,
    write_team_namespace,
)


class _StubSession:
    """Minimal session that mimics agent_team session's state API."""

    def __init__(self) -> None:
        self.state: dict = {}

    def update_state(self, data: dict) -> None:
        self.state.update(data)

    def get_state(self, key=None):
        if key is None:
            return self.state
        return self.state.get(key)


def test_read_teams_bucket_returns_empty_dict_when_absent():
    session = _StubSession()
    assert read_teams_bucket(session) == {}


def test_write_team_namespace_creates_bucket():
    session = _StubSession()
    write_team_namespace(session, "alpha", {"spec": {"team_name": "alpha"}})
    assert TEAMS_KEY in session.state
    assert session.state[TEAMS_KEY] == {"alpha": {"spec": {"team_name": "alpha"}}}


def test_read_team_namespace_returns_bucket_when_present():
    session = _StubSession()
    write_team_namespace(session, "alpha", {"spec": {"team_name": "alpha"}})
    bucket = read_team_namespace(session, "alpha")
    assert bucket == {"spec": {"team_name": "alpha"}}


def test_read_team_namespace_returns_none_when_missing():
    session = _StubSession()
    write_team_namespace(session, "alpha", {"spec": {}})
    assert read_team_namespace(session, "beta") is None


def test_merge_team_namespace_preserves_existing_keys():
    session = _StubSession()
    write_team_namespace(session, "alpha", {"spec": {"team_name": "alpha"}})
    merge_team_namespace(session, "alpha", {"lifecycle": "paused"})
    bucket = read_team_namespace(session, "alpha")
    assert bucket == {"spec": {"team_name": "alpha"}, "lifecycle": "paused"}


def test_merge_team_namespace_creates_bucket_if_absent():
    session = _StubSession()
    merge_team_namespace(session, "alpha", {"lifecycle": "running"})
    bucket = read_team_namespace(session, "alpha")
    assert bucket == {"lifecycle": "running"}


def test_merge_team_db_state_preserves_existing_bucket():
    session = _StubSession()
    write_team_namespace(session, "alpha", {"spec": {"team_name": "alpha"}})
    merge_team_db_state(session, "alpha", TEAM_DB_STATE_CREATED)
    bucket = read_team_namespace(session, "alpha")
    assert bucket == {
        "spec": {"team_name": "alpha"},
        TEAM_DB_STATE_KEY: TEAM_DB_STATE_CREATED,
    }
    assert read_team_db_state(session, "alpha") == TEAM_DB_STATE_CREATED


def test_read_team_db_state_returns_none_when_absent_or_not_string():
    session = _StubSession()
    write_team_namespace(session, "alpha", {TEAM_DB_STATE_KEY: 1})
    assert read_team_db_state(session, "alpha") is None
    assert read_team_db_state(session, "beta") is None


def test_multi_team_buckets_are_independent():
    session = _StubSession()
    write_team_namespace(session, "alpha", {"spec": {"team_name": "alpha"}})
    write_team_namespace(session, "beta", {"spec": {"team_name": "beta"}})
    assert sorted(read_team_names_in_session(session)) == ["alpha", "beta"]
    assert read_team_namespace(session, "alpha") != read_team_namespace(session, "beta")


def test_merge_one_team_does_not_touch_another():
    session = _StubSession()
    write_team_namespace(session, "alpha", {"spec": {"team_name": "alpha"}})
    write_team_namespace(session, "beta", {"spec": {"team_name": "beta"}})
    merge_team_namespace(session, "alpha", {"lifecycle": "paused"})
    assert read_team_namespace(session, "beta") == {"spec": {"team_name": "beta"}}


def test_remove_team_namespace_drops_bucket():
    session = _StubSession()
    write_team_namespace(session, "alpha", {"spec": {}})
    write_team_namespace(session, "beta", {"spec": {}})
    assert remove_team_namespace(session, "alpha") is True
    assert read_team_names_in_session(session) == ["beta"]


def test_remove_team_namespace_returns_false_when_absent():
    session = _StubSession()
    assert remove_team_namespace(session, "alpha") is False

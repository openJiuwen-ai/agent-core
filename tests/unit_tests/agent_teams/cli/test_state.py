# coding: utf-8
"""TeamCliState transitions used by the slash-command handlers."""

from __future__ import annotations

import pytest
from rich.console import Console

from openjiuwen.agent_teams.cli.spec_loader import SpecRegistry
from openjiuwen.agent_teams.cli.state import TeamCliState

pytestmark = pytest.mark.level0


def _make_state() -> TeamCliState:
    return TeamCliState(spec_registry=SpecRegistry(), console=Console())


def test_initial_state_has_no_active_routing_target():
    state = _make_state()

    assert state.active_team_name is None
    assert state.active_session_id is None
    assert state.pending_team_name is None
    assert state.pending_session_id is None
    assert state.stream_handles == {}
    assert state.watch_bindings == {}
    assert state.history_session_ids == {}


def test_set_active_promotes_pending_to_active():
    state = _make_state()
    state.set_pending("alpha", "s1")

    assert state.pending_team_name == "alpha"

    state.set_active("alpha", "s1")

    assert state.active_team_name == "alpha"
    assert state.active_session_id == "s1"
    assert state.pending_team_name is None
    assert state.pending_session_id is None


def test_set_active_with_none_clears_routing_target():
    state = _make_state()
    state.set_active("alpha", "s1")
    state.set_active(None, None)

    assert state.active_team_name is None
    assert state.active_session_id is None


def test_remember_session_records_distinct_pairs():
    state = _make_state()
    state.remember_session("alpha", "s1")
    state.remember_session("alpha", "s2")
    state.remember_session("beta", "s1")

    assert state.known_sessions("alpha") == ["s1", "s2"]
    assert state.known_sessions("beta") == ["s1"]
    assert state.known_sessions("missing") == []


def test_remember_session_dedupes_repeats():
    state = _make_state()
    state.remember_session("alpha", "s1")
    state.remember_session("alpha", "s1")

    assert state.known_sessions("alpha") == ["s1"]

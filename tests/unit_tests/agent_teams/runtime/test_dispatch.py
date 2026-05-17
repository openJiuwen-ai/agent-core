# coding: utf-8
"""Truth-table coverage for the dispatch decision."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from openjiuwen.agent_teams.runtime.dispatch import (
    RunActionKind,
    decide_run_action,
)
from openjiuwen.agent_teams.runtime.pool import (
    ActiveTeam,
    RuntimeState,
)


def _pool_entry(
    team_name: str = "alpha",
    session_id: str = "session-x",
    state: RuntimeState = RuntimeState.RUNNING,
) -> ActiveTeam:
    return ActiveTeam(
        team_name=team_name,
        agent=MagicMock(name="TeamAgent"),
        current_session_id=session_id,
        state=state,
    )


def test_create_when_team_missing_and_session_clean():
    action = decide_run_action(
        team_in_db=False,
        team_in_session=False,
        pool_entry=None,
        target_session_id="s1",
        target_team_name="alpha",
    )
    assert action.kind is RunActionKind.CREATE
    assert action.require_spec is True


def test_reject_inconsistent_when_pool_has_team_db_does_not():
    action = decide_run_action(
        team_in_db=False,
        team_in_session=False,
        pool_entry=_pool_entry(),
        target_session_id="s1",
        target_team_name="alpha",
    )
    assert action.kind is RunActionKind.REJECT_INCONSISTENT
    assert action.reason and "pool" in action.reason


def test_reject_orphaned_when_session_bucket_exists_but_no_team_row():
    action = decide_run_action(
        team_in_db=False,
        team_in_session=True,
        pool_entry=None,
        target_session_id="s1",
        target_team_name="alpha",
    )
    assert action.kind is RunActionKind.REJECT_ORPHANED
    assert action.reason and "not in DB" in action.reason


def test_new_team_in_session_for_cold_path_with_db_team_no_session_bucket():
    action = decide_run_action(
        team_in_db=True,
        team_in_session=False,
        pool_entry=None,
        target_session_id="s1",
        target_team_name="alpha",
    )
    assert action.kind is RunActionKind.NEW_TEAM_IN_SESSION
    assert action.require_spec is False


def test_cold_recover_when_db_and_bucket_present_no_pool():
    action = decide_run_action(
        team_in_db=True,
        team_in_session=True,
        pool_entry=None,
        target_session_id="s1",
        target_team_name="alpha",
    )
    assert action.kind is RunActionKind.COLD_RECOVER


def test_reject_running_when_pool_active_on_same_session():
    pool = _pool_entry(team_name="alpha", session_id="s1", state=RuntimeState.RUNNING)
    action = decide_run_action(
        team_in_db=True,
        team_in_session=True,
        pool_entry=pool,
        target_session_id="s1",
        target_team_name="alpha",
    )
    assert action.kind is RunActionKind.REJECT_RUNNING
    assert action.reason and "interact" in action.reason


def test_resume_from_pause_when_pool_paused_on_same_session():
    pool = _pool_entry(team_name="alpha", session_id="s1", state=RuntimeState.PAUSED)
    action = decide_run_action(
        team_in_db=True,
        team_in_session=True,
        pool_entry=pool,
        target_session_id="s1",
        target_team_name="alpha",
    )
    assert action.kind is RunActionKind.RESUME_FROM_PAUSE


def test_raises_when_pool_entry_on_other_session():
    """Cross-session pool entries must be torn down by Manager.activate
    before dispatch runs; reaching this branch is a contract violation."""
    pool = _pool_entry(team_name="alpha", session_id="other")
    with pytest.raises(RuntimeError, match="dispatch invariant"):
        decide_run_action(
            team_in_db=True,
            team_in_session=True,
            pool_entry=pool,
            target_session_id="s1",
            target_team_name="alpha",
        )


@pytest.mark.parametrize(
    "team_in_db,team_in_session,has_pool,session_match,paused,expected",
    [
        (False, False, False, False, False, RunActionKind.CREATE),
        # REJECT_INCONSISTENT triggers on (not team_in_db && pool present)
        # regardless of session match — keep session_match True so the
        # cross-session assertion below does not preempt the case.
        (False, False, True, True, False, RunActionKind.REJECT_INCONSISTENT),
        (False, True, False, False, False, RunActionKind.REJECT_ORPHANED),
        (True, False, False, False, False, RunActionKind.NEW_TEAM_IN_SESSION),
        (True, True, False, False, False, RunActionKind.COLD_RECOVER),
        (True, True, True, True, False, RunActionKind.REJECT_RUNNING),
        (True, True, True, True, True, RunActionKind.RESUME_FROM_PAUSE),
    ],
)
def test_truth_table_full_coverage(
    team_in_db: bool,
    team_in_session: bool,
    has_pool: bool,
    session_match: bool,
    paused: bool,
    expected: RunActionKind,
):
    target_session = "s1"
    pool = None
    if has_pool:
        # ``activate`` only forwards same-session pool entries to dispatch
        # now, so the truth table no longer exercises cross-session pool.
        assert session_match, "cross-session pool entries are torn down before dispatch"
        pool_state = RuntimeState.PAUSED if paused else RuntimeState.RUNNING
        pool = _pool_entry(session_id=target_session, state=pool_state)
    action = decide_run_action(
        team_in_db=team_in_db,
        team_in_session=team_in_session,
        pool_entry=pool,
        target_session_id=target_session,
        target_team_name="alpha",
    )
    assert action.kind is expected

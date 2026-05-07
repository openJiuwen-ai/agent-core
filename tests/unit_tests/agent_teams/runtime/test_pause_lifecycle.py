# coding: utf-8
"""Coverage for the PAUSED lifecycle additions in Step 4.

The Pause path needs ``MemberStatus.PAUSED`` to participate in the
member transition table (so recovery can flip PAUSED back to RESTARTING)
and the ``lifecycle`` hint to round-trip through the team namespace.
The deeper integration with ``CoordinationKernel.pause`` is exercised
in the persistent-team integration tests; here we only assert the
contracts that pause relies on.
"""

from __future__ import annotations

from openjiuwen.agent_teams.runtime.metadata import (
    merge_team_namespace,
    read_team_namespace,
    write_team_namespace,
)
from openjiuwen.agent_teams.schema.status import (
    MEMBER_TRANSITIONS,
    MemberStatus,
    is_valid_transition,
)


class _StubSession:
    def __init__(self) -> None:
        self.state: dict = {}

    def update_state(self, data: dict) -> None:
        self.state.update(data)

    def get_state(self, key=None):
        if key is None:
            return self.state
        return self.state.get(key)


def test_paused_to_restarting_transition_is_valid():
    assert is_valid_transition(
        MemberStatus.PAUSED,
        MemberStatus.RESTARTING,
        MEMBER_TRANSITIONS,
    )


def test_paused_to_ready_transition_is_valid():
    assert is_valid_transition(
        MemberStatus.PAUSED,
        MemberStatus.READY,
        MEMBER_TRANSITIONS,
    )


def test_ready_and_busy_can_enter_paused():
    assert is_valid_transition(MemberStatus.READY, MemberStatus.PAUSED, MEMBER_TRANSITIONS)
    assert is_valid_transition(MemberStatus.BUSY, MemberStatus.PAUSED, MEMBER_TRANSITIONS)


def test_paused_cannot_jump_back_to_busy_directly():
    assert not is_valid_transition(
        MemberStatus.PAUSED,
        MemberStatus.BUSY,
        MEMBER_TRANSITIONS,
    )


def test_lifecycle_hint_round_trips_through_team_namespace():
    session = _StubSession()
    write_team_namespace(session, "t1", {"spec": {"team_name": "t1"}})
    merge_team_namespace(session, "t1", {"lifecycle": "paused"})
    assert read_team_namespace(session, "t1")["lifecycle"] == "paused"


def test_lifecycle_hint_overrides_previous_value():
    session = _StubSession()
    write_team_namespace(session, "t1", {"lifecycle": "running"})
    merge_team_namespace(session, "t1", {"lifecycle": "paused"})
    assert read_team_namespace(session, "t1")["lifecycle"] == "paused"

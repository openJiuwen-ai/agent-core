# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The tracker's policy channel (F_122).

A framework-built team's leader receives its collaboration policy as a
``<team-policy>`` block through ``TeamContextTracker`` rather than as a
``build_team`` result. The channel is keyed on the sha256 of the delivered
text: delivered once, again only when the text changes, and part of the full
``snapshot_text`` a current-state channel registers.
"""

from __future__ import annotations

import pytest

from openjiuwen.agent_teams.inbound_render import render_team_policy_inline
from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.agent_teams.team_context import TEAM_CONTEXT_STATE_KEY, TeamContextTracker
from tests.test_logger import logger

_POLICY = render_team_policy_inline(body="## Role\nPlan, never execute.")
_POLICY_V2 = render_team_policy_inline(body="## Role\nPlan, never execute. Verify everything.")


class _StubSession:
    """Session stand-in holding the per-member state bucket."""

    def __init__(self) -> None:
        self.state: dict = {}

    def get_state(self, key: str | None = None):
        """Read one key out of the session state."""
        if key is None:
            return dict(self.state)
        return self.state.get(key)

    def update_state(self, data: dict) -> None:
        """Shallow-merge into the session state."""
        self.state.update(data)

    async def commit(self) -> None:
        """Nothing to flush in the stub."""
        return None


def _tracker(policy_text: str | None) -> TeamContextTracker:
    # No backend: the identity channel degrades to the private prompt only and
    # the roster / team-info channels stay silent, which isolates the policy
    # channel under test.
    return TeamContextTracker(
        team_backend=None,
        member_name="leader1",
        role=TeamRole.LEADER,
        member_prompt="",
        policy_text=policy_text,
    )


@pytest.mark.asyncio
@pytest.mark.level0
async def test_policy_is_delivered_once_and_recorded_by_hash():
    session = _StubSession()
    tracker = _tracker(_POLICY)

    first = await tracker.pending_text(session)
    logger.info("first delivery: %s", first)
    assert first is not None
    assert _POLICY in first
    await tracker.commit(session)
    assert "policy_hash" in session.state[TEAM_CONTEXT_STATE_KEY]

    assert await tracker.pending_text(session) is None


@pytest.mark.asyncio
@pytest.mark.level1
async def test_changed_policy_text_is_delivered_again():
    """An evolved policy template re-renders to new text, and that is re-delivered."""
    session = _StubSession()
    first = _tracker(_POLICY)
    await first.pending_text(session)
    await first.commit(session)

    rebuilt = _tracker(_POLICY_V2)
    again = await rebuilt.pending_text(session)
    assert again is not None
    assert _POLICY_V2 in again
    await rebuilt.commit(session)

    assert await rebuilt.pending_text(session) is None


@pytest.mark.asyncio
@pytest.mark.level1
async def test_uncommitted_delivery_is_offered_again():
    """Committing only after delivery: a lost delivery is retried, not forgotten."""
    session = _StubSession()
    tracker = _tracker(_POLICY)

    assert await tracker.pending_text(session) is not None
    # No commit — the baseline must not have moved.
    assert TEAM_CONTEXT_STATE_KEY not in session.state
    assert await tracker.pending_text(session) is not None


@pytest.mark.asyncio
@pytest.mark.level0
async def test_no_policy_text_keeps_the_channel_silent():
    session = _StubSession()
    tracker = _tracker(None)

    # The identity channel still speaks (member_name); the policy channel does not.
    pending = await tracker.pending_text(session)
    assert pending is not None
    assert "<team-policy>" not in pending
    snapshot = await tracker.snapshot_text()
    assert snapshot is not None
    assert "<team-policy>" not in snapshot


@pytest.mark.asyncio
@pytest.mark.level1
async def test_snapshot_carries_the_policy():
    """A current-state channel re-registering the full state gets the policy too."""
    tracker = _tracker(_POLICY)
    snapshot = await tracker.snapshot_text()
    assert snapshot is not None
    assert _POLICY in snapshot


@pytest.mark.level0
def test_inline_policy_block_has_no_namespace_declaration():
    """The in-process form is the bare element around the unescaped body."""
    block = render_team_policy_inline(body="a & `b` < c")
    assert block.startswith("<team-policy>\n")
    assert block.endswith("\n</team-policy>")
    assert "tools=" not in block
    assert "tool-namespace" not in block
    assert "a & `b` < c" in block

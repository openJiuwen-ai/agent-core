# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import pytest

from openjiuwen.core.context_engine.context.cancellation import (
    CancellationCause,
    clear_cancellation_cause,
    mark_cancellation_cause,
    reset_cancellation_causes,
    resolve_cancellation_cause,
)


@pytest.fixture(autouse=True)
def _clean_cancellation_causes():
    reset_cancellation_causes()
    yield
    reset_cancellation_causes()


def test_mark_and_resolve_exact_session():
    assert mark_cancellation_cause("session-1", CancellationCause.PAUSE)
    assert resolve_cancellation_cause("session-1") is CancellationCause.PAUSE
    assert resolve_cancellation_cause("session-2") is None


def test_sub_sessions_inherit_parent_cause():
    mark_cancellation_cause("session-1", CancellationCause.CANCEL)
    assert resolve_cancellation_cause("session-1:member-a") is CancellationCause.CANCEL
    assert resolve_cancellation_cause("session-1_sub_agent") is CancellationCause.CANCEL
    # Near-miss prefixes must not match.
    assert resolve_cancellation_cause("session-11") is None
    assert resolve_cancellation_cause("session-2:member-a") is None


def test_last_mark_wins():
    mark_cancellation_cause("session-1", CancellationCause.PAUSE)
    mark_cancellation_cause("session-1", CancellationCause.CANCEL)
    assert resolve_cancellation_cause("session-1") is CancellationCause.CANCEL


def test_inheritance_prefers_most_specific_ancestor():
    """Longest matching key wins, regardless of insertion order.

    A team-level PAUSE marked before a member-level CANCEL must not shadow
    it when a deeper sub-session resolves (and vice versa).
    """
    mark_cancellation_cause("session-1", CancellationCause.PAUSE)
    mark_cancellation_cause("session-1:member-a", CancellationCause.CANCEL)
    assert resolve_cancellation_cause("session-1:member-a_sub_x") is CancellationCause.CANCEL

    # Reverse combination: parent CANCEL must not shadow a child PAUSE.
    mark_cancellation_cause("session-2", CancellationCause.CANCEL)
    mark_cancellation_cause("session-2:member-b", CancellationCause.PAUSE)
    assert resolve_cancellation_cause("session-2:member-b_sub_y") is CancellationCause.PAUSE
    # Sessions under the bare parent keep inheriting the parent's mark.
    assert resolve_cancellation_cause("session-2:member-c") is CancellationCause.CANCEL


def test_clear_drops_the_mark():
    mark_cancellation_cause("session-1", CancellationCause.PAUSE)
    clear_cancellation_cause("session-1")
    assert resolve_cancellation_cause("session-1") is None


def test_resolve_does_not_consume_the_mark():
    """One mark stays resolvable for every cancellation it causes.

    A pause can cancel several processors (including sub-sessions); each
    resolution must still see the cause. The residual window ends only via
    clear / overwrite / TTL expiry — resume paths must call clear.
    """
    mark_cancellation_cause("session-1", CancellationCause.PAUSE)
    assert resolve_cancellation_cause("session-1") is CancellationCause.PAUSE
    assert resolve_cancellation_cause("session-1:member-a") is CancellationCause.PAUSE
    assert resolve_cancellation_cause("session-1") is CancellationCause.PAUSE
    clear_cancellation_cause("session-1")
    assert resolve_cancellation_cause("session-1") is None
    assert resolve_cancellation_cause("session-1:member-a") is None


def test_expired_mark_is_not_resolved():
    mark_cancellation_cause("session-1", CancellationCause.PAUSE, ttl_seconds=0)
    assert resolve_cancellation_cause("session-1") is None


def test_invalid_inputs_are_rejected():
    assert mark_cancellation_cause("", CancellationCause.PAUSE) is False
    assert mark_cancellation_cause(None, CancellationCause.PAUSE) is False
    assert mark_cancellation_cause("session-1", "bogus") is False
    assert mark_cancellation_cause("  ", CancellationCause.PAUSE) is False
    assert resolve_cancellation_cause(None) is None
    assert resolve_cancellation_cause("") is None


def test_cause_accepts_plain_string():
    mark_cancellation_cause("session-1", "cancel")
    assert resolve_cancellation_cause("session-1") is CancellationCause.CANCEL

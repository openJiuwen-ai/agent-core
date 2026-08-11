# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""TeamHarness must actually expose round-scoped steering.

This file exists because of a bug, and the bug is worth stating. Team steering
was written against ``NativeHarness``, calling ``harness.steer`` and reading
``harness.active_round``. But ``TeamAgent.harness`` returns a ``MemberRuntime``,
and the default one is a **TeamHarness** -- a plain adapter with no
``__getattr__`` that forwards a hand-picked set of methods. It forwarded
``send`` and not ``steer``, so ``steer_leader`` raised ``AttributeError`` for
every real team.

Nothing caught it: the runtime tests built the harness surface out of
``MagicMock`` and ``SimpleNamespace``, so they asserted against attributes they
had invented rather than ones that exist. A mock cannot tell you a method is
missing -- it grows one on demand. These tests use the real class.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.agent_teams.harness.team_harness import TeamHarness


def _harness(*, native, cycle_active: bool = True) -> TeamHarness:
    """A TeamHarness over a stand-in native, with the run cycle live or not."""
    harness = TeamHarness.__new__(TeamHarness)
    harness._native = native
    harness._active_agent_session = object() if cycle_active else None
    return harness


def test_the_forwarding_surface_carries_round_steering() -> None:
    """The regression guard, asserted on the class rather than an instance.

    A mock of this object would satisfy any call; the point is that the real
    class declares these. If either disappears, Team steering fails as an
    AttributeError at runtime -- which the caller turns into a generic
    "exception", not a usable reason.
    """
    assert hasattr(TeamHarness, "steer_round")
    assert hasattr(TeamHarness, "active_round")
    # And it is not accidentally satisfied by a catch-all.
    assert "__getattr__" not in TeamHarness.__dict__


@pytest.mark.asyncio
async def test_steering_forwards_the_id_and_the_expected_round() -> None:
    native = MagicMock()
    native.steer = AsyncMock(return_value=True)

    result = await _harness(native=native).steer_round(
        "prefer the async client", steer_id="req-9", expected_round_id=7
    )

    assert result is True
    native.steer.assert_awaited_once_with(
        "prefer the async client", steer_id="req-9", expected_round_id=7
    )


@pytest.mark.asyncio
async def test_steering_before_start_is_refused_not_raised() -> None:
    """Matches abort/pause: a dead cycle is a harmless impossibility.

    ``send`` raises here instead, because sending to a team that was never
    started is a caller error. Steering one is not -- the round it meant to
    reach may simply have ended, which is exactly what False reports.
    """
    native = MagicMock()
    native.steer = AsyncMock(return_value=True)
    harness = _harness(native=native, cycle_active=False)

    assert await harness.steer_round("too late") is False
    native.steer.assert_not_awaited()


def test_active_round_is_none_when_no_cycle_is_live() -> None:
    """The caller uses this to decide whether to take a gate ticket at all.

    Reading it off a torn-down native would raise; reading a stale round would
    send a steer to a harness that cannot take it.
    """
    native = MagicMock()
    native.active_round = object()

    assert _harness(native=native).active_round is native.active_round
    assert _harness(native=native, cycle_active=False).active_round is None


def test_round_steering_is_a_capability_not_part_of_every_runtime() -> None:
    """Two contracts, two Protocols, and the split is load-bearing both ways.

    ``ExternalCliRuntime.steer`` is declared ``-> None`` and buffers when no turn
    is in flight -- a buffered steer silently becomes the next turn's input,
    which is the promotion this path exists to prevent. Had the new method reused that
    name, a CLI-backed leader would satisfy the type and violate the contract,
    and its falsy ``None`` would be reported as a rejection *after* delivery.

    Declaring ``steer_round`` on ``MemberRuntime`` instead was also wrong, and a
    pre-existing conformance test said so: ``ExternalCliRuntime`` stopped being a
    ``MemberRuntime``, which is false -- it is a perfectly good one that simply
    cannot steer a round. Hence a separate capability Protocol.
    """
    from openjiuwen.agent_teams.agent.member_runtime import (
        MemberRuntime,
        SupportsRoundSteering,
    )
    from openjiuwen.agent_teams.external.runtime import ExternalCliRuntime

    assert issubclass(TeamHarness, SupportsRoundSteering)
    assert not issubclass(ExternalCliRuntime, SupportsRoundSteering)
    # And the capability split did not cost the CLI runtime its base conformance.
    assert hasattr(ExternalCliRuntime, "steer")
    assert "steer_round" not in dir(MemberRuntime)

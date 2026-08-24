# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Evolved member_prompt must reach the identity body (F_84 gap #1).

The read-side overlay writes the evolved prompt onto ``member.prompt`` via
``TeamBackend.get_member``. ``TeamContextTracker._identity_body`` already calls
``get_member`` (to read ``display_name``) but used to hand the constructor
snapshot ``self._member_prompt`` (the spec-time DB baseline, pre-evolution) to
``build_identity_text`` instead of the overlaid ``member.prompt``. So an evolved
``member_prompt.md`` (``evolved: true``) reached ``member.prompt`` but was
thrown away at the last step: the identity body kept showing the baseline.

These tests lock the injection: with an overlaid member, the evolved token
reaches the rendered identity body; without a backend (or a non-evolved
baseline), the constructor snapshot still carries through as a fallback.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.agent_teams.team_context import TeamContextTracker

# A marker an evolved prompt body carries; the baseline never has it.
_EVOLVED_TOKEN = "EVOLVED_TOKEN_8422"
_BASELINE_PROMPT = "预配置成员提示词基线"
_EVOLVED_PROMPT = f"{_EVOLVED_TOKEN} 预配置成员提示词"


class _FakeBackend:
    """Serves a single member whose ``prompt`` reflects the overlay.

    Mirrors the contract ``TeamContextTracker._identity_body`` relies on:
    ``get_member(name)`` returns the member row (already overlaid by the team's
    ``_overlay_member`` in the real path) or ``None`` when the row is absent.
    """

    def __init__(self, *, member: Any | None) -> None:
        self._member = member

    async def get_member(self, member_name: str):  # noqa: D401
        """Return the overlaid member row, regardless of the name asked."""
        return self._member

    # The tracker only touches ``get_member`` on the identity path; the other
    # probe methods are never called here but keep the fake back-compat with
    # the wider TeamBackend protocol for future cases.
    async def get_team_updated_at(self) -> int:
        return 1

    async def get_members_max_updated_at(self) -> int:
        return 1

    async def get_team_info(self):
        return SimpleNamespace(team_name="demo", display_name="Demo", desc="")

    async def list_members(self):
        return [self._member] if self._member is not None else []


def _tracker(*, backend: Any | None, member_prompt: str) -> TeamContextTracker:
    """Build a tracker scoped to one member, the way the rail factory does."""
    return TeamContextTracker(
        team_backend=backend,
        member_name="worker-a",
        role=TeamRole.TEAMMATE,
        display_name="WorkerA",
        member_prompt=member_prompt,
        language="cn",
    )


async def _identity_body(tracker: TeamContextTracker) -> str | None:
    """Drive the one-shot identity channel the way ``pending_text`` does."""
    baseline: dict[str, Any] = {}
    updated: dict[str, Any] = dict(baseline)
    return await tracker._identity_body(baseline, updated)


@pytest.mark.asyncio
@pytest.mark.level1
async def test_evolved_prompt_reaches_identity_body():
    """F_84 gap #1: overlaid member.prompt (evolved) must be in the body."""
    member = SimpleNamespace(
        member_name="worker-a",
        display_name="WorkerA",
        desc="预配置成员描述",
        prompt=_EVOLVED_PROMPT,
        role="teammate",
    )
    # Constructor snapshot is the pre-evolution baseline — what the rail wires.
    tracker = _tracker(backend=_FakeBackend(member=member), member_prompt=_BASELINE_PROMPT)
    body = await _identity_body(tracker)

    assert body is not None
    assert _EVOLVED_TOKEN in body, "evolved prompt did not reach identity body"
    assert _BASELINE_PROMPT not in body, "baseline snapshot leaked past the overlay"


@pytest.mark.asyncio
@pytest.mark.level1
async def test_non_evolved_prompt_still_carries_through():
    """Guard: when the overlay value equals the baseline, the body keeps it."""
    member = SimpleNamespace(
        member_name="worker-a",
        display_name="WorkerA",
        desc="预配置成员描述",
        prompt=_BASELINE_PROMPT,
        role="teammate",
    )
    tracker = _tracker(backend=_FakeBackend(member=member), member_prompt=_BASELINE_PROMPT)
    body = await _identity_body(tracker)

    assert body is not None
    assert _BASELINE_PROMPT in body


@pytest.mark.asyncio
@pytest.mark.level1
async def test_no_backend_falls_back_to_constructor_snapshot():
    """Guard: without a backend (member never fetched) the snapshot is used."""
    tracker = _tracker(backend=None, member_prompt=_BASELINE_PROMPT)
    body = await _identity_body(tracker)

    assert body is not None
    assert _BASELINE_PROMPT in body


@pytest.mark.asyncio
@pytest.mark.level1
async def test_backend_returns_none_member_emits_nothing():
    """Guard: a None member row suppresses the identity body, no crash."""
    tracker = _tracker(backend=_FakeBackend(member=None), member_prompt=_BASELINE_PROMPT)
    body = await _identity_body(tracker)

    assert body is None

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
from openjiuwen.agent_teams.team_context import TEAM_CONTEXT_STATE_KEY, TeamContextTracker

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

    async def get_member_updated_at_state(
        self, member_name: str, field: str
    ) -> tuple[int, bool]:
        """Single-member mtime probe the identity body re-announce path uses.

        Returns ``(mtime, present)``. ``present=True`` (a stamped
        ``updated_at``) lets the wall-clock comparison run; the fake moves the
        probe via :meth:`set_prompt_mtime`. ``present=False`` is exposed
        separately via :meth:`set_prompt_updated_at_absent` to exercise the
        blank-field "must update" path.
        """
        return getattr(self, "_prompt_mtime", 0), getattr(
            self, "_prompt_present", True
        )

    def set_prompt_mtime(self, mtime: int) -> None:
        """Move the prompt mtime so the identity body's probe sees a change."""
        self._prompt_mtime = mtime
        self._prompt_present = True

    def set_prompt_updated_at_absent(self) -> None:
        """Make the probe report a blank ``updated_at`` (present=False)."""
        self._prompt_mtime = 0
        self._prompt_present = False

    async def stamp_member_prompt_updated_at(self, member_name: str, ts: int) -> None:
        """Record the stamped timestamp so the next probe mirrors a real md."""
        self._prompt_mtime = ts
        self._prompt_present = True

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


# ─── member_prompt re-announce on same-session resume (事 1) ──────────────
# Once the identity body has been delivered, the constants never change, so a
# hand-evolved member_prompt re-announces *only* the prompt subsection. The
# probe is the md file's ``updated_at``; when it moves past the baseline the
# delta path renders ``## 私有工作约定`` + the evolved body, without restating
# member_name / display_name / member_workspace_path.

_MEMBER_NAME_LINE = "你的 member_name"
_DISPLAY_NAME_LINE = "你的 display_name"
_WORKSPACE_LINE = "你的私有工作区"


def _baseline_with_identity_emitted(prompt_mtime: int = 1) -> dict[str, Any]:
    """Baseline after a first identity body has been committed."""
    return {
        "identity_emitted": True,
        "member_prompt_mtime": prompt_mtime,
        "team_info_mtime": 0,
        "roster_mtime": 0,
    }


@pytest.mark.asyncio
@pytest.mark.level1
async def test_emitted_prompt_mtime_move_re_announces_only_prompt_delta():
    """事 1: evolved member_prompt re-announces without restating constants."""
    member = SimpleNamespace(
        member_name="worker-a",
        display_name="WorkerA",
        desc="预配置成员描述",
        prompt=_EVOLVED_PROMPT,
        role="teammate",
    )
    backend = _FakeBackend(member=member)
    tracker = _tracker(backend=backend, member_prompt=_BASELINE_PROMPT)
    # Baseline carries the emitted flag + the *old* prompt mtime; the md edit
    # moved the probe past it.
    baseline = _baseline_with_identity_emitted(prompt_mtime=1)
    updated: dict[str, Any] = dict(baseline)
    backend.set_prompt_mtime(2)
    body = await tracker._identity_body(baseline, updated)

    assert body is not None
    assert _EVOLVED_TOKEN in body, "evolved prompt did not re-reach the body"
    # Re-announce carries the prompt heading + body, NOT the constants.
    assert "## 私有工作约定" in body
    assert _MEMBER_NAME_LINE not in body, "constants must not be restated"
    assert _DISPLAY_NAME_LINE not in body
    assert _WORKSPACE_LINE not in body
    # The probe advanced the baseline mtime so the next call (mtime unchanged)
    # re-announces nothing.
    assert updated["member_prompt_mtime"] == 2


@pytest.mark.asyncio
@pytest.mark.level1
async def test_emitted_prompt_mtime_unchanged_re_announces_nothing():
    """事 1 guard: no md move → one-shot still holds → return None."""
    member = SimpleNamespace(
        member_name="worker-a",
        display_name="WorkerA",
        desc="预配置成员描述",
        prompt=_EVOLVED_PROMPT,
        role="teammate",
    )
    backend = _FakeBackend(member=member)
    tracker = _tracker(backend=backend, member_prompt=_BASELINE_PROMPT)
    # mtime matches the baseline — no move.
    baseline = _baseline_with_identity_emitted(prompt_mtime=2)
    updated: dict[str, Any] = dict(baseline)
    backend.set_prompt_mtime(2)
    body = await tracker._identity_body(baseline, updated)

    assert body is None
    assert "member_prompt_mtime" not in updated or updated["member_prompt_mtime"] == baseline["member_prompt_mtime"]


# ─── blank updated_at forces a one-shot re-delivery (事 1, blank-field path) ─
# A hand-evolved member_prompt whose frontmatter carries no ``updated_at``
# (present=False) must re-announce exactly once: the delta fires, a single
# timestamp T is stamped into the file and the baseline, and the next probe
# (present=True, mtime=T == baseline) re-announces nothing — never a loop.

@pytest.mark.asyncio
@pytest.mark.level1
async def test_emitted_blank_updated_at_re_announces_once():
    """事 1: blank updated_at (present=False) forces a one-shot re-announce."""
    member = SimpleNamespace(
        member_name="worker-a",
        display_name="WorkerA",
        desc="预配置成员描述",
        prompt=_EVOLVED_PROMPT,
        role="teammate",
    )
    backend = _FakeBackend(member=member)
    tracker = _tracker(backend=backend, member_prompt=_BASELINE_PROMPT)
    # Baseline carries a prior stamped mtime; the hand-edit blanked the field.
    baseline = _baseline_with_identity_emitted(prompt_mtime=100)
    updated: dict[str, Any] = dict(baseline)
    backend.set_prompt_updated_at_absent()  # present=False, mtime=0

    body = await tracker._identity_body(baseline, updated)

    assert body is not None
    assert _EVOLVED_TOKEN in body, "blank updated_at must still re-deliver the evolved prompt"
    assert "## 私有工作约定" in body
    # The stamper recorded T, and the baseline carries the same T (not 0, not
    # the old 100) so the next probe mirrors a real md write.
    assert updated["member_prompt_mtime"] == backend._prompt_mtime
    assert backend._prompt_present is True


@pytest.mark.asyncio
@pytest.mark.level1
async def test_emitted_blank_updated_at_does_not_loop_after_stamp():
    """事 1: after the stamp, the next probe (mtime==baseline) re-announces nothing."""
    member = SimpleNamespace(
        member_name="worker-a",
        display_name="WorkerA",
        desc="预配置成员描述",
        prompt=_EVOLVED_PROMPT,
        role="teammate",
    )
    backend = _FakeBackend(member=member)
    tracker = _tracker(backend=backend, member_prompt=_BASELINE_PROMPT)
    # First call: blank field → re-announce + stamp T into baseline.
    baseline = _baseline_with_identity_emitted(prompt_mtime=100)
    updated: dict[str, Any] = dict(baseline)
    backend.set_prompt_updated_at_absent()
    first_body = await tracker._identity_body(baseline, updated)
    assert first_body is not None
    stamped_mtime = updated["member_prompt_mtime"]

    # Second call: the probe now reports present=True, mtime=stamped_mtime (the
    # fake mirrors the stamp). baseline carries the same T → no re-fire.
    second_updated: dict[str, Any] = dict(updated)
    second_body = await tracker._identity_body(updated, second_updated)

    assert second_body is None, "after the stamp, the probe must not re-fire"
    assert second_updated["member_prompt_mtime"] == stamped_mtime


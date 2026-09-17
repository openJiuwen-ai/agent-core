# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Evolved team_card desc must re-deliver the team-info block.

``TeamContextTracker._team_info_body`` probes the ``team_card.md``
``updated_at`` and re-announces the team-info block when it moves. A
hand-evolved ``team_card.md`` whose frontmatter carries a *blank*
``updated_at`` (``present=False``) never moved the probe on its own: the old
single-value :meth:`get_team_updated_at` floored on the DB column
(``max(db, 0)`` → ``db``), so the evolved team desc never reached any member
— unlike ``member_prompt`` which already had a write-side stamp fallback.

This fix symmetrically replicates that fallback for ``team_card``: a blank
field is treated as an explicit "must update" signal, the block re-announces
once, and a single timestamp T is stamped into ``team_card.md`` + the
baseline in one move (next probe reads ``present=True, mtime=T`` →
``T == baseline`` → no re-fire). These tests lock the four behaviours:
blank re-announces once and does not loop; a stamped mtime move
re-announces while an unchanged one does not; a team_card stamp does not
emit an empty roster-change (the roster probe advances but the member-set
diff is empty); and with the cache off the probe degrades to no-op.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from openjiuwen.agent_teams.schema.team import TeamRole
from openjiuwen.agent_teams.team_context import TeamContextTracker

# A marker the evolved team_card desc carries; the baseline never has it.
_EVOLVED_TOKEN = "EVOLVED_TEAM_DESC_828"
_BASELINE_DESC = "团队目标基线描述"
_EVOLVED_DESC = f"{_EVOLVED_TOKEN} 团队目标演进后描述"


class _FakeBackend:
    """Serves team metadata whose ``desc`` reflects the overlay.

    Mirrors the contract ``TeamContextTracker._team_info_body`` relies on:
    ``get_team_info()`` returns the team row (already overlaid by the team's
    ``_overlay_member`` / ``get_team_field`` path in the real code) and the
    ``updated_at`` probe is reported via :meth:`get_team_updated_at_state`
    with an explicit ``present`` flag.
    """

    def __init__(self, *, team_info: Any) -> None:
        self._team_info = team_info
        # team_card updated_at probe state. Default: a stamped stable value
        # so the wall-clock comparison path is exercised first; the blank and
        # move paths mutate it below.
        self._card_mtime: int = 1
        self._card_present: bool = True
        # Roster probe mirror: get_members_max_updated_at reads the team_card
        # md too, so a stamp on team_card advances it. Track the roster body
        # so the side-effect guard can assert no empty roster-change leaks.
        self._roster_members: list[Any] = []

    async def get_team_updated_at_state(self) -> tuple[int, bool]:
        """team_card mtime probe the team-info re-announce path uses.

        Returns ``(mtime, present)``. ``present=True`` (a stamped
        ``updated_at``) lets the wall-clock comparison run; the fake moves the
        probe via :meth:`set_card_mtime`. ``present=False`` is exposed via
        :meth:`set_card_updated_at_absent` to exercise the blank-field "must
        update" path.
        """
        return self._card_mtime, self._card_present

    def set_card_mtime(self, mtime: int) -> None:
        """Move the team_card mtime so the team-info probe sees a change."""
        self._card_mtime = mtime
        self._card_present = True

    def set_card_updated_at_absent(self) -> None:
        """Make the probe report a blank ``updated_at`` (present=False)."""
        self._card_mtime = 0
        self._card_present = False

    async def stamp_team_card_updated_at(self, ts: int) -> None:
        """Record the stamped timestamp so the next probe mirrors a real md."""
        self._card_mtime = ts
        self._card_present = True

    async def get_team_info(self):
        return self._team_info

    async def get_members_max_updated_at(self) -> int:
        """Roster probe — mirrors that a team_card stamp advances it."""
        return self._card_mtime

    async def list_members(self):
        return list(self._roster_members)

    # Identity-channel surface kept for protocol back-compat (not exercised
    # by the team-info tests, but the tracker may touch it via pending_text).
    async def get_member(self, member_name: str):
        return None

    async def get_member_updated_at_state(
        self, member_name: str, field: str
    ) -> tuple[int, bool]:
        return (0, True)

    async def stamp_member_prompt_updated_at(self, member_name: str, ts: int) -> None:
        return None


def _tracker(*, backend: Any | None) -> TeamContextTracker:
    """Build a tracker scoped to one member, the way the rail factory does."""
    return TeamContextTracker(
        team_backend=backend,
        member_name="worker-a",
        role=TeamRole.TEAMMATE,
        display_name="WorkerA",
        member_prompt="私有工作约定",
        language="cn",
    )


def _team_info(desc: str) -> SimpleNamespace:
    return SimpleNamespace(team_name="demo", display_name="Demo", desc=desc)


async def _team_info_body(tracker: TeamContextTracker) -> str | None:
    """Drive the one-shot team-info channel the way ``pending_text`` does."""
    baseline: dict[str, Any] = {}
    updated: dict[str, Any] = dict(baseline)
    return await tracker._team_info_body(baseline, updated)


def _baseline_with_info_emitted(info_mtime: int = 1) -> dict[str, Any]:
    """Baseline after a first team-info block has been committed."""
    return {
        "identity_emitted": True,
        "member_prompt_mtime": 0,
        "team_info_mtime": info_mtime,
        "roster_mtime": 0,
    }


# ─── blank updated_at forces a one-shot re-delivery ──────────────────────
# A hand-evolved team_card whose frontmatter carries no ``updated_at``
# (present=False) must re-announce exactly once: the block fires, a single
# timestamp T is stamped into the file and the baseline, and the next probe
# (present=True, mtime=T == baseline) re-announces nothing — never a loop.

@pytest.mark.asyncio
@pytest.mark.level1
async def test_blank_team_card_updated_at_re_announces_once():
    """Blank updated_at (present=False) forces a one-shot team-info re-announce."""
    backend = _FakeBackend(team_info=_team_info(_EVOLVED_DESC))
    tracker = _tracker(backend=backend)
    # Baseline carries a prior stamped mtime; the hand-edit blanked the field.
    baseline = _baseline_with_info_emitted(info_mtime=100)
    updated: dict[str, Any] = dict(baseline)
    backend.set_card_updated_at_absent()  # present=False, mtime=0

    body = await tracker._team_info_body(baseline, updated)

    assert body is not None
    assert _EVOLVED_TOKEN in body, "blank updated_at must re-deliver the evolved team desc"
    # The stamper recorded T, and the baseline carries the same T (not 0, not
    # the old 100) so the next probe mirrors a real md write.
    assert updated["team_info_mtime"] == backend._card_mtime
    assert backend._card_present is True


@pytest.mark.asyncio
@pytest.mark.level1
async def test_blank_team_card_updated_at_does_not_loop_after_stamp():
    """After the stamp, the next probe (mtime==baseline) re-announces nothing."""
    backend = _FakeBackend(team_info=_team_info(_EVOLVED_DESC))
    tracker = _tracker(backend=backend)
    # First call: blank field → re-announce + stamp T into baseline.
    baseline = _baseline_with_info_emitted(info_mtime=100)
    updated: dict[str, Any] = dict(baseline)
    backend.set_card_updated_at_absent()
    first_body = await tracker._team_info_body(baseline, updated)
    assert first_body is not None
    stamped_mtime = updated["team_info_mtime"]

    # Second call: the probe now reports present=True, mtime=stamped_mtime (the
    # fake mirrors the stamp). baseline carries the same T → no re-fire.
    second_updated: dict[str, Any] = dict(updated)
    second_body = await tracker._team_info_body(updated, second_updated)

    assert second_body is None, "after the stamp, the probe must not re-fire"
    assert second_updated["team_info_mtime"] == stamped_mtime


# ─── stamped updated_at: wall-clock comparison ───────────────────────────
# A stamped ``updated_at`` keeps the wall-clock comparison: only a moved
# mtime re-fires, and the baseline records the probe value so a stable file
# does not loop.

@pytest.mark.asyncio
@pytest.mark.level1
async def test_stamped_mtime_move_re_announces():
    """A moved stamped mtime re-announces the team-info block."""
    backend = _FakeBackend(team_info=_team_info(_EVOLVED_DESC))
    tracker = _tracker(backend=backend)
    baseline = _baseline_with_info_emitted(info_mtime=1)
    updated: dict[str, Any] = dict(baseline)
    backend.set_card_mtime(2)  # moved past the baseline

    body = await tracker._team_info_body(baseline, updated)

    assert body is not None
    assert _EVOLVED_TOKEN in body
    assert updated["team_info_mtime"] == 2


@pytest.mark.asyncio
@pytest.mark.level1
async def test_stamped_mtime_unchanged_re_announces_nothing():
    """An unchanged stamped mtime does not re-fire (one-shot holds)."""
    backend = _FakeBackend(team_info=_team_info(_EVOLVED_DESC))
    tracker = _tracker(backend=backend)
    baseline = _baseline_with_info_emitted(info_mtime=2)
    updated: dict[str, Any] = dict(baseline)
    backend.set_card_mtime(2)  # matches the baseline — no move

    body = await tracker._team_info_body(baseline, updated)

    assert body is None


# ─── side-effect guard: team_card stamp must not emit an empty roster-change ─
# The roster probe (``get_members_max_updated_at``) also maxes the team_card md
# mtime, so a team_card stamp advances it. But the roster *body* only renders
# member rows, and ``diff_roster`` compares member fields — a team_card-only
# edit changes no member, so the roster diff is empty → ``body is None`` →
# ``_roster_block`` returns ``None``. Asserted via ``pending_text`` (the join
# path), not the private channel: the assembled message must carry the
# team-info block and NO roster-change event.

@pytest.mark.asyncio
@pytest.mark.level1
async def test_team_card_stamp_does_not_emit_empty_roster_change():
    """A team_card re-announce must not drag an empty roster-change block."""
    backend = _FakeBackend(team_info=_team_info(_EVOLVED_DESC))
    tracker = _tracker(backend=backend)
    # Identity already emitted; baseline carries a prior stamped team-info
    # mtime; the hand-edit blanked the team_card field. No members exist.
    baseline = _baseline_with_info_emitted(info_mtime=100)
    backend.set_card_updated_at_absent()
    backend._roster_members = []  # no peers → diff_roster empty

    # Drive the full pending_text join path the way the coordinator does.
    session = _FakeSession(baseline)
    text = await tracker.pending_text(session)

    assert text is not None
    assert _EVOLVED_TOKEN in text, "evolved team desc must reach the message"
    # No empty roster-change / roster snapshot event leaks alongside it: the
    # roster block is None (no peers, empty diff) and so is not joined in.
    assert "roster-change" not in text, "empty roster-change must not leak"
    assert "<team-event" not in text or "roster" not in text.split("<team-event")[-1].split(">", 1)[0]


# ─── evolution off (cache=None) degrades to no-op ────────────────────────
# When the probe reports ``(0, True)`` — a stable mtime with present=True (no
# "must update" signal) — the wall-clock comparison runs against a 0 that
# equals the initial baseline → return None, no stamp, no re-announce. The
# real ``TeamBackend`` surfaces this pair when ``team_card.md`` is missing on
# a cache-on team (a cache-off team probes the DB column instead, which moves
# on a team mutation and re-delivers — the pre-evolution behaviour).

@pytest.mark.asyncio
@pytest.mark.level1
async def test_cache_off_degrades_to_no_re_announce():
    """A stable ``(0, True)`` probe (md missing / present, mtime 0) must not stamp or re-announce."""
    backend = _FakeBackend(team_info=_team_info(_EVOLVED_DESC))
    tracker = _tracker(backend=backend)
    # present=True, mtime=0 — a stable probe. Baseline carries 0 too.
    baseline = _baseline_with_info_emitted(info_mtime=0)
    updated: dict[str, Any] = dict(baseline)
    backend._card_mtime = 0
    backend._card_present = True

    body = await tracker._team_info_body(baseline, updated)

    assert body is None, "cache-off must not re-announce (degrades to no-op)"
    # No stamp side effect fired.
    assert backend._card_mtime == 0
    assert backend._card_present is True


# ─── minimal session stub for the pending_text path ──────────────────────

class _FakeSession:
    """Carries the team-context baseline the way a child AgentSession does.

    Mirrors the synchronous ``get_state`` / ``update_state`` + async
    ``commit`` protocol :meth:`TeamContextTracker._read_baseline` and
    :meth:`_persist` use (``get_state`` returns the value directly, not a
    coroutine; ``update_state`` takes a dict; ``commit`` flushes).
    """

    def __init__(self, baseline: dict[str, Any]) -> None:
        self._state: dict[str, Any] = {
            "team_prompt_context": dict(baseline),
        }

    def get_state(self, key: str, default: Any = None) -> Any:
        return self._state.get(key, default)

    def update_state(self, updates: dict[str, Any]) -> None:
        self._state.update(updates)

    async def commit(self) -> None:
        return None

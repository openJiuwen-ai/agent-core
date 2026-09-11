# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Scenario-8 regression: predefined member's workspace reused by a new team,
with the md ``updated_at`` (T0) older than the new team's DB row
``updated_at`` (T1).

User hypothesis under test: a predefined member's independent workspace was
written long ago (T0); a new team created a day later reuses it and inserts a
new teammate DB row (T1 > T0). Does the newer DB row shadow the reused md's
evolved body via the mtime probe, so the evolved prompt/desc fails to reach
the member and the DB baseline value is served instead?

These tests pin what the overlay / write-protection actually return in the
"workspace already exists, md older than DB" case, so the hypothesis is
confirmed or refuted by the assertions.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock

from openjiuwen.agent_teams.context import reset_session_id, set_session_id
from openjiuwen.agent_teams.messager import Messager
from openjiuwen.agent_teams.paths import (
    configure_openjiuwen_home,
    reset_openjiuwen_home,
    team_member_workspace_dir,
)
from openjiuwen.agent_teams.team_workspace.frontmatter import (
    body_sha256,
    read_frontmatter,
    write_frontmatter,
)
from openjiuwen.agent_teams.team_workspace.workspace_cache import WorkspaceCache
from openjiuwen.agent_teams.team_workspace.workspace_store import WorkspaceStore
from openjiuwen.agent_teams.tools.database import DatabaseConfig, DatabaseType, TeamDatabase
from openjiuwen.agent_teams.tools.team import TeamBackend

_TEAM_A = "reuse-team-a"
_TEAM_B = "reuse-team-b"
_MEMBER = "predefined-worker"
_EVO_MARKER = "EVO-REUSE-8B3F"


def _prompt_path(team: str) -> Path:
    return (
        team_member_workspace_dir(team, _MEMBER)
        / "prompts"
        / "identity"
        / "member_prompt.md"
    )


def _write_baseline_md(team: str, *, body: str, updated_at: int) -> None:
    """Mirror what ``_assemble_member_workspace`` → ``write_member_prompt``
    writes at member spawn time (T0): baseline body + baseline hash + stamp."""
    path = _prompt_path(team)
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = {
        "kind": "prompt",
        "name": "member_prompt",
        "baseline_sha256": body_sha256(body),
        "evolved": False,
        "updated_at": updated_at,
    }
    path.write_text(write_frontmatter(meta, body), encoding="utf-8")


def _evolve_md(team: str, *, marker: str, keep_updated_at: bool = True) -> int:
    """Evolution party edits the body without stamping updated_at (the ST
    injection behaviour). Returns the md's updated_at (unchanged)."""
    path = _prompt_path(team)
    meta, body = read_frontmatter(path.read_text(encoding="utf-8"))
    evolved = f"{body}\n\n<!-- evolved --> {marker}\n"
    new_meta = dict(meta)
    new_meta["baseline_sha256"] = "deadbeef"  # force divergence → evolved
    # keep updated_at as-is (T0) to model the "old md" premise.
    path.write_text(write_frontmatter(new_meta, evolved), encoding="utf-8")
    return int(meta.get("updated_at", 0))


@pytest_asyncio.fixture
async def db_env(tmp_path):
    """Isolated home + one real TeamDatabase shared across both teams."""
    home = tmp_path / "home"
    home.mkdir()
    configure_openjiuwen_home(str(home))
    token = set_session_id("reuse_probe_session")
    db = TeamDatabase(
        DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:")
    )
    await db.initialize()
    messager = AsyncMock(spec=Messager)
    try:
        yield {"db": db, "messager": messager, "home": home}
    finally:
        await db.close()
        reset_session_id(token)
        reset_openjiuwen_home()


async def _build_team_with_predefined(
    db, messager, team_name: str, *, desc: str, prompt: str
) -> TeamBackend:
    """build_team + spawn_member with spec-time desc/prompt (mirrors
    blueprint._register_predefined_members at team.py:1714-1741)."""
    from openjiuwen.agent_teams.schema.status import MemberStatus
    from openjiuwen.agent_teams.schema.team import TeamRole
    from openjiuwen.core.single_agent import AgentCard

    bk = TeamBackend(
        team_name=team_name,
        member_name="leader",
        is_leader=True,
        db=db,
        messager=messager,
        evolution_enabled=True,
    )
    await bk.build_team(
        display_name=team_name,
        desc=f"{team_name} goal",
        leader_display_name="L",
        leader_desc="leader persona",
    )
    await bk.spawn_member(
        member_name=_MEMBER,
        display_name="Predefined Worker",
        agent_card=AgentCard(
            id=f"{team_name}_{_MEMBER}", name="Predefined Worker", description=desc
        ),
        desc=desc,
        prompt=prompt,
        status=MemberStatus.UNSTARTED,
        role=TeamRole.TEAMMATE,
    )
    return bk


class _StubManager:
    """Minimal stand-in so TeamBackend.workspace_cache resolves to a cache."""

    def __init__(self, cache):
        self.workspace_cache = cache

    def attach_workspace_cache(self, cache):
        self.workspace_cache = cache


# ── The core hypothesis: newer DB row vs older evolved md ──────────────────


@pytest.mark.asyncio
@pytest.mark.level0
async def test_reused_evolved_md_not_shadowed_by_newer_db_row(db_env):
    """THE core test for the user's scenario.

    Team A: md baseline written at T0 (spec value), then evolved (body
    edited, updated_at stays T0). Team B: reuses the same md-on-disk (older,
    evolved), creates a NEW DB row at T1 > T0. Assert: the B-side overlay
    still serves the EVOLVED md body, not the DB baseline — despite T1 > T0.
    """
    db = db_env["db"]
    messager = db_env["messager"]

    # ── Team A: write the baseline md at T0=1000, then evolve (T0 stays) ──
    await _build_team_with_predefined(
        db, messager, _TEAM_A, desc="A-desc", prompt="A-prompt-baseline"
    )
    _write_baseline_md(_TEAM_A, body="A-prompt-baseline", updated_at=1_000)
    md_t0 = _evolve_md(_TEAM_A, marker=_EVO_MARKER, keep_updated_at=True)
    assert md_t0 == 1_000

    member_a = await db.member.get_member(_MEMBER, _TEAM_A)
    db_t0 = member_a.updated_at
    # DB carries the spec baseline, not the evolved marker.
    assert _EVO_MARKER not in (member_a.prompt or "")

    # ── Team B: SAME member, NEW DB row at T1 (now > md T0=1000) ──
    # Reuse scenario: team B's md already exists on disk (older, evolved).
    # Pre-seed team B's md with the same evolved file (mirrors symlink reuse
    # of the independent workspace).
    path_b = _prompt_path(_TEAM_B)
    path_b.parent.mkdir(parents=True, exist_ok=True)
    path_a = _prompt_path(_TEAM_A)
    path_b.write_bytes(path_a.read_bytes())

    bk_b = await _build_team_with_predefined(
        db, messager, _TEAM_B, desc="B-desc", prompt="B-prompt-baseline"
    )
    member_b = await db.member.get_member(_MEMBER, _TEAM_B)
    db_t1 = member_b.updated_at

    # THE premise: team B's DB row is newer than the md's updated_at.
    assert db_t1 >= md_t0, f"DB T1 ({db_t1}) should be >= md T0 ({md_t0})"
    # And the DB value is the spec baseline, NOT the evolved marker.
    assert _EVO_MARKER not in (member_b.prompt or ""), "DB row carries spec baseline"

    # ── Attach a fresh WorkspaceCache (mirrors new-process / cold-recover) ──
    cache_b = WorkspaceCache(WorkspaceStore(), _TEAM_B, language="cn")
    bk_b.attach_workspace_manager(_StubManager(cache_b))

    # ── THE overlay probe: get_member applies the cache overlay ──
    overlaid = await bk_b.get_member(_MEMBER)
    assert overlaid is not None
    assert _EVO_MARKER in (overlaid.prompt or ""), (
        f"EVOLVED md body was shadowed by the newer DB row! "
        f"prompt={overlaid.prompt!r} (md t0={md_t0}, db t1={db_t1})"
    )


# ── Write-side protection: evolved md not clobbered on reuse ───────────────


@pytest.mark.asyncio
@pytest.mark.level0
async def test_write_member_prompt_does_not_clobber_reused_evolved_md(db_env):
    """The write-side protection in the 'workspace already exists' case.

    Team B's spawn runs ``write_member_prompt`` with the spec-time prompt
    (B-baseline). The write side's ``_evolved_content`` protection must keep
    the evolved md body — even though the md is older than nothing-in-particular
    here, the hash divergence is the only thing that matters.
    """
    db = db_env["db"]
    messager = db_env["messager"]

    await _build_team_with_predefined(
        db, messager, _TEAM_A, desc="A-desc", prompt="A-prompt"
    )
    _write_baseline_md(_TEAM_A, body="A-prompt", updated_at=1_000)
    _evolve_md(_TEAM_A, marker=_EVO_MARKER, keep_updated_at=True)

    # Mirror reuse into team B's path, then ask the store to write team B's
    # baseline (spec value "B-prompt-baseline") — must NOT clobber.
    path_b = _prompt_path(_TEAM_B)
    path_b.parent.mkdir(parents=True, exist_ok=True)
    path_a = _prompt_path(_TEAM_A)
    path_b.write_bytes(path_a.read_bytes())

    store = WorkspaceStore()
    result = store.write_member_prompt(_TEAM_B, _MEMBER, "B-prompt-baseline")
    _, body_after = read_frontmatter(path_b.read_text(encoding="utf-8"))
    assert _EVO_MARKER in body_after, (
        "write-side evolution protection failed: evolved md was overwritten"
    )


# ── The mtime probe: max(db, md) — older md never suppresses ───────────────


@pytest.mark.asyncio
@pytest.mark.level0
async def test_roster_probe_takes_max_db_md(db_env):
    """``get_members_max_updated_at`` returns ``max(db_max, md_max)``. An older
    md (T0) does NOT suppress the DB value (T1): the probe floors on the DB.
    This refutes the 'older md makes the probe go stale' half of the hypothesis.
    """
    db = db_env["db"]
    messager = db_env["messager"]

    bk_a = await _build_team_with_predefined(
        db, messager, _TEAM_A, desc="A-desc", prompt="A-prompt"
    )
    _write_baseline_md(_TEAM_A, body="A-prompt", updated_at=1_000)
    _evolve_md(_TEAM_A, marker=_EVO_MARKER, keep_updated_at=True)
    member_a = await db.member.get_member(_MEMBER, _TEAM_A)
    db_t = member_a.updated_at

    cache_a = WorkspaceCache(WorkspaceStore(), _TEAM_A, language="cn")
    bk_a.attach_workspace_manager(_StubManager(cache_a))
    md_t = cache_a.get_member_updated_at(_MEMBER, "prompt")

    probe = await bk_a.get_members_max_updated_at()
    # Probe = max(db, md). md is older (1000) than db (now) but probe still = db.
    assert probe == max(db_t, md_t)
    assert probe >= db_t  # older md did not drag the probe below DB

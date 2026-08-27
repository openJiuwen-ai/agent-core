# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Verify the spawn_member fix: the db row carries the evolved md value (not
the spec baseline), and the leader's FIRST roster — rendered right after
build_team, before the member spawns — already serves the evolved value.

Background (see doc/analysis/evolvable-team/2026-08-27-spawn-member-db-evolved-value-plan.md):
the race was that ``spawn_member`` wrote the spec baseline to the db while the
member's evolved md was only read at member-spawn time (``_assemble_member_workspace``).
The leader's first roster landed between build_team and member spawn, hit an
empty cache, and fell back to the stale db baseline.

The fix moves the md read (``WorkspaceAssembler.write_member_identity``:
build link + read/protect evolved md + prime cache + return body) into
``spawn_member`` *before* the db row is written, so:

  - the db row becomes an "enlist-time evolved snapshot" (evolved value for a
    reused member, baseline for a first-time member), and
  - the cache is primed with the evolved value before build_team returns, so
    the first roster's overlay hits the cache instead of the db.

These tests mirror the REAL leader timeline (``setup_agent`` attaches the
cache first, then build_team → spawn_member runs with the cache attached) —
unlike the race-repro tests which attach the cache only after spawn_member.
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

_TEAM = "evo-snapshot-team"
_MEMBER = "predefined-worker"
_DB_DESC = "spec baseline desc"
_DB_PROMPT = "spec baseline prompt"
_EVO_DESC_MARKER = "EVO-DESC-7C1"
_EVO_PROMPT_MARKER = "EVO-PROMPT-7C1"


def _card_path(team: str) -> Path:
    return (
        team_member_workspace_dir(team, _MEMBER)
        / "prompts"
        / "identity"
        / "card.md"
    )


def _prompt_path(team: str) -> Path:
    return (
        team_member_workspace_dir(team, _MEMBER)
        / "prompts"
        / "identity"
        / "member_prompt.md"
    )


def _write_evolved_md(team: str, *, field: str, baseline: str, marker: str) -> None:
    """Write an already-evolved md (hand-written: baseline_sha256=None → always
    evolved) at the team-internal member path. Mirrors a reused member whose
    standalone workspace evolved and is visible via the in-team link."""
    path = _card_path(team) if field == "desc" else _prompt_path(team)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = f"{baseline}\n\n<!-- evolved --> {marker}\n"
    meta = {
        "kind": "card" if field == "desc" else "prompt",
        "name": "member_card" if field == "desc" else "member_prompt",
        "baseline_sha256": None,  # hand-written → is_evolved() always True
        "evolved": True,
        "updated_at": 1_000,
    }
    path.write_text(write_frontmatter(meta, body), encoding="utf-8")


class _StubManager:
    """Minimal stand-in so TeamBackend.workspace_cache resolves to a cache."""

    def __init__(self, cache):
        self.workspace_cache = cache

    def attach_workspace_cache(self, cache):
        self.workspace_cache = cache


@pytest_asyncio.fixture
async def env(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    configure_openjiuwen_home(str(home))
    token = set_session_id("evo_snapshot_session")
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


def _make_backend(db, messager, *, cache: WorkspaceCache | None = None) -> TeamBackend:
    """Build a TeamBackend with the cache attached up-front — mirrors the real
    leader timeline where ``setup_agent`` → ``_attach_workspace_cache`` attaches
    the resident cache BEFORE build_team runs."""
    bk = TeamBackend(
        team_name=_TEAM,
        member_name="leader",
        is_leader=True,
        db=db,
        messager=messager,
        evolution_enabled=True,
    )
    if cache is not None:
        bk.attach_workspace_manager(_StubManager(cache))
    return bk


async def _build_team_and_spawn_predefined(bk: TeamBackend) -> None:
    """build_team + spawn_member with the spec-time desc/prompt. The fix under
    test runs inside spawn_member: the cache (already attached) is primed with
    the evolved md value and the db row carries that value."""
    from openjiuwen.agent_teams.schema.status import MemberStatus
    from openjiuwen.agent_teams.schema.team import TeamRole
    from openjiuwen.core.single_agent import AgentCard

    await bk.build_team(
        display_name=_TEAM,
        desc="team goal",
        leader_display_name="L",
        leader_desc="leader persona",
    )
    await bk.spawn_member(
        member_name=_MEMBER,
        display_name="Predefined Worker",
        agent_card=AgentCard(
            id=f"{_TEAM}_{_MEMBER}", name="Predefined Worker", description=_DB_DESC
        ),
        desc=_DB_DESC,
        prompt=_DB_PROMPT,
        status=MemberStatus.UNSTARTED,
        role=TeamRole.TEAMMATE,
    )


# ── Fix: db row carries the evolved snapshot, not the spec baseline ────────


@pytest.mark.asyncio
@pytest.mark.level0
async def test_spawn_member_writes_evolved_value_to_db(env):
    """Reused member with an evolved card.md + member_prompt.md: spawn_member's
    db row must carry the EVOLVED bodies, not the spec baseline."""
    db = env["db"]
    messager = env["messager"]

    # Pre-seed the team-internal member path with evolved md (mirrors a reused
    # standalone workspace surfaced via the in-team link).
    _write_evolved_md(_TEAM, field="desc", baseline=_DB_DESC, marker=_EVO_DESC_MARKER)
    _write_evolved_md(_TEAM, field="prompt", baseline=_DB_PROMPT, marker=_EVO_PROMPT_MARKER)

    cache = WorkspaceCache(WorkspaceStore(), _TEAM, language="cn")
    bk = _make_backend(db, messager, cache=cache)
    await _build_team_and_spawn_predefined(bk)

    member = await db.member.get_member(_MEMBER, _TEAM)
    assert member is not None
    # THE fix: db row is the enlist-time evolved snapshot.
    assert _EVO_DESC_MARKER in (member.desc or ""), (
        f"db desc was not the evolved snapshot; desc={member.desc!r}"
    )
    assert _EVO_PROMPT_MARKER in (member.prompt or ""), (
        f"db prompt was not the evolved snapshot; prompt={member.prompt!r}"
    )
    # The spec baseline bodies are still part of the evolved body (they were the
    # seed the evolution party edited), but the marker proves the evolved body
    # won — the spec value alone is NOT what was persisted.
    assert _DB_DESC in (member.desc or "")
    assert _DB_PROMPT in (member.prompt or "")


@pytest.mark.asyncio
@pytest.mark.level0
async def test_spawn_member_first_member_db_baseline(env):
    """First-time member (no evolved md on disk): spawn_member's db row carries
    the spec baseline (md written by the fix is the baseline it just seeded)."""
    db = env["db"]
    messager = env["messager"]

    # No pre-seeded md — first-time member.
    cache = WorkspaceCache(WorkspaceStore(), _TEAM, language="cn")
    bk = _make_backend(db, messager, cache=cache)
    await _build_team_and_spawn_predefined(bk)

    member = await db.member.get_member(_MEMBER, _TEAM)
    assert member is not None
    # No evolution → db carries the spec baseline (the freshly-seeded md body).
    assert member.desc == _DB_DESC, (
        f"first-time member db desc should be the spec baseline; got {member.desc!r}"
    )
    assert member.prompt == _DB_PROMPT, (
        f"first-time member db prompt should be the spec baseline; got {member.prompt!r}"
    )
    # And the md files were seeded (baseline) by the fix.
    assert _card_path(_TEAM).exists(), "fix should have seeded card.md"
    assert _prompt_path(_TEAM).exists(), "fix should have seeded member_prompt.md"


# ── Fix: first roster reads the evolved value (cache primed at spawn_member) ─


@pytest.mark.asyncio
@pytest.mark.level0
async def test_first_roster_reads_evolved_after_spawn_member_fix(env):
    """THE race closed: the leader's first roster — rendered right after
    build_team with NO member spawn yet — serves the evolved value because
    spawn_member already primed the cache."""
    db = env["db"]
    messager = env["messager"]

    _write_evolved_md(_TEAM, field="desc", baseline=_DB_DESC, marker=_EVO_DESC_MARKER)
    _write_evolved_md(_TEAM, field="prompt", baseline=_DB_PROMPT, marker=_EVO_PROMPT_MARKER)

    cache = WorkspaceCache(WorkspaceStore(), _TEAM, language="cn")
    bk = _make_backend(db, messager, cache=cache)
    await _build_team_and_spawn_predefined(bk)

    # No member spawn here — mirrors the real gap between build_team and the
    # first roster. The cache was primed inside spawn_member, so the overlay
    # must hit the evolved value without any re-read of a missing file.
    members = await bk.list_members()
    worker = next((m for m in members if m.member_name == _MEMBER), None)
    assert worker is not None
    assert _EVO_DESC_MARKER in (worker.desc or ""), (
        f"first roster leaked the stale db/spec baseline; desc={worker.desc!r}"
    )
    assert _EVO_PROMPT_MARKER in (worker.prompt or ""), (
        f"first roster leaked the stale db/spec baseline; prompt={worker.prompt!r}"
    )


# ── Evolution off: db carries the spec baseline, no file touched ─────────────


@pytest.mark.asyncio
@pytest.mark.level0
async def test_spawn_member_evolution_off_keeps_spec_baseline(env):
    """With the evolution switch off, spawn_member writes the spec baseline to
    the db and touches no md file (the fix's guard skips the assembler call)."""
    db = env["db"]
    messager = env["messager"]

    bk = TeamBackend(
        team_name=_TEAM,
        member_name="leader",
        is_leader=True,
        db=db,
        messager=messager,
        evolution_enabled=False,  # off
    )
    # Even if a cache is attached, the spec-evolution guard must skip the
    # assembler call — db stays the spec baseline, no md file seeded.
    bk.attach_workspace_manager(_StubManager(WorkspaceCache(WorkspaceStore(), _TEAM, language="cn")))
    await _build_team_and_spawn_predefined(bk)

    member = await db.member.get_member(_MEMBER, _TEAM)
    assert member is not None
    assert member.desc == _DB_DESC
    assert member.prompt == _DB_PROMPT
    assert not _card_path(_TEAM).exists(), "evolution off must not seed card.md"
    assert not _prompt_path(_TEAM).exists(), "evolution off must not seed member_prompt.md"

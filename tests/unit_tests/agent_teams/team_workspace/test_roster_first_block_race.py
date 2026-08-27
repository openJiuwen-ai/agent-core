# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Reproduce the leader-first-roster-stale race.

User observation: the leader's FIRST roster block in a freshly built team shows
the DB baseline desc for a predefined member (e.g. ``预配置成员描述``), even
though the member's ``card.md`` on disk is evolved (``8月24改过 / 【演进过的】
预配置成员描述``). A LATER ``roster-change`` block does show the evolved value.

Root cause (this test pins it): ``build_team`` calls ``spawn_member`` which
writes the DB row (T1, spec baseline desc) but does NOT assemble the member
workspace — that happens later when the member ``spawn`` runs
(``_assemble_member_workspace`` → ``ensure_team_member_workspace_link`` +
``write_member_identity`` prime cache). So between ``build_team`` completion
and the member's spawn, the leader's first roster probe:

  _roster_block → list_members → _overlay_member(worker-a) →
  cache.get_member_field("worker-a", "desc") → read team-member card.md path
  → FILE MISSING (symlink not built yet, no file at team-internal path)
  → returns None → falls back to DB baseline (stale spec value)

The ``write skipped (evolution wins)`` log only appears once the member
spawns (14:51:03 in the user's run) — by then the cache is primed with the
evolved value, and the next roster probe (mtime advanced) re-delivers it.
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

_TEAM = "race-team"
_MEMBER = "worker-a"
_DB_DESC = "预配置成员描述"          # spec baseline → DB row
_EVO_DESC = "【演进过的】预配置成员描述"  # evolved card.md body


def _card_path() -> Path:
    return (
        team_member_workspace_dir(_TEAM, _MEMBER)
        / "prompts"
        / "identity"
        / "card.md"
    )


@pytest_asyncio.fixture
async def env(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    configure_openjiuwen_home(str(home))
    token = set_session_id("race_probe_session")
    db = TeamDatabase(
        DatabaseConfig(db_type=DatabaseType.SQLITE, connection_string=":memory:")
    )
    await db.initialize()
    messager = AsyncMock(spec=Messager)
    try:
        yield {"db": db, "messager": messager}
    finally:
        await db.close()
        reset_session_id(token)
        reset_openjiuwen_home()


class _StubManager:
    def __init__(self, cache):
        self.workspace_cache = cache


async def _build_team_with_predefined(db, messager) -> TeamBackend:
    """build_team + spawn_member with the spec-time desc (DB baseline).

    Mirrors blueprint._register_predefined_members (team.py:1714-1741): the DB
    row is written at build_team time; the member workspace (card.md) is NOT
    written here — that happens at member spawn (setup_agent).
    """
    from openjiuwen.agent_teams.schema.status import MemberStatus
    from openjiuwen.agent_teams.schema.team import TeamRole
    from openjiuwen.core.single_agent import AgentCard

    bk = TeamBackend(
        team_name=_TEAM,
        member_name="leader",
        is_leader=True,
        db=db,
        messager=messager,
        evolution_enabled=True,
    )
    await bk.build_team(
        display_name=_TEAM,
        desc="goal",
        leader_display_name="L",
        leader_desc="leader persona",
    )
    await bk.spawn_member(
        member_name=_MEMBER,
        display_name="预配置成员",
        agent_card=AgentCard(id=f"{_TEAM}_{_MEMBER}", name="预配置成员", description=_DB_DESC),
        desc=_DB_DESC,
        prompt=None,
        status=MemberStatus.UNSTARTED,
        role=TeamRole.TEAMMATE,
    )
    return bk


@pytest.mark.asyncio
@pytest.mark.level0
async def test_first_roster_before_member_spawn_falls_back_to_db(env):
    """THE race: leader's first roster happens BEFORE the member spawns.

    build_team wrote the DB row (spec baseline desc). The member's evolved
    card.md lives at its team-internal path — but the symlink/file is only
    created at spawn time. So when the leader probes the roster right after
    build_team (no spawn yet), the overlay reads the card.md path, finds NO
    file, returns None, and the DB baseline desc leaks into the roster block.
    """
    db = env["db"]
    messager = env["messager"]

    bk = await _build_team_with_predefined(db, messager)
    # The premise: after build_team, the DB row exists but the member
    # workspace card.md does NOT exist yet (spawn has not run).
    assert not _card_path().exists(), (
        "precondition: card.md must not exist yet (member has not spawned)"
    )
    member_row = await db.member.get_member(_MEMBER, _TEAM)
    assert member_row is not None
    assert member_row.desc == _DB_DESC  # DB carries the spec baseline

    # Attach the leader's fresh cache (mirrors new-process leader after
    # build_team, before any member spawned).
    cache = WorkspaceCache(WorkspaceStore(), _TEAM, language="cn")
    bk.attach_workspace_manager(_StubManager(cache))

    # THE first-roster probe: list_members overlays worker-a.
    members = await bk.list_members()
    worker = next((m for m in members if m.member_name == _MEMBER), None)
    assert worker is not None
    # BUG: the overlay returned None (file missing) → DB baseline leaks in.
    assert worker.desc == _DB_DESC, (
        "first roster shows the stale DB baseline because card.md did not "
        "exist at the team-internal path before the member spawned"
    )
    assert _EVO_DESC not in (worker.desc or "")


@pytest.mark.asyncio
@pytest.mark.level0
async def test_roster_after_member_spawn_shows_evolved_value(env):
    """Control / fix-direction: once the member has spawned and the evolved
    card.md is on disk at the team-internal path, the overlay reads the
    evolved body and the roster shows the new value."""
    db = env["db"]
    messager = env["messager"]

    bk = await _build_team_with_predefined(db, messager)
    # Simulate the member spawn: write the evolved card.md at the team path
    # (mirrors _assemble_member_workspace → write_card with _evolved_content
    # protection keeping the existing evolved file).
    path = _card_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = {
        "kind": "card",
        "name": "member_card",
        "baseline_sha256": None,   # hand-written → is_evolved() always True
        "evolved": True,
        "updated_at": 1787552532013,
    }
    path.write_text(write_frontmatter(meta, _EVO_DESC), encoding="utf-8")

    cache = WorkspaceCache(WorkspaceStore(), _TEAM, language="cn")
    bk.attach_workspace_manager(_StubManager(cache))
    cache.invalidate()  # ensure a fresh read of the now-existing file

    members = await bk.list_members()
    worker = next((m for m in members if m.member_name == _MEMBER), None)
    assert worker is not None
    assert _EVO_DESC in (worker.desc or ""), (
        "after spawn (card.md on disk + evolved), the overlay serves the new body"
    )

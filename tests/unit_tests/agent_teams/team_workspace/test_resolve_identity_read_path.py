# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Verify the minimal fix's hard precondition: at build_team time (before any
member spawn / assembly), calling ``ensure_team_member_workspace_link`` then
``WorkspaceStore.read_card`` / ``read_member_prompt`` over the in-team link
path resolves to the REUSED host workspace's evolved md body.

The minimal plan is: build_team, before spawn_member, reads the evolved md
via the link and writes that value into the db row (instead of the spec
baseline). This test pins whether that read path actually reaches the
evolved body when the member is a reused predefined member whose host
workspace already carries an evolved card.md / member_prompt.md.

It also confirms a first-time member (no host workspace) reads None and so
falls back to the spec value — the no-evolution case stays correct.
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
    independent_member_workspace,
    reset_openjiuwen_home,
    team_member_workspace_dir,
)
from openjiuwen.agent_teams.team_workspace.frontmatter import (
    body_sha256,
    write_frontmatter,
)
from openjiuwen.agent_teams.team_workspace.workspace_store import WorkspaceStore
from openjiuwen.agent_teams.team_workspace.workspace_cache import WorkspaceCache
from openjiuwen.agent_teams.tools.database import DatabaseConfig, DatabaseType, TeamDatabase
from openjiuwen.agent_teams.tools.team import TeamBackend
from openjiuwen.agent_teams.workspace_layout import ensure_team_member_workspace_link

_MEMBER = "reuse-probe-worker"
_TEAM = "reuse-probe-team"
_EVO_DESC_MARKER = "EVO-DESC-7C1"
_EVO_PROMPT_MARKER = "EVO-PROMPT-7C1"


def _write_evolved_host_card(member_name: str, body: str) -> Path:
    """Write an evolved card.md into the member's HOST workspace (the physical
    dir the in-team link resolves to for a reused predefined member)."""
    host = independent_member_workspace(member_name)
    card = host / "prompts" / "identity" / "card.md"
    card.parent.mkdir(parents=True, exist_ok=True)
    meta = {
        "kind": "card",
        "name": "member_card",
        "baseline_sha256": "deadbeef",  # diverges → is_evolved True
        "evolved": True,
        "updated_at": 1_000,
    }
    card.write_text(write_frontmatter(meta, body), encoding="utf-8")
    return card


def _write_evolved_host_prompt(member_name: str, body: str) -> Path:
    host = independent_member_workspace(member_name)
    prompt = host / "prompts" / "identity" / "member_prompt.md"
    prompt.parent.mkdir(parents=True, exist_ok=True)
    meta = {
        "kind": "prompt",
        "name": "member_prompt",
        "baseline_sha256": "deadbeef",
        "evolved": True,
        "updated_at": 1_000,
    }
    prompt.write_text(write_frontmatter(meta, body), encoding="utf-8")
    return prompt


@pytest_asyncio.fixture
async def env(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    configure_openjiuwen_home(str(home))
    token = set_session_id("resolve_probe_session")
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


@pytest.mark.asyncio
@pytest.mark.level0
async def test_read_via_link_reaches_reused_evolved_desc(env):
    """build_team-time read: ensure_link + read_card resolves to the host's
    evolved desc, not None."""
    _write_evolved_host_card(_MEMBER, f"host evolved\n{_EVO_DESC_MARKER}")

    # build_team-time step: ensure the in-team link, THEN read (no spawn yet).
    ensure_team_member_workspace_link(_TEAM, _MEMBER)
    desc = WorkspaceStore().read_card(_TEAM, _MEMBER)

    assert desc is not None, "read_card returned None — link did not resolve to host md"
    assert _EVO_DESC_MARKER in desc, f"evolved desc not reached: {desc!r}"


@pytest.mark.asyncio
@pytest.mark.level0
async def test_read_via_link_reaches_reused_evolved_prompt(env):
    """Same for member_prompt.md."""
    _write_evolved_host_prompt(_MEMBER, f"host evolved prompt\n{_EVO_PROMPT_MARKER}")

    ensure_team_member_workspace_link(_TEAM, _MEMBER)
    prompt = WorkspaceStore().read_member_prompt(_TEAM, _MEMBER)

    assert prompt is not None
    assert _EVO_PROMPT_MARKER in prompt, f"evolved prompt not reached: {prompt!r}"


@pytest.mark.asyncio
@pytest.mark.level0
async def test_read_returns_none_for_first_time_member(env):
    """A first-time member (no host workspace) reads None — the caller must
    fall back to the spec value. This keeps the no-evolution case correct."""
    # No host workspace written.
    assert not independent_member_workspace("brand-new-member").exists()

    ensure_team_member_workspace_link(_TEAM, "brand-new-member")
    desc = WorkspaceStore().read_card(_TEAM, "brand-new-member")
    prompt = WorkspaceStore().read_member_prompt(_TEAM, "brand-new-member")

    assert desc is None
    assert prompt is None


@pytest.mark.asyncio
@pytest.mark.level0
async def test_link_type_after_ensure(env):
    """Surface what the in-team link actually is (symlink vs plain dir) so the
    read path's behaviour is documented, not assumed."""
    _write_evolved_host_card(_MEMBER, f"host evolved\n{_EVO_DESC_MARKER}")
    link = Path(ensure_team_member_workspace_link(_TEAM, _MEMBER))
    desc = WorkspaceStore().read_card(_TEAM, _MEMBER)
    print(f"\n[probe] link={link} is_symlink={link.is_symlink()} "
          f"link_target={link.resolve() if link.exists() else '<missing>'} "
          f"read_card={desc!r}")
    # The assertion that matters: the read reached the evolved body.
    assert desc is not None and _EVO_DESC_MARKER in desc

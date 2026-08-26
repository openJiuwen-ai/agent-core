# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for ``WorkspaceStore`` B-class value files (design-v5 block A).

Covers the write-side evolution protection ("an evolved file is never
overwritten") and the read-side overlay judgment ("evolved body wins over
the raw DB column").
"""

import pytest

from openjiuwen.agent_teams.paths import (
    configure_openjiuwen_home,
    reset_openjiuwen_home,
    team_member_workspace_dir,
    team_workspace_dir,
)
from openjiuwen.agent_teams.team_workspace.frontmatter import (
    body_sha256,
    read_frontmatter,
    write_frontmatter,
)
from openjiuwen.agent_teams.team_workspace.workspace_store import WorkspaceStore


@pytest.fixture
def store(tmp_path):
    """Isolated home — real ``agent_teams.paths`` functions resolve under tmp."""
    home = tmp_path / "home"
    home.mkdir()
    configure_openjiuwen_home(str(home))
    try:
        yield WorkspaceStore()
    finally:
        reset_openjiuwen_home()


class TestMemberWrite:
    """card.md / member_prompt.md write with baseline stamping."""

    def test_write_card_lands_with_baseline(self, store, tmp_path):
        store.write_card("T", "leader", "the desc")
        target = team_member_workspace_dir("T", "leader") / "prompts" / "identity" / "card.md"
        meta, body = read_frontmatter(target.read_text(encoding="utf-8"))
        assert body == "the desc"
        assert meta["baseline_sha256"] == body_sha256("the desc")
        assert meta["evolved"] is False

    def test_write_prompt_lands_in_member_dir(self, store, tmp_path):
        store.write_member_prompt("T", "w1", "be careful")
        target = team_member_workspace_dir("T", "w1") / "prompts" / "identity" / "member_prompt.md"
        assert target.exists()
        meta, body = read_frontmatter(target.read_text(encoding="utf-8"))
        assert body == "be careful"
        assert meta["kind"] == "prompt"

    def test_empty_value_skips_write(self, store, tmp_path):
        store.write_card("T", "leader", None)
        store.write_member_prompt("T", "leader", "")
        assert not (team_member_workspace_dir("T", "leader") / "prompts").exists()


class TestEvolutionProtection:
    """Write side: an evolved file is never clobbered."""

    def test_evolved_file_not_overwritten(self, store, tmp_path):
        store.write_card("T", "leader", "baseline")
        target = team_member_workspace_dir("T", "leader") / "prompts" / "identity" / "card.md"
        # Evolution party edits the body (hash diverges from baseline).
        meta, body = read_frontmatter(target.read_text(encoding="utf-8"))
        target.write_text(write_frontmatter(meta, "evolved body"), encoding="utf-8")
        # New user input must not clobber the evolved value.
        store.write_card("T", "leader", "new input")
        _, body = read_frontmatter(target.read_text(encoding="utf-8"))
        assert body == "evolved body"

    def test_unevolved_file_refreshed(self, store, tmp_path):
        store.write_card("T", "leader", "old")
        store.write_card("T", "leader", "new")
        target = team_member_workspace_dir("T", "leader") / "prompts" / "identity" / "card.md"
        _, body = read_frontmatter(target.read_text(encoding="utf-8"))
        assert body == "new"
        meta, _ = read_frontmatter(target.read_text(encoding="utf-8"))
        assert meta["baseline_sha256"] == body_sha256("new")

    def test_handwritten_file_not_overwritten(self, store, tmp_path):
        target = team_member_workspace_dir("T", "leader") / "prompts" / "identity" / "card.md"
        target.parent.mkdir(parents=True)
        target.write_text("hand written, no frontmatter", encoding="utf-8")
        store.write_card("T", "leader", "new input")
        assert target.read_text(encoding="utf-8") == "hand written, no frontmatter"

    def test_malformed_frontmatter_is_invalid_and_rewritable(self, store, tmp_path):
        target = team_member_workspace_dir("T", "leader") / "prompts" / "identity" / "card.md"
        target.parent.mkdir(parents=True)
        target.write_text("---\nkind: [unclosed\n---\nbody", encoding="utf-8")
        # Read side: invalid file → None (the DB column stands).
        assert store.read_card("T", "leader") is None
        # Write side: invalid file → freely rebuilt with the new value.
        store.write_card("T", "leader", "fresh baseline")
        meta, body = read_frontmatter(target.read_text(encoding="utf-8"))
        assert body == "fresh baseline"
        assert meta["baseline_sha256"] == body_sha256("fresh baseline")


class TestMemberRead:
    """Read side: evolved body wins, un-evolved/missing falls back to None."""

    def test_read_card_evolved(self, store, tmp_path):
        store.write_card("T", "leader", "baseline")
        target = team_member_workspace_dir("T", "leader") / "prompts" / "identity" / "card.md"
        meta, _ = read_frontmatter(target.read_text(encoding="utf-8"))
        target.write_text(write_frontmatter(meta, "evolved"), encoding="utf-8")
        assert store.read_card("T", "leader") == "evolved"

    def test_read_card_unevolved_returns_none(self, store, tmp_path):
        store.write_card("T", "leader", "baseline")
        assert store.read_card("T", "leader") is None

    def test_read_card_missing_returns_none(self, store):
        assert store.read_card("T", "ghost") is None

    def test_read_member_prompt_handwritten_wins(self, store):
        member_dir = team_member_workspace_dir("T", "m1")
        target = member_dir / "prompts" / "identity" / "member_prompt.md"
        target.parent.mkdir(parents=True)
        target.write_text("no frontmatter", encoding="utf-8")
        assert store.read_member_prompt("T", "m1") == "no frontmatter"


class TestLinkEntryBypassesProbe:
    """211: B-class IO resolves through ``workspaces/<member>_workspace`` (the
    unified link entry, ``team_member_workspace_dir``) — no real-dir probe."""

    def test_read_write_roundtrip_through_link_entry(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()

        configure_openjiuwen_home(str(home))
        try:
            store = WorkspaceStore()
            store.write_card("T", "w1", "the desc")
            store.write_member_prompt("T", "w1", "the prompt")
            # Un-evolved baseline reads as None (caller falls back to DB).
            assert store.read_card("T", "w1") is None
            # Evolved body round-trips through the link-shaped path.
            target = (
                team_member_workspace_dir("T", "w1") / "prompts" / "identity" / "card.md"
            )
            meta, _ = read_frontmatter(target.read_text(encoding="utf-8"))
            target.write_text(write_frontmatter(meta, "evolved"), encoding="utf-8")
            assert store.read_card("T", "w1") == "evolved"
        finally:
            reset_openjiuwen_home()


class TestTeamWriteRead:
    """B-class team files land in team-workspace/prompts/identity/."""

    def test_team_card_written_with_baseline(self, store, tmp_path):
        store.write_team_card("T", "team desc")
        target = team_workspace_dir("T") / "prompts" / "identity" / "team_card.md"
        assert target.exists()
        meta, body = read_frontmatter(target.read_text(encoding="utf-8"))
        assert body == "team desc"
        assert meta["baseline_sha256"] == body_sha256("team desc")
        # Un-evolved file reads as None — the caller falls back to the DB row.
        assert store.read_team_card("T") is None

    def test_team_evolved_not_overwritten(self, store, tmp_path):
        store.write_team_card("T", "baseline")
        target = team_workspace_dir("T") / "prompts" / "identity" / "team_card.md"
        meta, _ = read_frontmatter(target.read_text(encoding="utf-8"))
        target.write_text(write_frontmatter(meta, "evolved"), encoding="utf-8")
        store.write_team_card("T", "new input")
        assert store.read_team_card("T") == "evolved"

    def test_team_prompt_only_written_when_value(self, store, tmp_path):
        store.write_team_prompt("T", None)
        store.write_team_prompt("T", "the prompt")
        target = team_workspace_dir("T") / "prompts" / "identity" / "team_prompt.md"
        assert target.exists()
        meta, body = read_frontmatter(target.read_text(encoding="utf-8"))
        assert body == "the prompt"
        assert meta["baseline_sha256"] == body_sha256("the prompt")

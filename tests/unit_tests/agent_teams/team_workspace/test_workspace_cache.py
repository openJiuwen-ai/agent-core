# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for ``WorkspaceCache`` lazy get + invalidate (design-v5, 212).

Covers: a miss reads the file once and fills the dict; a hit is a zero-IO
dict lookup; ``invalidate`` drops the dict so the next get re-reads; the
write-side ``fill_*`` priming; the frontmatter read serves the latest body.
"""

import json
import pathlib

import pytest

from openjiuwen.agent_teams.paths import (
    configure_openjiuwen_home,
    reset_openjiuwen_home,
    team_member_workspace_dir,
    team_workspace_dir,
)
from openjiuwen.agent_teams.team_workspace.frontmatter import write_frontmatter
from openjiuwen.agent_teams.team_workspace.workspace_cache import WorkspaceCache
from openjiuwen.agent_teams.team_workspace.workspace_store import WorkspaceStore


@pytest.fixture
def ws(tmp_path):
    """Isolated home + a lazy WorkspaceCache bound to team ``T``."""
    home = tmp_path / "home"
    home.mkdir()
    configure_openjiuwen_home(str(home))
    try:
        yield WorkspaceCache(
            WorkspaceStore(),
            "T",
            language="cn",
        )
    finally:
        reset_openjiuwen_home()


def _team_root() -> pathlib.Path:
    return team_workspace_dir("T")


def _evolved_file(path: pathlib.Path, body: str, **meta_extra) -> None:
    """Write a workspace file whose body diverges from its recorded baseline."""
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = {"baseline_sha256": "deadbeef", **meta_extra}  # diverges from any real body
    path.write_text(write_frontmatter(meta, body), encoding="utf-8")


def _baseline_file(path: pathlib.Path, body: str, **meta_extra) -> None:
    """Write a workspace file whose body matches its baseline (un-evolved)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    from openjiuwen.agent_teams.team_workspace.frontmatter import body_sha256

    meta = {"baseline_sha256": body_sha256(body), **meta_extra}
    path.write_text(write_frontmatter(meta, body), encoding="utf-8")


class TestLazyGetReadsOnceThenHits:
    """A miss reads the file and fills the dict; the next call is a hit."""

    def test_a_class_template(self, ws):
        _evolved_file(_team_root() / "prompts" / "system" / "leader_bootstrap.cn.md", "evolved prompt")
        assert ws.get_template("leader_bootstrap").content == "evolved prompt"
        # Second call hits the dict — mutate the file to prove no re-read.
        _evolved_file(_team_root() / "prompts" / "system" / "leader_bootstrap.cn.md", "changed")
        assert ws.get_template("leader_bootstrap").content == "evolved prompt"

    def test_b_class_member_and_team(self, ws):
        _evolved_file(
            team_member_workspace_dir("T", "leader") / "prompts" / "identity" / "card.md",
            "evolved desc",
        )
        _evolved_file(
            _team_root() / "prompts" / "identity" / "team_card.md",
            "evolved team desc",
        )
        assert ws.get_member_field("leader", "desc") == "evolved desc"
        assert ws.get_member_field("leader", "prompt") is None
        assert ws.get_team_field("desc") == "evolved team desc"
        assert ws.get_team_field("prompt") is None

    def test_c_class_tool_md_and_params(self, ws):
        _evolved_file(
            _team_root() / "prompts" / "tool" / "task" / "submit_plan.cn.md",
            "evolved tool desc",
        )
        _evolved_file(
            _team_root() / "prompts" / "tool" / "tool.param.cn.md",
            json.dumps({"create_task.task.title": "evolved title"}, ensure_ascii=False),
        )
        assert ws.get_tool_md("submit_plan") == "evolved tool desc"
        assert ws.get_tool_param("create_task", "task.title") == "evolved title"

    def test_handwritten_file_without_frontmatter_wins(self, ws):
        path = _team_root() / "prompts" / "system" / "x.cn.md"
        path.parent.mkdir(parents=True)
        path.write_text("hand written", encoding="utf-8")
        assert ws.get_template("x").content == "hand written"

    def test_malformed_frontmatter_falls_back_to_default(self, ws):
        path = _team_root() / "prompts" / "system" / "x.cn.md"
        path.parent.mkdir(parents=True)
        path.write_text("---\nkind: [unclosed\n---\nbody", encoding="utf-8")
        assert ws.get_template("x") is None

    def test_missing_file_returns_none(self, ws):
        assert ws.get_template("never_written") is None


class TestUnevolvedFallsBack:
    """Un-evolved files: A-class serves the (framework-equal) body, B-class
    still returns None (the store only serves evolved values; the DB fallback
    equals the file body)."""

    def test_a_class_unevolved_returns_body(self, ws):
        _baseline_file(_team_root() / "prompts" / "system" / "x.cn.md", "baseline body")
        assert ws.get_template("x").content == "baseline body"

    def test_b_class_unevolved_returns_none(self, ws):
        _baseline_file(
            team_member_workspace_dir("T", "leader") / "prompts" / "identity" / "card.md",
            "baseline desc",
        )
        assert ws.get_member_field("leader", "desc") is None


class TestInvalidate:
    """invalidate drops resident values so the next get re-reads the file."""

    def test_invalidate_then_reread_picks_up_new_value(self, ws):
        path = _team_root() / "prompts" / "system" / "x.cn.md"
        _evolved_file(path, "v1")
        assert ws.get_template("x").content == "v1"
        _evolved_file(path, "v2")
        # Without invalidate the cached v1 is still served (read-once).
        assert ws.get_template("x").content == "v1"
        ws.invalidate()
        assert ws.get_template("x").content == "v2"

    def test_invalidate_drops_all_classes(self, ws):
        _evolved_file(_team_root() / "prompts" / "system" / "a.cn.md", "a")
        _evolved_file(
            team_member_workspace_dir("T", "leader") / "prompts" / "identity" / "card.md",
            "desc",
        )
        assert ws.get_template("a").content == "a"
        assert ws.get_member_field("leader", "desc") == "desc"
        ws.invalidate()
        # Files still on disk → re-read after invalidate.
        assert ws.get_template("a").content == "a"
        assert ws.get_member_field("leader", "desc") == "desc"


class TestDynamicMemberLazyRead:
    """212: a dynamic member spawned in a prior session is read on first get.

    The lazy model needs no roster: the member's card is read the first time
    anyone asks for it, so a dynamic member the declared spec never knew is
    covered the moment its name is queried.
    """

    def test_dynamic_member_read_on_first_get(self, ws):
        _evolved_file(
            team_member_workspace_dir("T", "counter-1") / "prompts" / "identity" / "card.md",
            "evolved dynamic desc",
        )
        _evolved_file(
            team_member_workspace_dir("T", "counter-1") / "prompts" / "identity" / "member_prompt.md",
            "evolved dynamic prompt",
        )
        assert ws.get_member_field("counter-1", "desc") == "evolved dynamic desc"
        assert ws.get_member_field("counter-1", "prompt") == "evolved dynamic prompt"
        # A member with no file → None (DB fallback), cached after first read.
        assert ws.get_member_field("team-leader", "desc") is None

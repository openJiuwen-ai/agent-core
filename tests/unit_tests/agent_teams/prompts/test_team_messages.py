# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for the team-state message bodies and the roster diff.

These bodies are what a member is told about the team over time: its own
identity, the team metadata, and the roster (once in full, then as deltas).
"""

import pytest

from openjiuwen.agent_teams.inbound_render import render_team_context
from openjiuwen.agent_teams.prompts.messages import (
    build_identity_text,
    build_roster_delta_text,
    build_roster_snapshot_text,
    build_team_info_text,
    diff_roster,
    format_member_line,
)


def _member(name: str, display: str = "", desc: str = "", role: str = "teammate") -> dict[str, str]:
    return {
        "member_name": name,
        "display_name": display or name.upper(),
        "desc": desc,
        "role": role,
    }


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


@pytest.mark.level0
def test_identity_carries_both_names_and_private_agreement():
    body = build_identity_text(
        member_name="dev1",
        display_name="成员一",
        member_prompt="always write tests",
        language="cn",
    )
    assert body is not None
    assert "# 成员身份" in body
    assert "你的 member_name: dev1" in body
    assert "你的 display_name: 成员一" in body
    assert "## 私有工作约定" in body
    assert "always write tests" in body


@pytest.mark.level0
def test_identity_carries_the_member_workspace():
    """Per-member and constant, exactly like the names — same body, not a channel."""
    body = build_identity_text(member_name="dev1", member_workspace_path="/ws/dev1", language="cn")
    assert body is not None
    assert "你的私有工作区: `/ws/dev1`" in body
    # It says what the directory is for, so the model does not misuse it.
    assert "团队共享工作空间" in body
    assert "skill" in body


@pytest.mark.level0
def test_identity_workspace_english():
    body = build_identity_text(member_name="dev1", member_workspace_path="/ws/dev1", language="en")
    assert body is not None
    assert "Your private workspace: `/ws/dev1`" in body


@pytest.mark.level0
def test_identity_without_workspace_drops_that_line():
    body = build_identity_text(member_name="dev1", language="cn")
    assert body is not None
    assert "私有工作区" not in body


@pytest.mark.level0
def test_identity_with_only_a_workspace_still_renders():
    body = build_identity_text(member_name=None, member_workspace_path="/ws/dev1", language="cn")
    assert body is not None
    assert "你的私有工作区: `/ws/dev1`" in body


@pytest.mark.level0
def test_identity_without_display_name_drops_that_line():
    body = build_identity_text(member_name="dev1", language="cn")
    assert body is not None
    assert "你的 member_name: dev1" in body
    assert "display_name" not in body


@pytest.mark.level0
def test_identity_without_private_agreement_drops_the_subsection():
    body = build_identity_text(member_name="dev1", member_prompt="   ", language="cn")
    assert body is not None
    assert "## 私有工作约定" not in body


@pytest.mark.level0
def test_identity_empty_returns_none():
    assert build_identity_text(member_name=None, member_prompt="", language="cn") is None


@pytest.mark.level0
def test_identity_english():
    body = build_identity_text(
        member_name="dev1",
        display_name="Dev One",
        member_prompt="ship small PRs",
        language="en",
    )
    assert body is not None
    assert "# Member Identity" in body
    assert "Your member_name: dev1" in body
    assert "Your display_name: Dev One" in body
    assert "## Private Working Agreement" in body


# ---------------------------------------------------------------------------
# Team info
# ---------------------------------------------------------------------------


@pytest.mark.level0
def test_team_info_full():
    body = build_team_info_text(
        team_info={"team_name": "demo", "display_name": "Demo", "desc": "Ship it"},
        language="en",
    )
    assert body is not None
    assert "# Team Info" in body
    assert "demo" in body
    assert "Ship it" in body


@pytest.mark.level0
def test_team_info_empty_returns_none():
    assert build_team_info_text(team_info=None, language="cn") is None
    assert build_team_info_text(team_info={}, language="cn") is None


@pytest.mark.level0
def test_team_info_keeps_workspace_mount_and_absolute_path():
    body = build_team_info_text(
        team_info={"team_name": "demo", "display_name": "Demo", "desc": "Ship it"},
        team_workspace_mount=".team/demo/",
        team_workspace_path="/tmp/demo-workspace",
        language="en",
    )
    assert body is not None
    assert "`.team/demo/`" in body
    assert "Absolute path: `/tmp/demo-workspace`" in body


@pytest.mark.level0
def test_team_info_supports_path_only_workspace():
    body = build_team_info_text(
        team_info={"team_name": "demo", "display_name": "Demo", "desc": "Ship it"},
        team_workspace_path="/tmp/demo-workspace",
        language="en",
    )
    assert body is not None
    assert "`.team/" not in body
    assert "Team Shared Workspace: `/tmp/demo-workspace`" in body


# ---------------------------------------------------------------------------
# Roster snapshot
# ---------------------------------------------------------------------------


@pytest.mark.level0
def test_roster_snapshot_lists_peers():
    body = build_roster_snapshot_text(
        members=[_member("dev1", "Dev", "Coder"), _member("qa1", "QA")],
        language="cn",
    )
    assert body is not None
    assert "# 成员关系" in body
    assert "member_name=dev1 display_name=Dev :: Coder" in body
    assert "member_name=qa1 display_name=QA" in body


@pytest.mark.level0
def test_roster_snapshot_empty_returns_none():
    assert build_roster_snapshot_text(members=[], language="cn") is None
    assert build_roster_snapshot_text(members=None, language="cn") is None


@pytest.mark.level0
def test_roster_snapshot_human_tag_is_gated():
    members = [_member("alice", "Alice", role="human_agent")]
    tagged = build_roster_snapshot_text(members=members, mark_humans=True, language="cn")
    untagged = build_roster_snapshot_text(members=members, mark_humans=False, language="cn")
    assert tagged is not None and "[human]" in tagged
    assert untagged is not None and "[human]" not in untagged


# ---------------------------------------------------------------------------
# Roster diff
# ---------------------------------------------------------------------------


@pytest.mark.level0
def test_diff_detects_joined():
    delta = diff_roster([_member("dev1")], [_member("dev1"), _member("dev2")])
    assert [m["member_name"] for m in delta.joined] == ["dev2"]
    assert delta.left == []
    assert delta.changed == []


@pytest.mark.level0
def test_diff_detects_left():
    delta = diff_roster([_member("dev1"), _member("dev2")], [_member("dev1")])
    assert [m["member_name"] for m in delta.left] == ["dev2"]
    assert delta.joined == []


@pytest.mark.level0
def test_diff_detects_changed_desc():
    delta = diff_roster([_member("dev1", desc="old")], [_member("dev1", desc="new")])
    assert [m["member_name"] for m in delta.changed] == ["dev1"]
    assert delta.changed[0]["desc"] == "new"


@pytest.mark.level0
def test_diff_ignores_untracked_fields():
    old = [{"member_name": "dev1", "display_name": "Dev", "desc": "", "role": "teammate", "status": "ready"}]
    new = [{"member_name": "dev1", "display_name": "Dev", "desc": "", "role": "teammate", "status": "busy"}]
    assert diff_roster(old, new).is_empty()


@pytest.mark.level0
def test_diff_of_identical_rosters_is_empty():
    roster = [_member("dev1"), _member("dev2")]
    assert diff_roster(roster, list(roster)).is_empty()


@pytest.mark.level0
def test_diff_from_none_treats_everything_as_joined():
    delta = diff_roster(None, [_member("dev1")])
    assert [m["member_name"] for m in delta.joined] == ["dev1"]


# ---------------------------------------------------------------------------
# Roster delta body
# ---------------------------------------------------------------------------


@pytest.mark.level0
def test_roster_delta_body_marks_each_kind():
    delta = diff_roster(
        [_member("dev1"), _member("qa1", desc="old")],
        [_member("dev2"), _member("qa1", desc="new")],
    )
    body = build_roster_delta_text(delta=delta, language="cn")
    assert body is not None
    assert "# 成员变更" in body
    assert "[加入] member_name=dev2" in body
    assert "[退出] member_name=dev1" in body
    assert "[信息更新] member_name=qa1" in body


@pytest.mark.level0
def test_roster_delta_body_english():
    delta = diff_roster([], [_member("dev1")])
    body = build_roster_delta_text(delta=delta, language="en")
    assert body is not None
    assert "# Roster Change" in body
    assert "[joined] member_name=dev1" in body


@pytest.mark.level0
def test_roster_delta_empty_returns_none():
    assert build_roster_delta_text(delta=diff_roster([], []), language="cn") is None


@pytest.mark.level0
def test_roster_delta_respects_human_tag_gate():
    delta = diff_roster([], [_member("alice", role="human_agent")])
    tagged = build_roster_delta_text(delta=delta, mark_humans=True, language="cn")
    untagged = build_roster_delta_text(delta=delta, mark_humans=False, language="cn")
    assert tagged is not None and "[human]" in tagged
    assert untagged is not None and "[human]" not in untagged


# ---------------------------------------------------------------------------
# Line formatting + XML wrapper
# ---------------------------------------------------------------------------


@pytest.mark.level0
def test_member_line_without_prefix_or_desc():
    assert format_member_line(_member("dev1", "Dev")) == "- member_name=dev1 display_name=Dev"


@pytest.mark.level0
def test_member_line_with_prefix():
    line = format_member_line(_member("dev1", "Dev"), prefix="加入")
    assert line == "- [加入] member_name=dev1 display_name=Dev"


@pytest.mark.level0
def test_team_context_wrapper_escapes_body():
    rendered = render_team_context(body="a < b & c")
    assert rendered.startswith("<team-context>")
    assert rendered.endswith("</team-context>")
    assert "a &lt; b &amp; c" in rendered

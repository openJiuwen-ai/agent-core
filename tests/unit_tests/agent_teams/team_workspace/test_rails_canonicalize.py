# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for TeamWorkspaceRail path canonicalization (flat mount)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from openjiuwen.agent_teams.team_workspace.rails import TeamWorkspaceRail


def _rail(workspace_path: str, team_name: str = "team-alpha") -> TeamWorkspaceRail:
    ws = SimpleNamespace(workspace_path=workspace_path, team_name=team_name)
    return TeamWorkspaceRail(workspace_manager=ws, member_name="alice")


@pytest.mark.level0
def test_canonicalize_flat_relative_unchanged():
    rail = _rail("/tmp/shared")
    assert rail._canonicalize_team_path(".team/debate/pos.md") == ".team/debate/pos.md"


@pytest.mark.level0
def test_canonicalize_strips_legacy_hub_segment():
    rail = _rail("/tmp/shared", team_name="team-alpha")
    assert (
        rail._canonicalize_team_path(".team/team-alpha/debate/pos.md")
        == ".team/debate/pos.md"
    )
    assert rail._canonicalize_team_path(".team/team-alpha") == ".team"


@pytest.mark.level0
def test_canonicalize_abs_nested_mount_under_shared_root():
    """Models often join abs root + `.team/` — rewrite onto the mount."""
    rail = _rail("/tmp/shared", team_name="team-alpha")
    assert (
        rail._canonicalize_team_path("/tmp/shared/.team/position-data.md")
        == ".team/position-data.md"
    )
    assert (
        rail._canonicalize_team_path("/tmp/shared/.team/team-alpha/position-data.md")
        == ".team/position-data.md"
    )


@pytest.mark.level0
def test_canonicalize_leaves_correct_abs_under_shared_root():
    rail = _rail("/tmp/shared")
    assert (
        rail._canonicalize_team_path("/tmp/shared/position-data.md")
        == "/tmp/shared/position-data.md"
    )


@pytest.mark.level0
def test_resolve_workspace_relative_strips_legacy_hub():
    rail = _rail("/tmp/shared", team_name="team-alpha")
    assert (
        rail._resolve_workspace_relative(".team/team-alpha/artifacts/a.md")
        == "artifacts/a.md"
    )
    assert rail._resolve_workspace_relative(".team/artifacts/a.md") == "artifacts/a.md"

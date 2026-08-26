# coding: utf-8

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from openjiuwen.agent_teams import paths as apaths
from openjiuwen.agent_teams.team_workspace.paths import (
    MEMBER_MODE_DYNAMIC,
    MEMBER_MODE_LEADER,
    MEMBER_MODE_PREDEFINED,
    member_dir_name,
    member_real_dir,
)


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path) -> Iterator[None]:
    apaths.configure_openjiuwen_home(tmp_path / "oj-home")
    yield
    apaths.reset_openjiuwen_home()


@pytest.mark.level0
def test_member_dir_name_prefix_on() -> None:
    assert member_dir_name("teamA", "memX", member_workspace_prefix=True) == "teamA#memX"


@pytest.mark.level0
def test_member_dir_name_prefix_off() -> None:
    assert member_dir_name("teamA", "memX", member_workspace_prefix=False) == "memX"


@pytest.mark.level0
def test_member_real_dir_leader_is_in_team() -> None:
    got = member_real_dir("teamA", "leader", MEMBER_MODE_LEADER)
    assert got == apaths.team_member_workspace_dir("teamA", "leader")


@pytest.mark.level0
def test_member_real_dir_predefined_is_agent_teams_shared() -> None:
    got = member_real_dir("teamA", "shared", MEMBER_MODE_PREDEFINED)
    assert got == apaths.get_agent_teams_home() / "shared"


@pytest.mark.level0
def test_member_real_dir_dynamic_with_prefix() -> None:
    got = member_real_dir("teamA", "memX", MEMBER_MODE_DYNAMIC, member_workspace_prefix=True)
    assert got == apaths.get_agent_teams_home() / "teamA#memX"


@pytest.mark.level0
def test_member_real_dir_dynamic_without_prefix() -> None:
    got = member_real_dir("teamA", "memX", MEMBER_MODE_DYNAMIC, member_workspace_prefix=False)
    assert got == apaths.get_agent_teams_home() / "memX"

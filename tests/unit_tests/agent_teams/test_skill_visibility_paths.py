# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Team-level Skill visibility: one library, per-workspace declarations.

These tests cover the agent_teams-facing contract: where the single Skill
library lives, where a team's and a member's ``skills-visibility.json`` land,
and what a member's effective view is once the two documents are composed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openjiuwen.agent_teams import paths
from openjiuwen.agent_teams.skill.visibility import (
    SCOPE_MEMBER,
    SCOPE_TEAM,
    FileSkillVisibilityProvider,
    bootstrap_skill_visibility,
    build_skill_visibility_provider,
    read_skill_visibility,
    set_skill_visibility,
    update_skill_visibility,
)
from tests.test_logger import logger as test_logger

TEAM_NAME = "demo-team"
MEMBER_NAME = "reviewer"


def teardown_function():
    """Drop the process-wide path overrides between tests."""
    paths.reset_openjiuwen_home()
    paths.reset_global_skills_dir()


@pytest.fixture
def team_home(tmp_path: Path) -> Path:
    """Point both the home layout and the Skill library at a throwaway tree."""
    paths.configure_openjiuwen_home(tmp_path / ".openjiuwen")
    paths.configure_global_skills_dir(tmp_path / "library")
    return paths.team_home(TEAM_NAME)


def _provider(global_disabled: list[str] | None = None) -> FileSkillVisibilityProvider:
    """Build the provider a team member's Skill rail would be wired with."""

    def load_disabled() -> list[str]:
        return list(global_disabled or [])

    loader = load_disabled if global_disabled is not None else None
    return build_skill_visibility_provider(
        member_path=paths.member_skill_visibility_path(TEAM_NAME, MEMBER_NAME),
        member_id=MEMBER_NAME,
        team_path=paths.team_skill_visibility_path(TEAM_NAME),
        team_id=TEAM_NAME,
        global_disabled_loader=loader,
    )


@pytest.mark.level0
def test_global_skills_dir_is_a_single_overridable_library(tmp_path: Path):
    """Every team and member resolves to the one physical library."""
    library = tmp_path / "library"
    paths.configure_global_skills_dir(library)

    assert paths.global_skills_dir() == library
    assert paths.GLOBAL_SKILLS_DIR == library

    paths.reset_global_skills_dir()
    assert paths.global_skills_dir() == paths.get_openjiuwen_home() / "workspace" / "skills"


@pytest.mark.level0
def test_visibility_declaration_sits_at_the_workspace_root(team_home: Path):
    """The file is a workspace-root file, never a per-member skills/ directory."""
    member_path = paths.member_skill_visibility_path(TEAM_NAME, MEMBER_NAME)
    team_path = paths.team_skill_visibility_path(TEAM_NAME)
    test_logger.info("member declaration: %s / team declaration: %s", member_path, team_path)

    assert member_path == team_home / "workspaces" / f"{MEMBER_NAME}_workspace" / paths.SKILL_VISIBILITY_FILENAME
    assert team_path == team_home / "team-workspace" / paths.SKILL_VISIBILITY_FILENAME
    assert member_path.parent == paths.team_member_workspace_dir(TEAM_NAME, MEMBER_NAME)
    assert team_path.parent == paths.team_workspace_dir(TEAM_NAME)
    assert "skills" not in member_path.parts[:-1]


@pytest.mark.level0
def test_member_without_a_declaration_inherits_the_whole_library(team_home: Path):
    """No document at all means no filtering, not zero Skills."""
    enabled, disabled = _provider()()
    test_logger.info("enabled=%s disabled=%s", enabled, disabled)

    assert enabled == set()
    assert disabled == set()


@pytest.mark.level0
def test_member_and_team_allow_lists_are_unioned(team_home: Path):
    """D6: enabled = member.allow UNION team.allow."""
    set_skill_visibility(
        paths.member_skill_visibility_path(TEAM_NAME, MEMBER_NAME),
        scope=SCOPE_MEMBER,
        entity_id=MEMBER_NAME,
        allow=["pdf_report"],
        deny=None,
    )
    set_skill_visibility(
        paths.team_skill_visibility_path(TEAM_NAME),
        scope=SCOPE_TEAM,
        entity_id=TEAM_NAME,
        allow=["web_search"],
        deny=None,
    )

    enabled, disabled = _provider()()

    assert enabled == {"pdf_report", "web_search"}
    assert disabled == set()


@pytest.mark.level0
def test_deny_wins_over_allow_from_either_document(team_home: Path):
    """D6: disabled = member.deny UNION team.deny UNION the library-wide switch."""
    set_skill_visibility(
        paths.member_skill_visibility_path(TEAM_NAME, MEMBER_NAME),
        scope=SCOPE_MEMBER,
        entity_id=MEMBER_NAME,
        allow=["pdf_report", "web_search", "shell_exec"],
        deny=["shell_exec"],
    )
    set_skill_visibility(
        paths.team_skill_visibility_path(TEAM_NAME),
        scope=SCOPE_TEAM,
        entity_id=TEAM_NAME,
        allow=["web_search"],
        deny=["web_search"],
    )

    enabled, disabled = _provider(global_disabled=["quarantined"])()
    test_logger.info("enabled=%s disabled=%s", enabled, disabled)

    assert enabled == {"pdf_report", "web_search", "shell_exec"}
    # A name present in both lists stays denied: deny is unconditional.
    assert disabled == {"shell_exec", "web_search", "quarantined"}


@pytest.mark.level0
def test_bootstrap_seeds_a_member_only_once(team_home: Path):
    """D1: config seeds the file once; later config edits never overwrite it."""
    member_path = paths.member_skill_visibility_path(TEAM_NAME, MEMBER_NAME)

    seeded = bootstrap_skill_visibility(
        member_path,
        scope=SCOPE_MEMBER,
        entity_id=MEMBER_NAME,
        allow=["pdf_report"],
        bootstrapped_from="config:agents.reviewer.skills",
    )
    assert seeded.allow == ["pdf_report"]

    # An explicit grant happens after assembly...
    update_skill_visibility(
        member_path,
        scope=SCOPE_MEMBER,
        entity_id=MEMBER_NAME,
        add_allow=["web_search"],
    )

    # ...and re-assembly with a different config must not roll it back.
    again = bootstrap_skill_visibility(
        member_path,
        scope=SCOPE_MEMBER,
        entity_id=MEMBER_NAME,
        allow=["something_else"],
        bootstrapped_from="config:agents.reviewer.skills",
    )
    test_logger.info("after re-bootstrap: %s", again.to_dict())

    assert again.allow == ["pdf_report", "web_search"]
    stored = read_skill_visibility(member_path, scope=SCOPE_MEMBER, entity_id=MEMBER_NAME)
    assert stored.allow == ["pdf_report", "web_search"]


@pytest.mark.level0
def test_bootstrap_without_config_skills_leaves_the_member_unrestricted(team_home: Path):
    """D7: a newly installed Skill needs no declaration edit for a default member."""
    member_path = paths.member_skill_visibility_path(TEAM_NAME, MEMBER_NAME)

    seeded = bootstrap_skill_visibility(
        member_path,
        scope=SCOPE_MEMBER,
        entity_id=MEMBER_NAME,
        allow=None,
        bootstrapped_from="config:agents.reviewer.skills",
    )

    assert seeded.is_unrestricted
    enabled, disabled = _provider()()
    assert enabled == set()
    assert disabled == set()

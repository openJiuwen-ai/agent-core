# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for the team Skill rail declaration and the rail's visibility filter."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openjiuwen.agent_teams import paths
from openjiuwen.agent_teams.paths import SKILL_VISIBILITY_FILENAME
from openjiuwen.agent_teams.rails.builtin_elements import SKILL_USE, SYS_OPERATION
from openjiuwen.agent_teams.rails.elements import TEAM_SKILL_USE
from openjiuwen.agent_teams.rails.team_skill_use_rail import create_team_skill_use_rail
from openjiuwen.agent_teams.schema.deep_agent_spec import RailSpec
from openjiuwen.agent_teams.skill.rail_spec import build_team_skill_rail_spec
from openjiuwen.agent_teams.skill.visibility import (
    SCOPE_MEMBER,
    SCOPE_TEAM,
    set_skill_visibility,
)
from openjiuwen.core.single_agent.skills.skill_manager import Skill
from tests.test_logger import logger as test_logger


def teardown_function():
    """Drop the process-wide path overrides between tests."""
    paths.reset_openjiuwen_home()
    paths.reset_global_skills_dir()


@pytest.fixture
def team_home(tmp_path: Path) -> Path:
    """Point both the home layout and the Skill library at a throwaway tree."""
    library = tmp_path / "library"
    library.mkdir(parents=True)
    paths.configure_openjiuwen_home(tmp_path / ".openjiuwen")
    paths.configure_global_skills_dir(library)
    return paths.team_home("alpha")


def _write_skill(library: Path, name: str) -> None:
    """Create a minimal Skill directory in the library."""
    skill_dir = library / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {name} skill\n---\nbody\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# build_team_skill_rail_spec
# ---------------------------------------------------------------------------


@pytest.mark.level0
def test_rail_spec_carries_config_skills_as_seed(team_home: Path):
    spec = build_team_skill_rail_spec(
        team_name="alpha",
        member_name="alice",
        config_skills=["xlsx", "pdf"],
        declared_rails=[],
    )

    assert spec is not None
    assert spec.type == TEAM_SKILL_USE
    assert spec.params["bootstrap_allow"] == ["xlsx", "pdf"]
    assert spec.params["team_name"] == "alpha"
    assert spec.params["skills_dir"] == [str(paths.global_skills_dir())]
    member_path = Path(spec.params["member_visibility_path"])
    assert member_path.name == SKILL_VISIBILITY_FILENAME
    assert member_path.parent == paths.team_member_workspace_dir("alpha", "alice")
    test_logger.info("team Skill rail spec params: %s", spec.params)


@pytest.mark.level0
def test_rail_spec_is_none_without_member_identity(team_home: Path):
    assert build_team_skill_rail_spec(
        team_name="alpha",
        member_name="",
        config_skills=None,
        declared_rails=[],
    ) is None


@pytest.mark.level0
@pytest.mark.parametrize("declared", [SKILL_USE, TEAM_SKILL_USE])
def test_rail_spec_defers_to_a_declared_skill_rail(team_home: Path, declared: str):
    assert build_team_skill_rail_spec(
        team_name="alpha",
        member_name="alice",
        config_skills=["xlsx"],
        declared_rails=[RailSpec(type=declared)],
    ) is None


@pytest.mark.level0
def test_rail_spec_include_tools_defers_to_sys_operation(team_home: Path):
    without_fs = build_team_skill_rail_spec(
        team_name="alpha",
        member_name="alice",
        config_skills=None,
        declared_rails=[],
    )
    with_fs = build_team_skill_rail_spec(
        team_name="alpha",
        member_name="alice",
        config_skills=None,
        declared_rails=[RailSpec(type=SYS_OPERATION)],
    )

    assert without_fs.params["include_tools"] is True
    assert with_fs.params["include_tools"] is False


@pytest.mark.level0
def test_rail_spec_uses_the_managed_team_workspace_root(team_home: Path, tmp_path: Path):
    custom_root = tmp_path / "custom-team-workspace"
    spec = build_team_skill_rail_spec(
        team_name="alpha",
        member_name="alice",
        config_skills=None,
        declared_rails=[],
        team_workspace_path=str(custom_root),
    )

    assert Path(spec.params["team_visibility_path"]) == custom_root / SKILL_VISIBILITY_FILENAME


# ---------------------------------------------------------------------------
# TeamSkillUseRail
# ---------------------------------------------------------------------------


def _build_rail(team_home: Path, *, bootstrap_allow: list[str]):
    """Build a rail for member ``alice`` of team ``alpha``."""
    return create_team_skill_use_rail(
        member_name="alice",
        team_name="alpha",
        member_visibility_path=paths.member_skill_visibility_path("alpha", "alice"),
        team_visibility_path=paths.team_skill_visibility_path("alpha"),
        skills_dir=[str(paths.global_skills_dir())],
        bootstrap_allow=bootstrap_allow,
        include_tools=False,
    )


@pytest.mark.level0
def test_rail_seeds_the_member_declaration_once(team_home: Path):
    _build_rail(team_home, bootstrap_allow=["xlsx"])
    declaration = paths.member_skill_visibility_path("alpha", "alice")
    payload = json.loads(declaration.read_text(encoding="utf-8"))

    assert payload["scope"] == SCOPE_MEMBER
    assert payload["id"] == "alice"
    assert payload["allow"] == ["xlsx"]

    # A second assembly with a different config value must not roll back the
    # stored declaration -- the file is the authority.
    _build_rail(team_home, bootstrap_allow=["pdf"])
    assert json.loads(declaration.read_text(encoding="utf-8"))["allow"] == ["xlsx"]


@pytest.mark.level0
def test_rail_filters_skills_by_member_allow(team_home: Path):
    rail = _build_rail(team_home, bootstrap_allow=["xlsx"])
    skills = [
        Skill(name="xlsx", description="x", directory=Path("xlsx")),
        Skill(name="pdf", description="p", directory=Path("pdf")),
    ]

    assert [skill.name for skill in rail._filter_skills(skills)] == ["xlsx"]


@pytest.mark.level0
def test_empty_allow_inherits_the_whole_library(team_home: Path):
    rail = _build_rail(team_home, bootstrap_allow=[])
    skills = [
        Skill(name="xlsx", description="x", directory=Path("xlsx")),
        Skill(name="pdf", description="p", directory=Path("pdf")),
    ]

    assert [skill.name for skill in rail._filter_skills(skills)] == ["xlsx", "pdf"]


@pytest.mark.level0
def test_team_allow_widens_and_team_deny_wins(team_home: Path):
    rail = _build_rail(team_home, bootstrap_allow=["xlsx"])
    set_skill_visibility(
        paths.team_skill_visibility_path("alpha"),
        scope=SCOPE_TEAM,
        entity_id="alpha",
        allow=["pdf"],
        deny=["xlsx"],
    )
    skills = [
        Skill(name="xlsx", description="x", directory=Path("xlsx")),
        Skill(name="pdf", description="p", directory=Path("pdf")),
    ]

    # allow is the union; deny always wins over allow.
    assert [skill.name for skill in rail._filter_skills(skills)] == ["pdf"]


@pytest.mark.level0
def test_snapshot_signature_moves_when_a_grant_changes(team_home: Path):
    library = paths.global_skills_dir()
    _write_skill(library, "xlsx")
    _write_skill(library, "pdf")
    rail = _build_rail(team_home, bootstrap_allow=["xlsx"])

    before = rail._build_skills_snapshot_signature()
    set_skill_visibility(
        paths.member_skill_visibility_path("alpha", "alice"),
        scope=SCOPE_MEMBER,
        entity_id="alice",
        allow=["xlsx", "pdf"],
        deny=None,
    )
    after = rail._build_skills_snapshot_signature()

    assert before != after
    assert rail.enabled_skills == {"xlsx", "pdf"}
    test_logger.info("signature moved on a grant: %s -> %s", len(before), len(after))

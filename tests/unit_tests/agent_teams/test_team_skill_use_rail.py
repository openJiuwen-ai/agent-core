# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Team Skill rail: one shared library narrowed by visibility declarations.

These tests pin the behaviour ``TeamSkillUseRail`` adds on top of the shared
``SkillUseRail`` and, just as importantly, the behaviour it must *not* break:
an empty allow-list still means "inherit the whole library", and deny still
wins over allow.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from openjiuwen.agent_teams import paths
from openjiuwen.agent_teams.rails.elements import build_team_skill_use_rail
from openjiuwen.agent_teams.rails.team_skill_use_rail import (
    TeamSkillUseRail,
    create_team_skill_use_rail,
    global_disabled_skills,
)
from openjiuwen.agent_teams.skill.visibility import (
    SCOPE_MEMBER,
    SCOPE_TEAM,
    StaticSkillVisibilityProvider,
    read_skill_visibility,
    set_skill_visibility,
)
from openjiuwen.core.single_agent.skills.skill_manager import Skill
from tests.test_logger import logger as test_logger

TEAM_NAME = "demo-team"
MEMBER_NAME = "reviewer"
LIBRARY_SKILLS = ("alpha", "beta", "gamma")


def teardown_function():
    """Drop the process-wide path overrides between tests."""
    paths.reset_openjiuwen_home()
    paths.reset_global_skills_dir()


class _FakeSession:
    """Minimal session double exposing the rail's state protocol."""

    def __init__(self) -> None:
        self._state: dict[str, Any] = {}

    def get_state(self, key: str) -> Any:
        """Return the stored value for one state key."""
        return self._state.get(key)

    def update_state(self, values: dict[str, Any]) -> None:
        """Merge new values into the session state."""
        self._state.update(values)


class _BrokenProvider:
    """Visibility provider that always fails, to exercise degradation."""

    def __call__(self) -> tuple[set[str], set[str]]:
        """Raise, standing in for an unreadable declaration."""
        raise RuntimeError("declaration unavailable")

    def metadata_signature(self) -> tuple[tuple[str, float], ...]:
        """Raise, standing in for an unstattable declaration."""
        raise RuntimeError("declaration unavailable")


@pytest.fixture
def library(tmp_path: Path) -> Path:
    """Create a Skill library holding :data:`LIBRARY_SKILLS`."""
    root = tmp_path / "library"
    for name in LIBRARY_SKILLS:
        skill_dir = root / name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {name} skill\n---\nbody\n",
            encoding="utf-8",
        )
    return root


@pytest.fixture
def member_path(tmp_path: Path) -> Path:
    """Return the member declaration path (the file itself is not created)."""
    return tmp_path / "member_workspace" / paths.SKILL_VISIBILITY_FILENAME


@pytest.fixture
def team_path(tmp_path: Path) -> Path:
    """Return the team declaration path (the file itself is not created)."""
    return tmp_path / "team-workspace" / paths.SKILL_VISIBILITY_FILENAME


def _build_rail(library: Path, member_path: Path, team_path: Path | None) -> TeamSkillUseRail:
    """Build a rail over one library and the given declaration paths."""
    return create_team_skill_use_rail(
        member_name=MEMBER_NAME,
        team_name=TEAM_NAME,
        member_visibility_path=member_path,
        team_visibility_path=team_path,
        skills_dir=[str(library)],
        bootstrap_allow=[],
    )


async def _visible_names(rail: TeamSkillUseRail) -> list[str]:
    """Load the library through the rail and return the visible Skill names."""
    await rail._prepare_skills()
    return sorted(skill.name for skill in rail.skills)


@pytest.mark.asyncio
@pytest.mark.level0
async def test_absent_declarations_inherit_the_whole_library(library, member_path, team_path):
    """No declaration file anywhere means the member sees every Skill.

    This is the inherited "empty allow-list does not filter" rule; turning a
    missing file into "deny everything" would silently strip every member.
    """
    rail = _build_rail(library, member_path, team_path)

    names = await _visible_names(rail)

    test_logger.info(f"visible without any declaration: {names}")
    assert names == sorted(LIBRARY_SKILLS)


@pytest.mark.asyncio
@pytest.mark.level0
async def test_member_allow_narrows_the_view(library, member_path, team_path):
    """A non-empty member allow-list restricts the view to those Skills."""
    set_skill_visibility(
        member_path,
        scope=SCOPE_MEMBER,
        entity_id=MEMBER_NAME,
        allow=["alpha"],
        deny=None,
    )
    rail = _build_rail(library, member_path, team_path)

    names = await _visible_names(rail)

    test_logger.info(f"visible with member allow=[alpha]: {names}")
    assert names == ["alpha"]


@pytest.mark.asyncio
@pytest.mark.level0
async def test_team_allow_unions_with_member_allow(library, member_path, team_path):
    """The team grant adds to the member grant instead of intersecting it."""
    set_skill_visibility(
        member_path,
        scope=SCOPE_MEMBER,
        entity_id=MEMBER_NAME,
        allow=["alpha"],
        deny=None,
    )
    set_skill_visibility(
        team_path,
        scope=SCOPE_TEAM,
        entity_id=TEAM_NAME,
        allow=["beta"],
        deny=None,
    )
    rail = _build_rail(library, member_path, team_path)

    names = await _visible_names(rail)

    test_logger.info(f"visible with member+team allow: {names}")
    assert names == ["alpha", "beta"]


@pytest.mark.asyncio
@pytest.mark.level0
async def test_deny_beats_allow(library, member_path, team_path):
    """A denied Skill stays invisible even when the same document allows it."""
    set_skill_visibility(
        member_path,
        scope=SCOPE_MEMBER,
        entity_id=MEMBER_NAME,
        allow=["alpha", "beta"],
        deny=["beta"],
    )
    rail = _build_rail(library, member_path, team_path)

    names = await _visible_names(rail)

    test_logger.info(f"visible with member deny=[beta]: {names}")
    assert names == ["alpha"]


@pytest.mark.asyncio
@pytest.mark.level1
async def test_team_deny_beats_member_allow(library, member_path, team_path):
    """A team-level revocation cannot be worked around by a member grant."""
    set_skill_visibility(
        member_path,
        scope=SCOPE_MEMBER,
        entity_id=MEMBER_NAME,
        allow=["alpha", "beta"],
        deny=None,
    )
    set_skill_visibility(
        team_path,
        scope=SCOPE_TEAM,
        entity_id=TEAM_NAME,
        allow=None,
        deny=["alpha"],
    )
    rail = _build_rail(library, member_path, team_path)

    names = await _visible_names(rail)

    test_logger.info(f"visible with team deny=[alpha]: {names}")
    assert names == ["beta"]


@pytest.mark.asyncio
@pytest.mark.level1
async def test_library_kill_switch_removes_a_skill(library, member_path, team_path):
    """``skills_state.json`` disables a Skill for every member, allow or not."""
    (library / "skills_state.json").write_text(
        json.dumps({"skill_configs": {"gamma": {"enabled": False}}}),
        encoding="utf-8",
    )
    assert global_disabled_skills([str(library)]) == ["gamma"]
    rail = _build_rail(library, member_path, team_path)

    names = await _visible_names(rail)

    test_logger.info(f"visible with gamma globally disabled: {names}")
    assert names == ["alpha", "beta"]


@pytest.mark.asyncio
@pytest.mark.level0
async def test_grant_written_later_moves_the_snapshot_signature(library, member_path, team_path):
    """A grant with no library change still forces a prompt rebuild.

    Nothing under the library moves when an operator edits a declaration, so a
    signature built only from ``SKILL.md`` mtimes would never notice.
    """
    set_skill_visibility(
        member_path,
        scope=SCOPE_MEMBER,
        entity_id=MEMBER_NAME,
        allow=["alpha"],
        deny=None,
    )
    rail = _build_rail(library, member_path, team_path)
    before = rail._build_skills_snapshot_signature()

    set_skill_visibility(
        member_path,
        scope=SCOPE_MEMBER,
        entity_id=MEMBER_NAME,
        allow=["alpha", "beta"],
        deny=None,
    )
    after = rail._build_skills_snapshot_signature()

    test_logger.info(f"signature moved after the grant: {before != after}")
    assert before != after
    assert await _visible_names(rail) == ["alpha", "beta"]


@pytest.mark.asyncio
@pytest.mark.level1
async def test_revocation_takes_effect_on_the_session_view(library, member_path, team_path):
    """A Skill revoked mid-session stops being resolvable by the skill tools."""
    rail = _build_rail(library, member_path, team_path)
    session = _FakeSession()
    await rail._prepare_skills()
    rail._save_session_baseline(session, rail.skills)
    assert sorted(skill.name for skill in rail.get_skills_for_session(session)) == sorted(LIBRARY_SKILLS)

    set_skill_visibility(
        member_path,
        scope=SCOPE_MEMBER,
        entity_id=MEMBER_NAME,
        allow=None,
        deny=["gamma"],
    )
    await rail._prepare_skills()
    names = sorted(skill.name for skill in rail.get_skills_for_session(session))

    test_logger.info(f"session view after revoking gamma: {names}")
    assert names == ["alpha", "beta"]


@pytest.mark.level1
def test_broken_provider_keeps_the_last_known_good_sets(library):
    """A failing declaration read never widens nor empties the member's view."""
    rail = TeamSkillUseRail(
        skills_dir=[str(library)],
        visibility_provider=StaticSkillVisibilityProvider(enabled=["alpha"], disabled=["beta"]),
    )
    assert rail.enabled_skills == {"alpha"}

    rail.visibility_provider = _BrokenProvider()
    rail._apply_visibility()

    test_logger.info(f"sets after a provider failure: {rail.enabled_skills} / {rail.disabled_skills}")
    assert rail.enabled_skills == {"alpha"}
    assert rail.disabled_skills == {"beta"}


@pytest.mark.level1
def test_broken_provider_does_not_break_the_signature(library):
    """A failing provider still yields a signature: library plus retained grants.

    The snapshot is built on every model call, so a declaration that cannot be
    read must degrade rather than raise — and it must keep describing the
    allow / deny pair still in force, or the next call would look like a change.
    """
    rail = TeamSkillUseRail(
        skills_dir=[str(library)],
        visibility_provider=StaticSkillVisibilityProvider(enabled=["alpha"]),
    )
    rail.visibility_provider = _BrokenProvider()

    signature = rail._build_skills_snapshot_signature()

    test_logger.info(f"signature with a broken provider: {signature}")
    library_entries = [entry for entry in signature if not entry[0].startswith("skill-visibility.")]
    grant_entries = [entry[0] for entry in signature if entry[0].startswith("skill-visibility.")]
    assert len(library_entries) == len(LIBRARY_SKILLS)
    assert grant_entries == ["skill-visibility.allow:alpha"]
    assert signature == rail._build_skills_snapshot_signature()


@pytest.mark.level0
def test_seeding_is_file_authoritative(library, member_path, team_path):
    """The blueprint seeds the declaration once; a later grant is not rolled back."""
    create_team_skill_use_rail(
        member_name=MEMBER_NAME,
        team_name=TEAM_NAME,
        member_visibility_path=member_path,
        team_visibility_path=team_path,
        skills_dir=[str(library)],
        bootstrap_allow=["alpha"],
    )
    assert read_skill_visibility(member_path, scope=SCOPE_MEMBER, entity_id=MEMBER_NAME).allow == ["alpha"]

    set_skill_visibility(
        member_path,
        scope=SCOPE_MEMBER,
        entity_id=MEMBER_NAME,
        allow=["alpha", "beta"],
        deny=None,
    )
    create_team_skill_use_rail(
        member_name=MEMBER_NAME,
        team_name=TEAM_NAME,
        member_visibility_path=member_path,
        team_visibility_path=team_path,
        skills_dir=[str(library)],
        bootstrap_allow=["alpha"],
    )

    stored = read_skill_visibility(member_path, scope=SCOPE_MEMBER, entity_id=MEMBER_NAME)
    test_logger.info(f"declaration after re-assembly: {stored.allow}")
    assert stored.allow == ["alpha", "beta"]


@pytest.mark.level0
def test_element_builds_the_rail_over_the_configured_library(library, member_path, team_path):
    """The ``core.team.skill_use`` factory wires paths, library and seed."""
    context = SimpleNamespace(member_name=MEMBER_NAME)
    params = {
        "team_name": TEAM_NAME,
        "skills_dir": [str(library)],
        "member_visibility_path": str(member_path),
        "team_visibility_path": str(team_path),
        "bootstrap_allow": ["alpha"],
    }

    rail = build_team_skill_use_rail(params, context)

    test_logger.info(f"element-built rail enabled set: {rail.enabled_skills}")
    assert isinstance(rail, TeamSkillUseRail)
    assert rail.enabled_skills == {"alpha"}
    assert member_path.is_file()


@pytest.mark.level1
def test_element_is_gated_out_without_a_declaration_path(library):
    """Without a member declaration path there is nothing to narrow; no rail."""
    context = SimpleNamespace(member_name=MEMBER_NAME)

    rail = build_team_skill_use_rail({"team_name": TEAM_NAME, "skills_dir": [str(library)]}, context)

    test_logger.info(f"rail built without a declaration path: {rail}")
    assert rail is None


@pytest.mark.level1
def test_inherited_filter_is_reused_verbatim(library):
    """The subclass delegates the allow / deny decision to the base rail."""
    rail = TeamSkillUseRail(
        skills_dir=[str(library)],
        visibility_provider=StaticSkillVisibilityProvider(enabled=[], disabled=["beta"]),
    )
    skills = [Skill(name=name, description="", directory=library / name) for name in LIBRARY_SKILLS]

    filtered = [skill.name for skill in rail._filter_skills(skills)]

    test_logger.info(f"filtered names with empty allow and deny=[beta]: {filtered}")
    assert filtered == ["alpha", "gamma"]

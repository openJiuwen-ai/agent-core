# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for team-scoped Skill visibility declarations.

Skills live in exactly one physical library. Which team member may see which
Skill is expressed by ``skills-visibility.json`` documents, composed as::

    enabled  = member.allow UNION team.allow
    disabled = member.deny UNION team.deny UNION global_disabled

An empty allow-list means "inherit the whole library", never "deny everything",
and the deny-list always wins.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openjiuwen.agent_teams.skill.visibility import (
    AUTHORITY_EXPLICIT,
    AUTHORITY_MIGRATION,
    AUTHORITY_SEED,
    SCOPE_MEMBER,
    SCOPE_TEAM,
    SKILL_VISIBILITY_SCHEMA_VERSION,
    FileSkillVisibilityProvider,
    SkillVisibility,
    SkillVisibilityProvider,
    StaticSkillVisibilityProvider,
    bootstrap_skill_visibility,
    build_skill_visibility_provider,
    compose_skill_visibility,
    normalize_skill_names,
    read_skill_visibility,
    set_skill_visibility,
    update_skill_visibility,
    write_skill_visibility,
)
from tests.test_logger import logger as test_logger


def _member(allow: list[str] | None = None, deny: list[str] | None = None) -> SkillVisibility:
    """Build a member-scope document."""
    return SkillVisibility(scope=SCOPE_MEMBER, id="reviewer", allow=allow or [], deny=deny or [])


def _team(allow: list[str] | None = None, deny: list[str] | None = None) -> SkillVisibility:
    """Build a team-scope document."""
    return SkillVisibility(scope=SCOPE_TEAM, id="research_team", allow=allow or [], deny=deny or [])


def _visible(library: set[str], enabled: set[str], disabled: set[str]) -> set[str]:
    """Apply the rail's allow-then-deny filter to a set of Skill names."""
    survivors = {name for name in library if not enabled or name in enabled}
    return survivors - disabled


# ---------------------------------------------------------------------------
# Composition rules
# ---------------------------------------------------------------------------


@pytest.mark.level0
def test_compose_takes_the_union_of_member_and_team_allow():
    """A team grant widens what a restricted member may see."""
    enabled, disabled = compose_skill_visibility(
        _member(allow=["alpha"]),
        _team(allow=["beta"]),
        None,
    )

    assert enabled == {"alpha", "beta"}
    assert disabled == set()
    test_logger.info("composed allow union: %s", sorted(enabled))


@pytest.mark.level0
def test_compose_takes_the_union_of_member_team_and_global_deny():
    """All three deny sources land in the same effective deny-list."""
    enabled, disabled = compose_skill_visibility(
        _member(allow=["alpha"], deny=["gamma"]),
        _team(allow=["beta"], deny=["delta"]),
        ["epsilon"],
    )

    assert enabled == {"alpha", "beta"}
    assert disabled == {"gamma", "delta", "epsilon"}


@pytest.mark.level0
def test_compose_lets_deny_win_over_allow():
    """A Skill named in both lists is denied; deny is unconditional."""
    enabled, disabled = compose_skill_visibility(
        _member(allow=["alpha", "beta"]),
        _team(deny=["beta"]),
        None,
    )

    assert enabled == {"alpha", "beta"}
    assert disabled == {"beta"}
    # The rail resolves the conflict, so state the end result explicitly too.
    assert _visible({"alpha", "beta", "gamma"}, enabled, disabled) == {"alpha"}


@pytest.mark.level0
def test_compose_keeps_empty_allow_empty():
    """Empty allow must stay empty: substituting the library would freeze it."""
    enabled, disabled = compose_skill_visibility(_member(), _team(), None)

    assert enabled == set()
    assert disabled == set()


@pytest.mark.level0
def test_compose_without_a_team_uses_member_only():
    """A member outside any team composes against None."""
    enabled, disabled = compose_skill_visibility(
        _member(allow=["alpha"], deny=["beta"]),
        None,
        ["gamma"],
    )

    assert enabled == {"alpha"}
    assert disabled == {"beta", "gamma"}


@pytest.mark.level0
@pytest.mark.parametrize(
    ("allow", "deny", "expected"),
    [
        ([], [], {"alpha", "beta", "gamma"}),
        (["alpha"], [], {"alpha"}),
        ([], ["alpha"], {"beta", "gamma"}),
        (["alpha", "beta"], ["beta"], {"alpha"}),
    ],
)
def test_empty_allow_does_not_filter(allow, deny, expected):
    """An empty allow-list inherits the library instead of denying it."""
    enabled, disabled = compose_skill_visibility(_member(allow=allow, deny=deny), None, None)

    assert _visible({"alpha", "beta", "gamma"}, enabled, disabled) == expected


# ---------------------------------------------------------------------------
# Bootstrap: seed once, never overwrite
# ---------------------------------------------------------------------------


@pytest.mark.level0
def test_bootstrap_seeds_when_the_file_is_absent(tmp_path: Path):
    """The first bootstrap writes the config-provided allow-list."""
    path = tmp_path / "ws" / "skills-visibility.json"

    seeded = bootstrap_skill_visibility(
        path,
        scope=SCOPE_MEMBER,
        entity_id="reviewer",
        allow=["beta", "alpha", "alpha"],
        bootstrapped_from="config:agents.teammate.skills",
    )

    assert path.is_file()
    assert seeded.allow == ["alpha", "beta"]
    assert seeded.deny == []
    assert seeded.scope == SCOPE_MEMBER
    assert seeded.id == "reviewer"
    assert seeded.bootstrapped_from == "config:agents.teammate.skills"
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == SKILL_VISIBILITY_SCHEMA_VERSION
    test_logger.info("bootstrapped %s -> %s", path, seeded.allow)


@pytest.mark.level0
def test_bootstrap_never_overwrites_an_existing_file(tmp_path: Path):
    """Config seeds once; a later config change must not revert a grant."""
    path = tmp_path / "ws" / "skills-visibility.json"
    bootstrap_skill_visibility(
        path,
        scope=SCOPE_MEMBER,
        entity_id="reviewer",
        allow=["alpha"],
        bootstrapped_from="config:agents.teammate.skills",
    )
    update_skill_visibility(
        path,
        scope=SCOPE_MEMBER,
        entity_id="reviewer",
        add_allow=["beta"],
    )

    kept = bootstrap_skill_visibility(
        path,
        scope=SCOPE_MEMBER,
        entity_id="reviewer",
        allow=["alpha"],
        bootstrapped_from="config:agents.teammate.skills",
    )

    assert kept.allow == ["alpha", "beta"]
    assert read_skill_visibility(path, scope=SCOPE_MEMBER, entity_id="reviewer").allow == ["alpha", "beta"]


@pytest.mark.level1
def test_bootstrap_is_idempotent_across_repeated_assembly(tmp_path: Path):
    """Assembly may call bootstrap on every rebuild without side effects."""
    path = tmp_path / "ws" / "skills-visibility.json"
    first = bootstrap_skill_visibility(
        path,
        scope=SCOPE_TEAM,
        entity_id="research_team",
        allow=None,
        bootstrapped_from="team-workspace-init",
    )
    stamp = path.stat().st_mtime_ns

    second = bootstrap_skill_visibility(
        path,
        scope=SCOPE_TEAM,
        entity_id="research_team",
        allow=["alpha"],
        bootstrapped_from="team-workspace-init",
    )

    assert first.allow == []
    assert second.allow == []
    assert second.is_unrestricted is True
    assert path.stat().st_mtime_ns == stamp


@pytest.mark.level0
def test_bootstrap_with_empty_allow_inherits_the_whole_library(tmp_path: Path):
    """A default member sees Skills installed after it was bootstrapped."""
    path = tmp_path / "ws" / "skills-visibility.json"
    bootstrap_skill_visibility(
        path,
        scope=SCOPE_MEMBER,
        entity_id="reviewer",
        allow=[],
        bootstrapped_from="config:agents.teammate.skills",
    )

    provider = build_skill_visibility_provider(member_path=path, member_id="reviewer")
    enabled, disabled = provider()

    assert enabled == set()
    assert _visible({"alpha", "brand-new-skill"}, enabled, disabled) == {"alpha", "brand-new-skill"}


# ---------------------------------------------------------------------------
# Read / write / degradation
# ---------------------------------------------------------------------------


@pytest.mark.level0
def test_read_missing_file_is_unrestricted(tmp_path: Path):
    """No declaration means no restriction, not zero Skills."""
    visibility = read_skill_visibility(
        tmp_path / "absent.json",
        scope=SCOPE_MEMBER,
        entity_id="reviewer",
    )

    assert visibility.is_unrestricted is True
    assert visibility.scope == SCOPE_MEMBER
    assert visibility.id == "reviewer"


@pytest.mark.level1
def test_read_corrupt_file_degrades_to_unrestricted(tmp_path: Path):
    """A damaged document must never strip an agent of every Skill."""
    path = tmp_path / "skills-visibility.json"
    path.write_text("{not json", encoding="utf-8")

    visibility = read_skill_visibility(path, scope=SCOPE_MEMBER, entity_id="reviewer")

    assert visibility.is_unrestricted is True
    test_logger.info("corrupt declaration degraded to unrestricted")


@pytest.mark.level1
def test_read_non_object_document_degrades_to_unrestricted(tmp_path: Path):
    """A JSON array is not a document; it degrades like a corrupt file."""
    path = tmp_path / "skills-visibility.json"
    path.write_text('["alpha"]', encoding="utf-8")

    assert read_skill_visibility(path, scope=SCOPE_TEAM, entity_id="t").is_unrestricted is True


@pytest.mark.level1
def test_read_tolerates_malformed_fields(tmp_path: Path):
    """Odd field types degrade to permissive defaults instead of raising."""
    path = tmp_path / "skills-visibility.json"
    path.write_text(
        json.dumps(
            {
                "version": "one",
                "scope": "",
                "id": None,
                "bootstrapped_from": 42,
                "allow": "alpha",
                "deny": {"beta": True},
            }
        ),
        encoding="utf-8",
    )

    visibility = read_skill_visibility(path, scope=SCOPE_MEMBER, entity_id="reviewer")

    assert visibility.version == SKILL_VISIBILITY_SCHEMA_VERSION
    assert visibility.scope == SCOPE_MEMBER
    assert visibility.id == "reviewer"
    assert visibility.bootstrapped_from is None
    assert visibility.allow == ["alpha"]
    assert visibility.deny == []


@pytest.mark.level0
def test_write_then_read_round_trips(tmp_path: Path):
    """A written document reads back field for field."""
    path = tmp_path / "nested" / "skills-visibility.json"
    document = SkillVisibility(
        scope=SCOPE_TEAM,
        id="research_team",
        bootstrapped_from="migration:symlinks",
        allow=["alpha"],
        deny=["beta"],
    )

    write_skill_visibility(path, document)

    reloaded = read_skill_visibility(path, scope=SCOPE_TEAM, entity_id="research_team")
    assert reloaded.to_dict() == document.to_dict()


@pytest.mark.level0
def test_normalize_skill_names_drops_blank_and_non_string_entries():
    """Hand-edited or RPC-supplied dirt cannot reach the document."""
    assert normalize_skill_names(["beta", " alpha ", "", "  ", None, 7, "beta"]) == ["alpha", "beta"]
    assert normalize_skill_names(None) == []


@pytest.mark.level0
def test_set_replaces_both_lists_and_keeps_provenance(tmp_path: Path):
    """The full-set authorization semantics replace, not merge."""
    path = tmp_path / "skills-visibility.json"
    bootstrap_skill_visibility(
        path,
        scope=SCOPE_MEMBER,
        entity_id="reviewer",
        allow=["alpha"],
        bootstrapped_from="config:agents.teammate.skills",
    )

    result = set_skill_visibility(
        path,
        scope=SCOPE_MEMBER,
        entity_id="reviewer",
        allow=["gamma"],
        deny=["delta"],
    )

    assert result.allow == ["gamma"]
    assert result.deny == ["delta"]
    assert result.bootstrapped_from == "config:agents.teammate.skills"


@pytest.mark.level1
def test_set_with_empty_allow_restores_inheritance(tmp_path: Path):
    """Clearing the allow-list returns the agent to the whole library."""
    path = tmp_path / "skills-visibility.json"
    set_skill_visibility(path, scope=SCOPE_MEMBER, entity_id="reviewer", allow=["alpha"], deny=None)

    result = set_skill_visibility(path, scope=SCOPE_MEMBER, entity_id="reviewer", allow=None, deny=None)

    assert result.is_unrestricted is True


@pytest.mark.level0
def test_update_applies_deltas_and_lets_remove_win(tmp_path: Path):
    """Incremental grant/revoke; a name in both add and remove is removed."""
    path = tmp_path / "skills-visibility.json"
    set_skill_visibility(
        path,
        scope=SCOPE_MEMBER,
        entity_id="reviewer",
        allow=["alpha", "beta"],
        deny=["gamma"],
    )

    result = update_skill_visibility(
        path,
        scope=SCOPE_MEMBER,
        entity_id="reviewer",
        add_allow=["delta", "beta"],
        remove_allow=["beta"],
        add_deny=["epsilon"],
        remove_deny=["gamma"],
    )

    assert result.allow == ["alpha", "delta"]
    assert result.deny == ["epsilon"]


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------


@pytest.mark.level0
def test_file_provider_composes_member_and_team_documents(tmp_path: Path):
    """The provider is the only place assembly needs to wire visibility."""
    member_path = tmp_path / "member" / "skills-visibility.json"
    team_path = tmp_path / "team" / "skills-visibility.json"
    set_skill_visibility(member_path, scope=SCOPE_MEMBER, entity_id="reviewer", allow=["alpha"], deny=["zeta"])
    set_skill_visibility(team_path, scope=SCOPE_TEAM, entity_id="research_team", allow=["beta"], deny=["gamma"])

    provider = build_skill_visibility_provider(
        member_path=member_path,
        member_id="reviewer",
        team_path=team_path,
        team_id="research_team",
        global_disabled_loader=lambda: ["kill-switched"],
    )
    enabled, disabled = provider()

    assert isinstance(provider, FileSkillVisibilityProvider)
    assert isinstance(provider, SkillVisibilityProvider)
    assert enabled == {"alpha", "beta"}
    assert disabled == {"zeta", "gamma", "kill-switched"}


@pytest.mark.level0
def test_file_provider_sees_a_revocation_written_later(tmp_path: Path):
    """An authorization RPC lands without rebuilding the provider."""
    member_path = tmp_path / "member" / "skills-visibility.json"
    provider = build_skill_visibility_provider(member_path=member_path, member_id="reviewer")
    assert provider() == (set(), set())

    update_skill_visibility(member_path, scope=SCOPE_MEMBER, entity_id="reviewer", add_deny=["alpha"])

    assert provider() == (set(), {"alpha"})


@pytest.mark.level1
def test_file_provider_returns_mutable_copies(tmp_path: Path):
    """Mutating the returned sets must not poison the memoized value."""
    member_path = tmp_path / "member" / "skills-visibility.json"
    set_skill_visibility(member_path, scope=SCOPE_MEMBER, entity_id="reviewer", allow=["alpha"], deny=None)
    provider = build_skill_visibility_provider(member_path=member_path, member_id="reviewer")

    enabled, disabled = provider()
    enabled.add("smuggled")
    disabled.add("smuggled")

    assert provider() == ({"alpha"}, set())


@pytest.mark.level1
def test_file_provider_survives_a_failing_global_loader(tmp_path: Path):
    """A broken kill-switch loader must not strand the agent."""
    member_path = tmp_path / "member" / "skills-visibility.json"
    set_skill_visibility(member_path, scope=SCOPE_MEMBER, entity_id="reviewer", allow=["alpha"], deny=None)

    def _boom() -> list[str]:
        raise RuntimeError("state file unreadable")

    provider = build_skill_visibility_provider(
        member_path=member_path,
        member_id="reviewer",
        global_disabled_loader=_boom,
    )

    assert provider() == ({"alpha"}, set())


@pytest.mark.level1
def test_file_provider_signature_covers_every_backing_file(tmp_path: Path):
    """The signature carries one entry per declaration file, missing ones too."""
    member_path = tmp_path / "member" / "skills-visibility.json"
    team_path = tmp_path / "team" / "skills-visibility.json"
    set_skill_visibility(member_path, scope=SCOPE_MEMBER, entity_id="reviewer", allow=None, deny=None)
    provider = build_skill_visibility_provider(
        member_path=member_path,
        member_id="reviewer",
        team_path=team_path,
        team_id="research_team",
    )

    signature = provider.metadata_signature()

    assert [path for path, _ in signature] == [str(member_path), str(team_path)]
    assert signature[0][1] > 0
    assert signature[1][1] == -1.0
    assert provider.member_path == member_path
    assert provider.team_path == team_path


@pytest.mark.level0
def test_static_provider_reports_an_empty_signature():
    """No file backs a static provider, so it contributes no mtimes."""
    provider = StaticSkillVisibilityProvider(enabled=["alpha"], disabled=["beta"])

    assert provider() == ({"alpha"}, {"beta"})
    assert provider.metadata_signature() == ()
    assert isinstance(provider, SkillVisibilityProvider)


# ---------------------------------------------------------------------------
# Bootstrap authority: the outcome must not depend on the call order
# ---------------------------------------------------------------------------


@pytest.mark.level0
def test_a_higher_authority_seed_replaces_a_default_seed(tmp_path: Path):
    """A migration-derived allow-list wins over a config seed that landed first."""
    path = tmp_path / "skills-visibility.json"
    bootstrap_skill_visibility(
        path,
        scope=SCOPE_MEMBER,
        entity_id="reviewer",
        allow=["alpha", "beta"],
        bootstrapped_from="config:agents.teammate.skills",
    )

    reseeded = bootstrap_skill_visibility(
        path,
        scope=SCOPE_MEMBER,
        entity_id="reviewer",
        allow=["alpha"],
        bootstrapped_from="migration:symlinks",
        authority=AUTHORITY_MIGRATION,
    )

    assert reseeded.allow == ["alpha"]
    assert reseeded.authority == AUTHORITY_MIGRATION
    assert read_skill_visibility(path, scope=SCOPE_MEMBER, entity_id="reviewer").allow == ["alpha"]
    test_logger.info("reseeded %s -> %s", path, reseeded.allow)


@pytest.mark.level0
def test_a_default_seed_never_replaces_a_higher_authority_document(tmp_path: Path):
    """The reverse order yields the same document: seeding is order-independent."""
    path = tmp_path / "skills-visibility.json"
    bootstrap_skill_visibility(
        path,
        scope=SCOPE_MEMBER,
        entity_id="reviewer",
        allow=["alpha"],
        bootstrapped_from="migration:symlinks",
        authority=AUTHORITY_MIGRATION,
    )

    kept = bootstrap_skill_visibility(
        path,
        scope=SCOPE_MEMBER,
        entity_id="reviewer",
        allow=["alpha", "beta"],
        bootstrapped_from="config:agents.teammate.skills",
    )

    assert kept.allow == ["alpha"]
    assert kept.authority == AUTHORITY_MIGRATION


@pytest.mark.level0
def test_an_explicit_authorization_outranks_every_seed(tmp_path: Path):
    """An operator decision is sealed against config and migration seeds alike."""
    path = tmp_path / "skills-visibility.json"
    granted = set_skill_visibility(
        path,
        scope=SCOPE_MEMBER,
        entity_id="reviewer",
        allow=["gamma"],
        deny=["alpha"],
    )

    kept = bootstrap_skill_visibility(
        path,
        scope=SCOPE_MEMBER,
        entity_id="reviewer",
        allow=["alpha", "beta"],
        bootstrapped_from="migration:symlinks",
        authority=AUTHORITY_MIGRATION,
    )

    assert granted.authority == AUTHORITY_EXPLICIT
    assert kept.allow == ["gamma"]
    assert kept.deny == ["alpha"]


@pytest.mark.level1
def test_reseeding_preserves_the_stored_deny_list(tmp_path: Path):
    """Replacing an allow-list must never drop a revocation."""
    path = tmp_path / "skills-visibility.json"
    write_skill_visibility(
        path,
        SkillVisibility(
            scope=SCOPE_MEMBER,
            id="reviewer",
            bootstrapped_from="config:agents.teammate.skills",
            authority=AUTHORITY_SEED,
            allow=["alpha", "beta"],
            deny=["beta"],
        ),
    )

    reseeded = bootstrap_skill_visibility(
        path,
        scope=SCOPE_MEMBER,
        entity_id="reviewer",
        allow=["alpha"],
        bootstrapped_from="migration:symlinks",
        authority=AUTHORITY_MIGRATION,
    )

    assert reseeded.allow == ["alpha"]
    assert reseeded.deny == ["beta"]


@pytest.mark.level1
def test_a_pre_authority_document_is_classified_by_its_provenance(tmp_path: Path):
    """Upgrading must not make an existing authorization replaceable."""
    seeded = tmp_path / "seeded.json"
    seeded.write_text(
        json.dumps(
            {
                "version": 1,
                "scope": SCOPE_MEMBER,
                "id": "reviewer",
                "bootstrapped_from": "config:agents.teammate.skills",
                "allow": ["alpha"],
                "deny": [],
            }
        ),
        encoding="utf-8",
    )
    authorized = tmp_path / "authorized.json"
    authorized.write_text(
        json.dumps(
            {
                "version": 1,
                "scope": SCOPE_MEMBER,
                "id": "reviewer",
                "bootstrapped_from": None,
                "allow": ["alpha"],
                "deny": [],
            }
        ),
        encoding="utf-8",
    )

    parsed_seed = read_skill_visibility(seeded, scope=SCOPE_MEMBER, entity_id="reviewer")
    parsed_authorization = read_skill_visibility(authorized, scope=SCOPE_MEMBER, entity_id="reviewer")

    assert parsed_seed.authority == AUTHORITY_SEED
    assert parsed_authorization.authority == AUTHORITY_EXPLICIT

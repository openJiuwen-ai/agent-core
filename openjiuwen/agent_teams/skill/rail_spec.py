# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Declarative assembly of the team Skill rail.

Two call sites mount the team Skill rail on a member: the team's
``AgentConfigurator`` (leader / teammate / human agent) and
``TeamWorkerBackend`` (single-shot swarmflow workers). Both need the same
decision — "should this member get the rail, and with which static params" —
so it lives here rather than being written twice and drifting.

An embedder that assembles members from its own declarative catalogue (the
jiuwenswarm platform does) cannot fill the identity half of those params: the
member name is minted per spawn, long after the blueprint is written. Such a
blueprint declares a bare ``core.team.skill_use`` rail carrying only its
exposure preferences and :func:`complete_declared_team_skill_rails` fills the
identity in once the member is known. Without that, a declared Skill rail would
suppress the auto-declared one and the member would read the whole library
unfiltered.

The params are deliberately serializable only: the member and team declaration
paths travel as strings, so a member rebuilt from a serialized seed in another
process reconstructs its own visibility provider locally instead of depending on
a live handle that cannot cross that boundary.
"""

from __future__ import annotations

from pathlib import Path

from openjiuwen.agent_teams.paths import (
    SKILL_VISIBILITY_FILENAME,
    global_skills_dir,
    member_skill_visibility_path,
    team_skill_visibility_path,
)
from openjiuwen.agent_teams.schema.deep_agent_spec import RailSpec

# Params that identify *whose* view the rail resolves. They are the ones a
# blueprint cannot know and the ones completion fills in; everything else on a
# declared rail (exposure mode, tool inclusion) belongs to the declarer.
_IDENTITY_PARAM_NAMES: tuple[str, ...] = (
    "team_name",
    "skills_dir",
    "member_visibility_path",
    "team_visibility_path",
    "bootstrap_allow",
)


def _member_declaration_path(
    team_name: str,
    member_name: str,
    member_workspace_path: str | None,
) -> Path:
    """Resolve where the member's Skill visibility declaration lives.

    The member's real workspace root wins over the standard team layout: a
    runtime that cannot create the workspace symlink runs the member from an
    independent workspace, and the declaration has to sit in the directory the
    member actually owns rather than in an empty layout slot beside it.

    Args:
        team_name: Resolved team name.
        member_name: Member name.
        member_workspace_path: The member's resolved workspace root, or None.

    Returns:
        Absolute path of the member declaration file.
    """
    if member_workspace_path:
        return Path(member_workspace_path) / SKILL_VISIBILITY_FILENAME
    return member_skill_visibility_path(team_name, member_name)


def _team_declaration_path(team_name: str, team_workspace_path: str | None) -> Path:
    """Resolve where the team-wide Skill visibility declaration lives.

    Args:
        team_name: Resolved team name.
        team_workspace_path: The shared team workspace root, or None for the
            standard layout.

    Returns:
        Absolute path of the team declaration file.
    """
    if team_workspace_path:
        return Path(team_workspace_path) / SKILL_VISIBILITY_FILENAME
    return team_skill_visibility_path(team_name)


def _identity_params(
    *,
    team_name: str,
    member_name: str,
    config_skills: list[str] | None,
    team_workspace_path: str | None,
    member_workspace_path: str | None,
) -> dict[str, object]:
    """Build the identity half of the team Skill rail params.

    Args:
        team_name: Resolved team name.
        member_name: Member name; also the declaration's entity id.
        config_skills: ``agents.<role>.skills`` from the blueprint, or None.
        team_workspace_path: Shared team workspace root, or None.
        member_workspace_path: The member's resolved workspace root, or None.

    Returns:
        A mapping of the identity params.
    """
    return {
        "team_name": team_name,
        "skills_dir": [str(global_skills_dir())],
        "member_visibility_path": str(
            _member_declaration_path(team_name, member_name, member_workspace_path),
        ),
        "team_visibility_path": str(_team_declaration_path(team_name, team_workspace_path)),
        "bootstrap_allow": list(config_skills or []),
    }


def build_team_skill_rail_spec(
    *,
    team_name: str,
    member_name: str,
    config_skills: list[str] | None,
    declared_rails: list[RailSpec],
    team_workspace_path: str | None = None,
    member_workspace_path: str | None = None,
) -> RailSpec | None:
    """Declare the team Skill rail for one member.

    Skills live in exactly one physical library; the member's own
    ``skills-visibility.json`` plus the team-wide one decide which of them it
    sees. ``agents.<role>.skills`` is carried into the rail params as the seed
    allow-list only — the declaration file, once written, is the authority.

    Nothing is declared when the member has no stable identity (an unnamed
    member owns no workspace to key a declaration on) or when the blueprint
    already declares a Skill rail of its own, whose params must be respected.
    A declared ``core.team.skill_use`` is expected to have been completed by
    :func:`complete_declared_team_skill_rails` first.

    Args:
        team_name: Resolved team name.
        member_name: Member name; also the declaration's entity id.
        config_skills: ``agents.<role>.skills`` from the blueprint, or None.
        declared_rails: The member's already-declared rails, inspected for an
            existing Skill rail and for the system-operation rail that owns the
            read_file / bash tools.
        team_workspace_path: Root of the shared team workspace when the team has
            one. A team may point it at a custom directory, and the team
            declaration has to be read from where the workspace manager seeded
            it; None falls back to the standard team workspace layout.
        member_workspace_path: The member's resolved workspace root. None falls
            back to the standard team member workspace layout.

    Returns:
        A ``core.team.skill_use`` RailSpec, or None to mount no Skill rail.
    """
    if not member_name:
        return None

    from openjiuwen.agent_teams.rails.builtin_elements import SKILL_USE, SYS_OPERATION
    from openjiuwen.agent_teams.rails.elements import TEAM_SKILL_USE

    declared_types = {getattr(rail, "type", None) for rail in declared_rails}
    if SKILL_USE in declared_types or TEAM_SKILL_USE in declared_types:
        return None

    params = _identity_params(
        team_name=team_name,
        member_name=member_name,
        config_skills=config_skills,
        team_workspace_path=team_workspace_path,
        member_workspace_path=member_workspace_path,
    )
    params["skill_mode"] = "all"
    # ``include_tools`` reproduces what the DeepAgent factory's auto-add would
    # have chosen: the Skill rail only registers its read_file / bash fallback
    # when no system-operation rail already owns them, otherwise every build
    # logs a duplicate-ability warning per tool.
    params["include_tools"] = SYS_OPERATION not in declared_types
    return RailSpec(type=TEAM_SKILL_USE, params=params)


def complete_declared_team_skill_rails(
    declared_rails: list[RailSpec],
    *,
    team_name: str,
    member_name: str,
    config_skills: list[str] | None,
    team_workspace_path: str | None = None,
    member_workspace_path: str | None = None,
) -> list[RailSpec]:
    """Fill member identity into every declared ``core.team.skill_use`` rail.

    A blueprint declares the rail to own its exposure preferences (``skill_mode``
    above all) but has no member identity to point it at, so it leaves the
    declaration paths out. This resolves them the same way
    :func:`build_team_skill_rail_spec` does, leaving any param the declarer did
    set untouched — including ``include_tools``, which defaults to the same
    "only when no system-operation rail owns the tools" rule.

    Args:
        declared_rails: The member's declared rails.
        team_name: Resolved team name.
        member_name: Member name; also the declaration's entity id.
        config_skills: ``agents.<role>.skills`` from the blueprint, or None.
        team_workspace_path: Shared team workspace root, or None.
        member_workspace_path: The member's resolved workspace root, or None.

    Returns:
        The rail list, with declared team Skill rails replaced by completed
        copies. The input list is never mutated; it is returned unchanged when
        there is nothing to complete.
    """
    from openjiuwen.agent_teams.rails.builtin_elements import SYS_OPERATION
    from openjiuwen.agent_teams.rails.elements import TEAM_SKILL_USE

    if not member_name:
        return declared_rails
    if not any(getattr(rail, "type", None) == TEAM_SKILL_USE for rail in declared_rails):
        return declared_rails

    declared_types = {getattr(rail, "type", None) for rail in declared_rails}
    identity = _identity_params(
        team_name=team_name,
        member_name=member_name,
        config_skills=config_skills,
        team_workspace_path=team_workspace_path,
        member_workspace_path=member_workspace_path,
    )
    completed: list[RailSpec] = []
    for rail in declared_rails:
        if getattr(rail, "type", None) != TEAM_SKILL_USE:
            completed.append(rail)
            continue
        params = dict(rail.params or {})
        for name in _IDENTITY_PARAM_NAMES:
            if not params.get(name):
                params[name] = identity[name]
        if "include_tools" not in params:
            params["include_tools"] = SYS_OPERATION not in declared_types
        completed.append(rail.model_copy(update={"params": params}))
    return completed


__all__ = [
    "build_team_skill_rail_spec",
    "complete_declared_team_skill_rails",
]

# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Team-scoped Skill rail: one physical library, per-member visibility.

Skills exist exactly once on disk, in the library returned by
``openjiuwen.agent_teams.paths.global_skills_dir``. A team member owns no Skill
directory, no symlink view and no sandbox copy of it; what it may see is
declared in ``skills-visibility.json`` at its own workspace root, composed with
the team-wide document (see ``openjiuwen.agent_teams.skill.visibility``).

:class:`TeamSkillUseRail` is the team's own subclass of the shared
``SkillUseRail``. It changes exactly two things and inherits everything else:

- the allow / deny sets are recomputed from the visibility declarations on every
  filter pass and on every snapshot-signature build, so a grant written by
  another process lands on the member's next turn without rebuilding the agent;
- the snapshot signature carries the composed decision itself, so a change of
  grants forces a prompt rebuild even when nothing under the library moved.

Empty allow keeps its inherited meaning: "do not filter", i.e. inherit the whole
library. Deny always wins over allow.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openjiuwen.harness.skills.library_state import collect_disabled_skills
from openjiuwen.agent_teams.skill.visibility import (
    SCOPE_MEMBER,
    SkillVisibilityProvider,
    bootstrap_skill_visibility,
    build_skill_visibility_provider,
)
from openjiuwen.core.common.logging import team_logger
from openjiuwen.core.single_agent.skills.skill_manager import Skill
from openjiuwen.harness.rails.skills.skill_use_rail import SkillUseRail

# Provenance recorded in a member declaration seeded from the team blueprint.
# Decisions read the document's ``authority`` field, never this string.
MEMBER_BOOTSTRAP_SOURCE = "config:agents.skills"

# Filler timestamp for the synthetic allow / deny entries appended to the
# snapshot signature. The signature is a tuple of ``(token, mtime)`` pairs used
# only for equality comparison, and a grant carries no mtime of its own.
_VISIBILITY_SIGNATURE_MTIME = 0.0


class TeamSkillUseRail(SkillUseRail):
    """SkillUseRail narrowed by the member's and the team's visibility documents.

    The rail always scans the one shared Skill library. Which of the Skills it
    finds there reach the model is decided by a
    :class:`~openjiuwen.agent_teams.skill.visibility.SkillVisibilityProvider`,
    re-read on every refresh rather than frozen at construction.
    """

    def __init__(
        self,
        *,
        skills_dir: str | list[str],
        visibility_provider: SkillVisibilityProvider,
        skill_mode: str = "all",
        include_tools: bool = True,
    ) -> None:
        """Initialize the team Skill rail.

        Args:
            skills_dir: The shared Skill library root (or roots).
            visibility_provider: Recomputes ``(enabled, disabled)`` from the
                member and team visibility declarations.
            skill_mode: ``all`` (list every visible Skill in the system prompt)
                or ``auto_list``; same meaning as on the base rail.
            include_tools: Whether the rail registers its own read_file / bash
                fallback tools. Off when a system-operation rail already owns
                them, otherwise every build logs duplicate-ability warnings.
        """
        super().__init__(
            skills_dir=skills_dir,
            skill_mode=skill_mode,
            include_tools=include_tools,
        )
        self.visibility_provider = visibility_provider
        # Seed the sets before the first refresh so an early call to
        # ``get_skills_for_session`` already sees the member's real view.
        self._apply_visibility()

    def _apply_visibility(self) -> None:
        """Recompute the allow / deny sets from the visibility provider.

        A failing provider keeps the last known-good sets: falling back to "no
        filter" would leak Skills across members, and falling back to "deny
        everything" would strand the member.
        """
        try:
            enabled, disabled = self.visibility_provider()
        except Exception as exc:  # noqa: BLE001 - a broken declaration must not stop the member
            team_logger.warning(
                "Skill visibility provider failed ({}); keeping the previous allow/deny sets",
                exc,
            )
            return
        self.enabled_skills = set(enabled)
        self.disabled_skills = set(disabled)

    def _is_skill_name_visible(self, skill_name: str) -> bool:
        """Return whether one Skill name survives the current allow / deny sets.

        Args:
            skill_name: Skill name, which is also its directory name.

        Returns:
            True when the member may see the Skill. An empty allow set means
            "no allow filtering"; deny always wins.
        """
        if self.enabled_skills and skill_name not in self.enabled_skills:
            return False
        return skill_name not in self.disabled_skills

    def _filter_skills(self, skills: list[Skill]) -> list[Skill]:
        """Apply the freshly composed visibility before the inherited filter."""
        self._apply_visibility()
        return super()._filter_skills(skills)

    def _build_skills_snapshot_signature(self) -> tuple[tuple[str, float], ...]:
        """Extend the library snapshot with the composed visibility decision.

        The base signature only tracks Skill directories and SKILL.md mtimes, so
        a grant written into a visibility document would move nothing and the
        member would keep its stale prompt. Appending the composed sets makes
        the decision itself part of the signature — authoritative, and immune to
        the coarse timestamp granularity that makes an mtime proxy unreliable.
        """
        self._apply_visibility()
        entries = list(super()._build_skills_snapshot_signature())
        entries.extend(
            (f"skill-visibility.allow:{name}", _VISIBILITY_SIGNATURE_MTIME)
            for name in sorted(self.enabled_skills)
        )
        entries.extend(
            (f"skill-visibility.deny:{name}", _VISIBILITY_SIGNATURE_MTIME)
            for name in sorted(self.disabled_skills)
        )
        return tuple(entries)

    def get_skills_for_session(self, session: Any = None) -> list[Skill]:
        """Return the session's Skill view, re-checked against current grants.

        This is the path the ``skill`` and ``list_skill`` tools resolve a name
        through, and the session baseline is persisted state: without this
        re-check a revoked Skill would stay invokable until the session ended.
        The system prompt keeps quoting the unfiltered baseline for cache
        stability; the runtime attachment already announces the removal.

        Args:
            session: The session whose baseline is being resolved, or None.

        Returns:
            The visible subset of the merged baseline / current Skill view.
        """
        merged = super().get_skills_for_session(session)
        return [skill for skill in merged if self._is_skill_name_visible(skill.name)]


def global_disabled_skills(skills_dir: list[str]) -> list[str]:
    """Return the Skill names switched off globally in the shared library.

    The library-wide on/off switch lives in ``skills_state.json`` next to the
    Skills themselves and is written by the marketplace / install flow, not by
    any team. It is read through the team package's own reader
    (:func:`openjiuwen.harness.skills.library_state.collect_disabled_skills`)
    rather than through another layer's private helper, so a refactor there
    cannot break the team rail silently.

    Args:
        skills_dir: The Skill library roots to inspect.

    Returns:
        Sorted Skill names whose stored config says ``enabled: false``.
    """
    return collect_disabled_skills(list(skills_dir))


def create_team_skill_use_rail(
    *,
    member_name: str,
    team_name: str,
    member_visibility_path: str | Path,
    team_visibility_path: str | Path | None,
    skills_dir: list[str],
    bootstrap_allow: list[str],
    skill_mode: str = "all",
    include_tools: bool = False,
) -> TeamSkillUseRail:
    """Seed the member declaration and build the rail over the shared library.

    This is the *only* writer of a member's seed declaration. Every assembly
    path — the team ``AgentConfigurator``, the swarmflow worker backend and an
    embedder that declares ``core.team.skill_use`` in its own catalogue —
    reaches the member's Skill rail through here, so the seed is written once
    per build, by the component that also consumes the document. An embedder
    must not seed the same file from a second provider of its own: the two
    writers would contend for the same lock and their skip rules would drift.

    Seeding is file-authoritative and idempotent: ``agents.<role>.skills`` only
    supplies the initial allow-list, and an existing declaration is returned
    untouched, so a later config edit cannot roll back a grant already in
    effect. An empty allow-list seeds an unrestricted document, which is what
    lets a member with no configured Skills pick up newly installed ones without
    any metadata change.

    A seeding failure is logged and swallowed: a missing declaration reads back
    as "no restriction", which beats refusing to start the member.

    Args:
        member_name: Member identity; also the declaration's entity id.
        team_name: Team identity; the team declaration's entity id.
        member_visibility_path: Declaration path at the member workspace root.
        team_visibility_path: Declaration path at the team workspace root, or
            None when the team has no shared workspace.
        skills_dir: The shared Skill library roots.
        bootstrap_allow: Seed allow-list from the blueprint.
        skill_mode: ``all`` or ``auto_list``.
        include_tools: Whether the rail registers its read_file / bash fallback.

    Returns:
        A configured :class:`TeamSkillUseRail`.
    """
    try:
        bootstrap_skill_visibility(
            member_visibility_path,
            scope=SCOPE_MEMBER,
            entity_id=member_name,
            allow=bootstrap_allow,
            bootstrapped_from=MEMBER_BOOTSTRAP_SOURCE,
        )
    except OSError as exc:
        team_logger.warning(
            "Failed to seed Skill visibility declaration for member {} at {}: {}",
            member_name,
            member_visibility_path,
            exc,
        )

    provider = build_skill_visibility_provider(
        member_path=member_visibility_path,
        member_id=member_name,
        team_path=team_visibility_path,
        team_id=team_name,
        global_disabled_loader=lambda: global_disabled_skills(skills_dir),
    )
    return TeamSkillUseRail(
        skills_dir=skills_dir,
        visibility_provider=provider,
        skill_mode=skill_mode,
        include_tools=include_tools,
    )


__all__ = [
    "MEMBER_BOOTSTRAP_SOURCE",
    "TeamSkillUseRail",
    "create_team_skill_use_rail",
    "global_disabled_skills",
]

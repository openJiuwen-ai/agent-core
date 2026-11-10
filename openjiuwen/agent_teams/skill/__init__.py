# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Team-scoped Skill surface: visibility declarations + external-agent CLI.

``visibility.py`` owns the team side of the single-source Skill library: Skill
entities live in exactly one directory (``paths.global_skills_dir()``) and each
member / team workspace only carries a ``skills-visibility.json`` declaring
what it may see. ``file_lock.py`` is the cross-process lock every writer of
that declaration takes. The library-wide on/off
switch (``skills_state.json``) that a composition folds into its disabled set.

``cli.py`` is a non-interactive command-line wrapper over
``ExternalTeamClient`` that branches on the join descriptor's ``scope``: a
``member`` (third-party CLI team member) drives the real teammate team tools
(view_task / claim_task / send_message) + inbox, while an ``operator`` (a
non-member, external team controller) gets the broad control surface. The two
scenarios have separate skill docs — ``SKILL_member.md`` and
``SKILL_operator.md`` — each with its own coordination protocol and command
reference.
"""

from openjiuwen.agent_teams.skill.file_lock import (
    DEFAULT_LOCK_TIMEOUT_SECONDS,
    FileLockTimeout,
    cross_process_file_lock,
    lock_path_for,
)
from openjiuwen.harness.skills.library_state import (
    SKILLS_STATE_FILENAME,
    collect_disabled_skills,
)
from openjiuwen.agent_teams.skill.visibility import (
    AUTHORITY_EXPLICIT,
    AUTHORITY_MIGRATION,
    AUTHORITY_SEED,
    MISSING_METADATA_MTIME,
    SCOPE_MEMBER,
    SCOPE_TEAM,
    SKILL_VISIBILITY_SCHEMA_VERSION,
    FileSkillVisibilityProvider,
    SkillVisibility,
    SkillVisibilityProvider,
    StaticSkillVisibilityProvider,
    StatToken,
    bootstrap_skill_visibility,
    build_skill_visibility_provider,
    compose_skill_visibility,
    normalize_skill_names,
    read_skill_visibility,
    set_skill_visibility,
    update_skill_visibility,
    write_skill_visibility,
)

__all__ = [
    "AUTHORITY_EXPLICIT",
    "AUTHORITY_MIGRATION",
    "AUTHORITY_SEED",
    "DEFAULT_LOCK_TIMEOUT_SECONDS",
    "MISSING_METADATA_MTIME",
    "SCOPE_MEMBER",
    "SCOPE_TEAM",
    "SKILLS_STATE_FILENAME",
    "SKILL_VISIBILITY_SCHEMA_VERSION",
    "FileLockTimeout",
    "FileSkillVisibilityProvider",
    "SkillVisibility",
    "SkillVisibilityProvider",
    "StatToken",
    "StaticSkillVisibilityProvider",
    "bootstrap_skill_visibility",
    "build_skill_visibility_provider",
    "collect_disabled_skills",
    "compose_skill_visibility",
    "cross_process_file_lock",
    "lock_path_for",
    "normalize_skill_names",
    "read_skill_visibility",
    "set_skill_visibility",
    "update_skill_visibility",
    "write_skill_visibility",
]

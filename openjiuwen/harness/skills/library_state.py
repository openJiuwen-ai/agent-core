# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Reader for the Skill library's on/off state file.

A Skill library keeps a library-wide kill switch in ``skills_state.json``,
written next to the Skills themselves by the install / marketplace flow::

    {
      "skill_configs": {
        "gamma": {"enabled": false}
      }
    }

A Skill switched off there is unavailable to *every* agent that reads the
library, whatever any other configuration says. Both the single-agent rail
assembly and the team Skill rail fold those names into the Skill rail's
``disabled_skills``, so the reader lives here rather than in either caller: one
parser means a format change lands in one place.

The format is read defensively on purpose. A missing, unreadable or malformed
state file means "nothing is switched off", because a corrupted file must not
silently blank out an agent's whole Skill view.
"""

from __future__ import annotations

import json
from pathlib import Path

from openjiuwen.core.common.logging import logger

# Basename of the library-wide Skill on/off state file.
SKILLS_STATE_FILENAME = "skills_state.json"


def collect_disabled_skills(skills_dirs: list[str | Path]) -> list[str]:
    """Collect the Skill names switched off in the given library roots.

    Args:
        skills_dirs: Skill library roots to inspect. Roots that hold no state
            file are skipped.

    Returns:
        Sorted, de-duplicated Skill names whose stored config says
        ``enabled: false``.
    """
    disabled: set[str] = set()
    for skills_dir in skills_dirs:
        state_path = Path(skills_dir) / SKILLS_STATE_FILENAME
        if not state_path.is_file():
            continue
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            logger.warning(
                "[SkillLibraryState] failed to read '%s'; treating it as no Skill disabled",
                state_path,
            )
            continue
        if not isinstance(data, dict):
            continue
        skill_configs = data.get("skill_configs", {})
        if not isinstance(skill_configs, dict):
            continue
        for name, cfg in skill_configs.items():
            if isinstance(cfg, dict) and cfg.get("enabled") is False:
                disabled.add(str(name))
    return sorted(disabled)


__all__ = [
    "SKILLS_STATE_FILENAME",
    "collect_disabled_skills",
]

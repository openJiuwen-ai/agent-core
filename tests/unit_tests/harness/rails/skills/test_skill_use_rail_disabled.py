# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for SkillUseRail disabled-skill handling (issue #1499).

A disabled / not-enabled skill must never be scanned into the skill cache:
the incremental loader skips them before loading, so ``_skill_cache`` and
``skills`` stay free of filtered-out entries (and no wasted SKILL.md IO).
"""

import tempfile
from pathlib import Path

import pytest

from openjiuwen.harness.rails.skills.skill_use_rail import SkillUseRail


def _write_skill(root: Path, name: str) -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(f"# {name}\n\nA skill named {name}.\n", encoding="utf-8")


@pytest.fixture
def skills_root(tmp_path: Path) -> Path:
    _write_skill(tmp_path, "enabled_skill")
    _write_skill(tmp_path, "disabled_skill")
    _write_skill(tmp_path, "other_skill")
    return tmp_path


@pytest.mark.asyncio
async def test_disabled_skill_not_scanned_into_cache(skills_root: Path) -> None:
    rail = SkillUseRail(str(skills_root), disabled_skills=["disabled_skill"])
    await rail.reload_skills()
    assert {s.name for s in rail.skills} == {"enabled_skill", "other_skill"}
    # _skill_cache keys are resolved absolute paths, not bare skill names.
    assert str((skills_root / "disabled_skill").resolve()) not in rail._skill_cache


@pytest.mark.asyncio
async def test_enabled_allowlist_limits_loaded_skills(skills_root: Path) -> None:
    rail = SkillUseRail(str(skills_root), enabled_skills=["enabled_skill"])
    await rail.reload_skills()
    assert {s.name for s in rail.skills} == {"enabled_skill"}
    # Not-enabled skills must not even reach the cache (no wasted IO).
    assert set(rail._skill_cache.keys()) == {str((skills_root / "enabled_skill").resolve())}


@pytest.mark.asyncio
async def test_no_filters_loads_all_skills(skills_root: Path) -> None:
    rail = SkillUseRail(str(skills_root))
    await rail.reload_skills()
    assert {s.name for s in rail.skills} == {"enabled_skill", "disabled_skill", "other_skill"}
    assert len(rail._skill_cache) == 3

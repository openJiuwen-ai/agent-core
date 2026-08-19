# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for member-scoped Skill evolution I/O isolation."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from openjiuwen.harness.rails.evolution.member_skill_evolution_rail import (
    MemberSkillEvolutionRail,
)
from openjiuwen.harness.rails.evolution.member_skill_workspace import (
    ensure_member_skill_copy,
)


@pytest.mark.asyncio
async def test_private_skill_copy_runs_in_worker_thread(tmp_path: Path):
    rail = object.__new__(MemberSkillEvolutionRail)
    rail._member_skills_dir = str(tmp_path / "member")
    rail._global_skills_dir = str(tmp_path / "global")
    copied_path = tmp_path / "member" / "release-skill"

    with patch(
        "openjiuwen.harness.rails.evolution.member_skill_evolution_rail.asyncio.to_thread",
        new=AsyncMock(return_value=copied_path),
    ) as to_thread:
        result = await rail._ensure_private_skill("release-skill")

    assert result == copied_path
    to_thread.assert_awaited_once_with(
        ensure_member_skill_copy,
        member_skills_dir=str(tmp_path / "member"),
        global_skills_dir=str(tmp_path / "global"),
        skill_name="release-skill",
    )

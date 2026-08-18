# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Member-scoped Skill evolution with copy-on-write isolation."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from openjiuwen.harness.rails.evolution.member_skill_workspace import (
    ensure_member_skill_copy,
)
from openjiuwen.harness.rails.evolution.skill_evolution_rail import SkillEvolutionRail


class MemberSkillEvolutionRail(SkillEvolutionRail):
    """Regular Skill rail whose mutations are isolated to one member workspace."""

    def __init__(
        self,
        skills_dir,
        *,
        member_skills_dir: str | Path,
        global_skills_dir: str | Path,
        **kwargs: Any,
    ) -> None:
        super().__init__(skills_dir, **kwargs)
        self._member_skills_dir = str(member_skills_dir)
        self._global_skills_dir = str(global_skills_dir)

    async def _handle_evolution_from_signals(
        self,
        *,
        skill_name: str,
        **kwargs: Any,
    ) -> Any:
        """Detach the target before external-signal evolution can mutate it."""
        await self._ensure_private_skill(skill_name)
        return await super()._handle_evolution_from_signals(
            skill_name=skill_name,
            **kwargs,
        )

    async def _evolve_skill_with_sharing(
        self,
        *,
        skill_name: str,
        **kwargs: Any,
    ) -> bool:
        """Detach the target before automatic/shared evolution can mutate it."""
        await self._ensure_private_skill(skill_name)
        return await super()._evolve_skill_with_sharing(
            skill_name=skill_name,
            **kwargs,
        )

    async def _ensure_private_skill(self, skill_name: str) -> Path:
        return await asyncio.to_thread(
            ensure_member_skill_copy,
            member_skills_dir=self._member_skills_dir,
            global_skills_dir=self._global_skills_dir,
            skill_name=skill_name,
        )


__all__ = ["MemberSkillEvolutionRail"]

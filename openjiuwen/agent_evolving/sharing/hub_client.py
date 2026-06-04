# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""High-level helpers for searching and installing skills from the experience hub."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from openjiuwen.agent_evolving.checkpointing.evolution_store import EvolutionStore
from openjiuwen.agent_evolving.sharing.backends.base import SharingBackend
from openjiuwen.agent_evolving.sharing.experience_sharer import ExperienceSharer
from openjiuwen.agent_evolving.sharing.types import QueryKeywords, SkillSearchResult
from openjiuwen.core.common.logging import logger


class ExperienceHubClient:
    """Search hub skills by keywords and install packages locally."""

    def __init__(
        self,
        backend: SharingBackend,
        evolution_store: EvolutionStore,
    ) -> None:
        self._sharer = ExperienceSharer(backend=backend, local_cache_dir=None)
        self._store = evolution_store

    @property
    def sharer(self) -> ExperienceSharer:
        return self._sharer

    async def search_skills(
        self,
        query: QueryKeywords,
        *,
        top_k: int = 5,
    ) -> List[SkillSearchResult]:
        return await self._sharer.search_skills(query, top_k=top_k)

    async def install_skill(
        self,
        skill_id: str,
        *,
        skill_name: Optional[str] = None,
    ) -> Optional[Path]:
        """Download and install the hub skill package into the local skills directory."""
        resolved_id = (skill_id or "").strip()
        if not resolved_id:
            return None

        package_bytes = await self._sharer.download_skill_package(resolved_id)
        if not package_bytes:
            logger.warning("[ExperienceHubClient] no package found for skill_id=%s", resolved_id)
            return None

        meta = await self._sharer.get_skill_package_meta(resolved_id)
        target_name = (skill_name or (meta.skill_name if meta else "") or "").strip() or None
        installed = await self._store.install_skill_package(
            package_bytes,
            skill_name=target_name,
        )
        if installed is not None:
            logger.info(
                "[ExperienceHubClient] installed skill_id=%s to %s",
                resolved_id,
                installed,
            )
        return installed


__all__ = ["ExperienceHubClient"]

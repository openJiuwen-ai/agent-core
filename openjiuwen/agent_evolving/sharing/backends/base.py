# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Abstract sharing backend.

Hub partitions are keyed by ``skill_id`` (stable identity from SKILL.md
frontmatter).  Each skill keeps a single immutable package on the hub;
experience bundles append over time under the same ``skill_id``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from openjiuwen.agent_evolving.sharing.types import (
    QueryKeywords,
    SharedSkillBundle,
    SkillPackageMeta,
    SkillSearchResult,
    UploadResult,
)


class SharingBackend(ABC):
    """Sharing backend contract."""

    @abstractmethod
    async def upload_bundle(self, bundle: SharedSkillBundle) -> UploadResult:
        """Persist ``bundle`` when accepted and return the upload outcome."""

    @abstractmethod
    async def download_bundles(
        self,
        skill_id: str,
        query: QueryKeywords,
        top_k: int = 3,
    ) -> List[SharedSkillBundle]:
        """Return up to ``top_k`` bundles ranked by relevance to ``query``."""

    @abstractmethod
    async def has_skill_package(self, skill_id: str) -> bool:
        """Return True iff the hub already stores the initial skill package."""

    @abstractmethod
    async def upload_skill_package(
        self,
        skill_id: str,
        package_bytes: bytes,
        meta: SkillPackageMeta,
    ) -> None:
        """Persist the initial skill package under ``skill_id``.

        Implementations must treat re-upload as a no-op when a package
        already exists (remote keeps only the first version).
        """

    @abstractmethod
    async def download_skill_package(self, skill_id: str) -> Optional[bytes]:
        """Return the stored skill package bytes, or ``None`` if missing."""

    @abstractmethod
    async def get_skill_package_meta(self, skill_id: str) -> Optional[SkillPackageMeta]:
        """Return hub metadata for ``skill_id``."""

    @abstractmethod
    async def search_skills(
        self,
        query: QueryKeywords,
        top_k: int = 5,
    ) -> List[SkillSearchResult]:
        """Search skills on the hub by keyword relevance."""


__all__ = ["SharingBackend"]

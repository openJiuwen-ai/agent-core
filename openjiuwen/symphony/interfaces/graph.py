# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Capability graph mutation integration protocol."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from openjiuwen.symphony.orchestration.contracts import GraphMutationResult


@runtime_checkable
class SkillGraphUpdater(Protocol):
    """Narrow dependency used after an approved Skill asset change."""

    async def add_skills(
        self,
        changed_skill_ids: Sequence[str],
        *,
        request_id: str,
        source_revision: str,
    ) -> GraphMutationResult:
        """Publish one atomic batch of newly added Skills."""

        raise NotImplementedError

    async def update_skills(
        self,
        changed_skill_ids: Sequence[str],
        *,
        request_id: str,
        source_revision: str,
    ) -> GraphMutationResult:
        """Publish one atomic batch of updated Skills."""

        raise NotImplementedError

    async def delete_skills(
        self,
        changed_skill_ids: Sequence[str],
        *,
        request_id: str,
        source_revision: str,
    ) -> GraphMutationResult:
        """Publish one atomic batch of deleted Skills."""

        raise NotImplementedError

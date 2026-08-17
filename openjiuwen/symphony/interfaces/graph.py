# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Capability graph mutation integration protocol."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from openjiuwen.symphony.models import SourceSnapshot
    from openjiuwen.symphony.orchestration.contracts import (
        GraphMutationResult,
        SkillGraphAdd,
        SkillGraphDelete,
        SkillGraphUpdate,
    )


@runtime_checkable
class SkillGraphUpdater(Protocol):
    """Narrow dependency used after an approved Skill asset change."""

    async def add_skills(
        self,
        skills: Sequence[SkillGraphAdd],
        *,
        request_id: str,
        expected_graph_version: str,
        source_snapshot: SourceSnapshot,
    ) -> GraphMutationResult:
        """Publish one atomic batch of newly added Skills."""

        raise NotImplementedError

    async def update_skills(
        self,
        skills: Sequence[SkillGraphUpdate],
        *,
        request_id: str,
        expected_graph_version: str,
        source_snapshot: SourceSnapshot,
    ) -> GraphMutationResult:
        """Publish one atomic batch of updated Skills."""

        raise NotImplementedError

    async def delete_skills(
        self,
        skills: Sequence[SkillGraphDelete],
        *,
        request_id: str,
        expected_graph_version: str,
        source_snapshot: SourceSnapshot,
    ) -> GraphMutationResult:
        """Publish one atomic batch of deleted Skills."""

        raise NotImplementedError

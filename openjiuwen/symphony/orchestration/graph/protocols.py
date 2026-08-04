"""Shared protocols for capability graph construction and matching."""

from __future__ import annotations

from typing import Iterable, Protocol

from openjiuwen.symphony.orchestration.graph.models import LLMMatch, RelationCandidate, SkillRegistry


class OntologyMatcher(Protocol):
    """Match candidate capability relations against an ontology."""

    async def match(
        self,
        registry: SkillRegistry,
        candidates: Iterable[RelationCandidate],
    ) -> list[LLMMatch]:
        ...

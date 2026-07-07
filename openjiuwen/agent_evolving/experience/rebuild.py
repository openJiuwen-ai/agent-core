# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Rebuild lifecycle service for experience evolution."""

from __future__ import annotations

from typing import Any, Optional

from openjiuwen.agent_evolving.checkpointing.types import EvolutionRecord
from openjiuwen.agent_evolving.experience.archive import EvolutionArchivePair, EvolutionArchiveService
from openjiuwen.agent_evolving.experience.draft_schema import normalize_subject
from openjiuwen.core.common.logging import logger


class ExperienceRebuildService:
    """Prepare deterministic rebuild context without generating the rebuilt skill body."""

    def __init__(self, *, store: Any, archive_service: EvolutionArchiveService | None = None) -> None:
        self._store = store
        self._archive_service = archive_service or EvolutionArchiveService(store=store)

    async def prepare_rebuild_context(
        self,
        subject: dict[str, Any],
        *,
        user_intent: Optional[str] = None,
        min_score: float = 0.5,
        max_context_records: int = 40,
        max_context_chars: int = 20000,
    ) -> Optional[dict[str, Any]]:
        """Archive current state, filter rebuild inputs, and return structured context."""
        normalized_subject = normalize_subject(subject)
        skill_name = normalized_subject.name
        subject_kind = normalized_subject.kind
        if not self._store.skill_exists(skill_name, subject_kind=subject_kind):
            return None

        records_log = await self._store.load_full_evolution_log(skill_name, subject_kind=subject_kind)

        archive_pair: Optional[EvolutionArchivePair] = None
        archive_error: Optional[Exception] = None
        try:
            archive_pair = await self._archive_service.archive_current_pair(skill_name, subject_kind=subject_kind)
        except Exception as exc:
            archive_error = exc
            logger.warning("[ExperienceRebuildService] archive pair failed for '%s': %s", skill_name, exc)

        filtered_records = _filter_rebuild_records(records_log.entries, min_score=min_score)
        context = _build_rebuild_context_payload(
            filtered_records,
            max_records=max_context_records,
            max_chars=max_context_chars,
        )
        context.update(
            {
                "subject": normalized_subject.to_payload(),
                "skill_name": skill_name,
                "user_intent": user_intent,
                "min_score": min_score,
                "archive_path": archive_pair.evolution_archive_name if archive_pair else None,
                "archive_pair": archive_pair.to_payload() if archive_pair else None,
                "archive_version": archive_pair.version if archive_pair else None,
                "archive_error": archive_error,
            }
        )

        if archive_pair:
            await self._store.clear_evolutions(skill_name, subject_kind=subject_kind)
            self._archive_service.prune(skill_name, subject_kind=subject_kind)

        return context


def _filter_rebuild_records(records: list[EvolutionRecord], *, min_score: float) -> list[EvolutionRecord]:
    filtered: list[EvolutionRecord] = []
    for record in records:
        if getattr(record, "score", 0.0) < min_score:
            continue
        change = getattr(record, "change", None)
        if getattr(change, "skip_reason", None):
            continue
        filtered.append(record)
    return filtered


def _build_rebuild_context_payload(
    records: list[EvolutionRecord],
    *,
    max_records: int,
    max_chars: int,
) -> dict[str, Any]:
    sorted_records = sorted(
        records,
        key=lambda record: (record.score, getattr(record, "timestamp", "")),
        reverse=True,
    )
    included = sorted_records[:max_records]
    overflow = sorted_records[max_records:]
    items: list[dict[str, Any]] = []
    used_chars = 0

    for index, record in enumerate(included):
        content = getattr(record.change, "content", "")
        remaining_chars = max(max_chars - used_chars, 0)
        if remaining_chars == 0:
            overflow = included[index:] + overflow
            break
        clipped_content = content[:remaining_chars]
        used_chars += len(clipped_content)
        items.append(_to_rebuild_item(record, content=clipped_content))

    return {
        "records": items,
        "overflow_index": {"items": [_to_index_item(record) for record in overflow]},
    }


def _to_rebuild_item(record: EvolutionRecord, *, content: str) -> dict[str, Any]:
    item = _to_index_item(record)
    item["content"] = content
    return item


def _to_index_item(record: EvolutionRecord) -> dict[str, Any]:
    target = getattr(record.change.target, "value", record.change.target)
    return {
        "record_id": record.id,
        "summary": record.summary or "",
        "target": target,
        "section": record.change.section,
        "score": record.score,
        "updated_at": getattr(record, "timestamp", ""),
    }


__all__ = [
    "ExperienceRebuildService",
]

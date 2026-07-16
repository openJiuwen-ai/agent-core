# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Rebuild lifecycle service for experience evolution."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from openjiuwen.agent_evolving.checkpointing.types import EvolutionRecord
from openjiuwen.agent_evolving.experience.draft_schema import normalize_subject
from openjiuwen.core.common.logging import logger


class ExperienceRebuildService:
    """Prepare deterministic rebuild context without generating the rebuilt skill body."""

    def __init__(self, *, store: Any) -> None:
        self._store = store

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
        if not self._store.skill_exists(skill_name):
            return None

        skill_md_path, skills_base = _resolve_skill_paths_from_store(self._store, skill_name)

        evo_archive: Optional[str] = None
        archive_error: Optional[Exception] = None
        try:
            _, evo_archive = await self._store.archive_current_state(skill_name)
        except Exception as exc:
            archive_error = exc
            logger.warning("[ExperienceRebuildService] archive failed for '%s': %s", skill_name, exc)

        records_log = await self._store.load_evolution_log(skill_name)
        filtered_records = _filter_rebuild_records(records_log.entries, min_score=min_score)
        context = _build_rebuild_context_payload(
            filtered_records,
            max_records=max_context_records,
            max_chars=max_context_chars,
        )
        context_payload: dict[str, Any] = {
            "subject": normalized_subject.to_payload(),
            "skill_name": skill_name,
            "user_intent": user_intent,
            "min_score": min_score,
            "archive_path": evo_archive,
            "archive_error": archive_error,
        }
        if skill_md_path:
            context_payload["skill_md_path"] = skill_md_path
        if skills_base:
            context_payload["skills_base"] = skills_base
        context.update(context_payload)

        return context

    async def complete_rebuild(self, rebuild_context: dict[str, Any]) -> bool:
        """Bump SemVer from evolution entries, then clear live evolution log.

        Only runs when ``archive_path`` is present (evolutions were archived during prepare).
        Returns True when cleared, False when skipped.
        """
        archive_path = rebuild_context.get("archive_path")
        if not archive_path:
            return False
        skill_name = str(rebuild_context.get("skill_name") or "").strip()
        if not skill_name:
            return False
        new_version = await self._store.bump_version_for_rebuild(skill_name)
        await self._store.clear_evolutions(skill_name, retain_version=new_version)
        return True


def _resolve_skill_paths_from_store(store: Any, skill_name: str) -> tuple[Optional[str], Optional[str]]:
    """Resolve absolute skill_md_path and skills_base via store private helpers."""
    resolve_dir = getattr(store, "_resolve_skill_dir", None)
    if not callable(resolve_dir):
        return None, None
    skill_dir = resolve_dir(skill_name)
    if skill_dir is None:
        return None, None
    resolved_dir = skill_dir.resolve()
    skills_base = str(resolved_dir.parent)
    find_md = getattr(store, "_find_skill_md", None)
    md_path = find_md(skill_dir) if callable(find_md) else None
    if md_path is None:
        md_path = Path(skill_dir) / "SKILL.md"
    return str(md_path.resolve()), skills_base


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
        "summary": getattr(record, "summary", "") or "",
        "target": target,
        "section": record.change.section,
        "score": record.score,
        "updated_at": getattr(record, "timestamp", ""),
    }


__all__ = [
    "ExperienceRebuildService",
]

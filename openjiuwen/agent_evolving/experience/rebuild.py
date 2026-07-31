# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Rebuild lifecycle service for experience evolution."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, List, Optional, Sequence

from openjiuwen.agent_evolving.checkpointing.changelog import (
    classify_records_for_changelog,
)
from openjiuwen.agent_evolving.checkpointing.types import EvolutionRecord
from openjiuwen.agent_evolving.experience.draft_schema import normalize_subject
from openjiuwen.core.common.logging import logger


class ExperienceRebuildService:
    """Prepare deterministic rebuild context without generating the rebuilt skill body."""

    def __init__(
        self,
        *,
        store: Any,
        llm: Any = None,
        model: Optional[str] = None,
        language: str = "cn",
        classify_fn: Optional[Callable[[Sequence[EvolutionRecord]], Any]] = None,
    ) -> None:
        self._store = store
        self._llm = llm
        self._model = model
        self._language = language
        self._classify_fn = classify_fn

    def update_llm(self, llm: Any, model: str) -> None:
        """Refresh LLM client used for changelog classification."""
        self._llm = llm
        self._model = model

    async def prepare_rebuild_context(
        self,
        subject: dict[str, Any],
        *,
        user_intent: Optional[str] = None,
        min_score: float = 0.5,
        max_context_records: int = 40,
        max_context_chars: int = 20000,
        record_ids: Optional[Sequence[str]] = None,
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

        normalized_ids = _normalize_record_ids(record_ids)
        records_log = await self._store.load_evolution_log(skill_name)
        filtered_records = _filter_rebuild_records(
            records_log.entries,
            min_score=min_score,
            record_ids=normalized_ids,
        )
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
        if normalized_ids is not None:
            context_payload["record_ids"] = normalized_ids
        if skill_md_path:
            context_payload["skill_md_path"] = skill_md_path
        if skills_base:
            context_payload["skills_base"] = skills_base
        context.update(context_payload)

        return context

    async def complete_rebuild(self, rebuild_context: dict[str, Any]) -> bool:
        """Bump SemVer from evolution entries, write changelog, then clear live log.

        Archive during prepare is a safety step, not a gate for bump/clear.
        Skip only when archive explicitly failed (``archive_error``), so a
        prior partial failure that left the version already archived cannot
        permanently block rebuild.

        Entry selection for bump/changelog (distinct from prepare's context filter):
        - With ``record_ids``: only whitelisted IDs participate.
        - Without ``record_ids``: use the full live log (no ``min_score`` /
          ``skip_reason`` filtering), matching pre-whitelist behavior so that
          ``clear_evolutions`` does not drop records that never contributed to
          the version bump.

        Returns True when cleared, False when skipped.
        """
        if rebuild_context.get("archive_error") is not None:
            return False
        skill_name = str(rebuild_context.get("skill_name") or "").strip()
        if not skill_name:
            return False

        selected_entries = await self._select_entries_for_rebuild(rebuild_context)
        new_version = await self._store.bump_version_for_rebuild(
            skill_name,
            entries=selected_entries,
        )
        if new_version:
            await self._write_changelog_for_rebuild(
                skill_name,
                new_version,
                entries=selected_entries,
            )
        await self._store.clear_evolutions(skill_name, retain_version=new_version)
        return True

    async def _select_entries_for_rebuild(
        self,
        rebuild_context: dict[str, Any],
    ) -> list[EvolutionRecord]:
        """Select entries for version bump and changelog.

        Whitelist mode (``record_ids`` present) keeps only matching IDs.
        Otherwise return the full live log — do not apply prepare's
        ``min_score`` / ``skip_reason`` filters.
        """
        skill_name = str(rebuild_context.get("skill_name") or "").strip()
        record_ids = _normalize_record_ids(rebuild_context.get("record_ids"))
        evo_log = await self._store.load_evolution_log(skill_name)
        entries = list(getattr(evo_log, "entries", None) or [])
        if not record_ids:
            return entries
        return _filter_rebuild_records(
            entries,
            min_score=0.0,
            record_ids=record_ids,
        )

    async def _write_changelog_for_rebuild(
        self,
        skill_name: str,
        new_version: str,
        *,
        entries: Sequence[EvolutionRecord],
    ) -> None:
        """Classify selected evolution entries and append a version section to changelog.md."""
        append_changelog = getattr(self._store, "append_changelog_for_rebuild", None)
        if not callable(append_changelog):
            return
        try:
            classified = await classify_records_for_changelog(
                entries,
                llm=self._llm,
                model=self._model,
                language=self._language,
                classify_fn=self._classify_fn,
            )
            await append_changelog(skill_name, new_version, classified)
        except Exception as exc:
            logger.warning(
                "[ExperienceRebuildService] changelog update failed for '%s' v%s: %s",
                skill_name,
                new_version,
                exc,
            )


def _normalize_record_ids(record_ids: Optional[Sequence[str]]) -> Optional[List[str]]:
    """Deduplicate and strip IDs; empty result means no whitelist.

    A bare ``str`` / ``bytes`` is treated as a single ID (not iterated as
    characters), so accidental string inputs do not expand into a char list.
    """
    if not record_ids:
        return None
    if isinstance(record_ids, bytes):
        item = record_ids.decode("utf-8", errors="replace").strip()
        return [item] if item else None
    if isinstance(record_ids, str):
        item = record_ids.strip()
        return [item] if item else None
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in record_ids:
        item = str(raw or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        normalized.append(item)
    return normalized or None


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


def _filter_rebuild_records(
    records: list[EvolutionRecord],
    *,
    min_score: float,
    record_ids: Optional[Sequence[str]] = None,
) -> list[EvolutionRecord]:
    """Select rebuild inputs.

    With a whitelist: keep only matching IDs (ignore min_score / skip_reason).
    Without a whitelist: apply existing min_score / skip_reason filters.
    """
    if record_ids:
        allowed = set(record_ids)
        return [record for record in records if getattr(record, "id", None) in allowed]

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

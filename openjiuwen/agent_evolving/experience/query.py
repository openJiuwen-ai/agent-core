# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Read-only experience query service for evolution tools and host commands."""

from __future__ import annotations

from typing import Any, Optional

from openjiuwen.agent_evolving.checkpointing.types import EvolutionRecord
from openjiuwen.agent_evolving.experience.draft_schema import normalize_subject


def split_experience_index_query_terms(query: Optional[str]) -> list[str]:
    """Split a literal OR query expression for experience index matching."""
    terms: list[str] = []
    seen: set[str] = set()
    for raw_term in str(query or "").lower().split("|"):
        term = raw_term.strip()
        if not term or term in seen:
            continue
        seen.add(term)
        terms.append(term)
    return terms


def filter_experience_index_records(
    records: list[EvolutionRecord],
    *,
    target: Optional[str],
    section: Optional[str],
    query: Optional[str],
    sort: str,
) -> list[EvolutionRecord]:
    """Filter records for the bounded agent-facing experience index."""
    filtered: list[EvolutionRecord] = []
    query_terms = split_experience_index_query_terms(query)
    for record in records:
        change_target = getattr(record.change.target, "value", record.change.target)
        if target and change_target != target:
            continue
        if section and record.change.section != section:
            continue
        if query_terms:
            haystack = " ".join(
                [
                    record.summary or "",
                    record.id,
                    str(change_target),
                    record.change.section,
                ]
            ).lower()
            if not any(term in haystack for term in query_terms):
                continue
        filtered.append(record)

    if sort == "updated_desc":
        return sorted(filtered, key=lambda record: getattr(record, "timestamp", ""), reverse=True)
    if sort != "score_desc":
        raise ValueError("sort must be score_desc or updated_desc")
    return sorted(filtered, key=lambda record: (record.score, getattr(record, "timestamp", "")), reverse=True)


def _paginate_experience_index(
    records: list[EvolutionRecord],
    *,
    cursor: Optional[str],
    limit: int,
) -> tuple[list[EvolutionRecord], Optional[str]]:
    """Return one cursor page for the agent-facing experience index."""
    start = int(cursor or "0")
    end = start + limit
    page = records[start:end]
    next_cursor = str(end) if end < len(records) else None
    return page, next_cursor


class ExperienceQueryService:
    """Read-only persisted experience query service."""

    def __init__(self, *, store: Any) -> None:
        self._store = store

    async def list_experiences(
        self,
        subject: dict[str, Any],
        *,
        min_score: Optional[float] = None,
        limit: int = 20,
        cursor: Optional[str] = None,
        target: Optional[str] = None,
        section: Optional[str] = None,
        query: Optional[str] = None,
        sort: str = "score_desc",
    ) -> dict[str, Any]:
        """Return bounded structured experience metadata."""
        normalized_subject = normalize_subject(subject)
        skill_name = normalized_subject.name
        subject_kind = normalized_subject.kind
        if not self._store.skill_exists(skill_name, subject_kind=subject_kind):
            raise ValueError(f"skill not found: {skill_name}")
        if limit < 1 or limit > 100:
            raise ValueError("limit must be between 1 and 100")

        records = await self._store.get_records_by_score(
            skill_name,
            min_score=min_score,
            subject_kind=subject_kind,
        )
        filtered = filter_experience_index_records(
            records,
            target=target,
            section=section,
            query=query,
            sort=sort,
        )
        page, next_cursor = _paginate_experience_index(filtered, cursor=cursor, limit=limit)
        return {
            "success": True,
            "operation": "list",
            "subject": normalized_subject.to_payload(),
            "total_count": len(filtered),
            "has_more": next_cursor is not None,
            "next_cursor": next_cursor,
            "items": [_to_index_item(record) for record in page],
        }

    async def read_experiences(
        self,
        subject: dict[str, Any],
        *,
        record_ids: list[str],
        max_content_chars: int = 2000,
    ) -> dict[str, Any]:
        """Read selected full experience records."""
        normalized_subject = normalize_subject(subject)
        skill_name = normalized_subject.name
        subject_kind = normalized_subject.kind
        if not self._store.skill_exists(skill_name, subject_kind=subject_kind):
            raise ValueError(f"skill not found: {skill_name}")
        if not record_ids:
            raise ValueError("record_ids must be a non-empty list")
        if max_content_chars < 1 or max_content_chars > 20000:
            raise ValueError("max_content_chars must be between 1 and 20000")

        records = await self._store.load_records_by_ids(
            skill_name,
            record_ids,
            subject_kind=subject_kind,
        )
        found_ids = {record.id for record in records}
        missing_ids = [record_id for record_id in record_ids if record_id not in found_ids]
        if missing_ids:
            raise ValueError(f"unknown record_ids: {', '.join(missing_ids)}")
        return {
            "success": True,
            "operation": "read",
            "subject": normalized_subject.to_payload(),
            "items": [_to_read_item(record, max_content_chars=max_content_chars) for record in records],
        }


def _to_index_item(record: EvolutionRecord) -> dict[str, Any]:
    change_target = getattr(record.change.target, "value", record.change.target)
    return {
        "record_id": record.id,
        "summary": record.summary,
        "target": change_target,
        "section": record.change.section,
        "score": record.score,
        "updated_at": getattr(record, "timestamp", ""),
    }


def _to_read_item(record: EvolutionRecord, *, max_content_chars: int) -> dict[str, Any]:
    item = _to_index_item(record)
    content = record.change.content[:max_content_chars]
    item.update(
        {
            "content": content,
            "content_truncated": len(record.change.content) > max_content_chars,
        }
    )
    return item


__all__ = [
    "ExperienceQueryService",
    "filter_experience_index_records",
    "split_experience_index_query_terms",
]

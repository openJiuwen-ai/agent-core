# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Parsing helpers for skill evolution experience drafts."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Optional

from openjiuwen.agent_evolving.checkpointing.types import (
    VALID_SECTIONS,
    EvolutionPatch,
    EvolutionTarget,
)


@dataclass
class ParsedExperienceDraft:
    """Parsed LLM output before it becomes a persisted evolution record."""

    patch: EvolutionPatch
    summary: Optional[str] = None
    keywords: Optional[list[str]] = None


def normalize_keywords(raw: Any) -> Optional[list[str]]:
    """Normalize optional keyword lists from LLM JSON."""
    if not isinstance(raw, list):
        return None
    keywords = [str(item).strip() for item in raw if str(item).strip()]
    return keywords or None


def normalize_summary(raw: Any) -> Optional[str]:
    """Normalize optional one-line experience summaries from LLM JSON."""
    if not isinstance(raw, str):
        return None
    summary = " ".join(raw.split())
    if not summary or summary.lower() == "null":
        return None
    return summary


def parse_experience_draft(data: dict) -> Optional[ParsedExperienceDraft]:
    """Parse one JSON object into a patch plus optional record summary."""
    action = data.get("action", "append")
    if action == "skip":
        return ParsedExperienceDraft(
            patch=EvolutionPatch(
                section="",
                action="skip",
                content="",
                skip_reason=data.get("skip_reason", "unknown"),
            ),
            summary=None,
        )

    section = data.get("section", "Troubleshooting")
    if section not in VALID_SECTIONS:
        section = "Troubleshooting"

    raw_target = data.get("target", "body")
    try:
        target = EvolutionTarget(raw_target)
    except ValueError:
        target = EvolutionTarget.BODY

    merge_target = data.get("merge_target")
    if merge_target in ("null", None):
        merge_target = None

    keywords = normalize_keywords(data.get("keywords"))
    summary = normalize_summary(data.get("summary"))
    patch = EvolutionPatch(
        section=section,
        action="append",
        content=data.get("content", ""),
        target=target,
        merge_target=merge_target,
        script_filename=data.get("script_filename"),
        script_language=data.get("script_language"),
        script_purpose=data.get("script_purpose"),
        keywords=keywords,
        summary=summary,
    )
    return ParsedExperienceDraft(
        patch=patch,
        summary=summary,
        keywords=keywords,
    )


def parse_experience_drafts_with_error(
    raw: str,
    extract_json_with_error_fn: Callable[[str], tuple[Any | None, str]],
) -> tuple[list[ParsedExperienceDraft] | None, str]:
    """Parse raw LLM JSON into experience drafts plus the JSON parser error."""
    data, last_error = extract_json_with_error_fn(raw)
    if data is None:
        return None, last_error

    items = data if isinstance(data, list) else [data]
    drafts: list[ParsedExperienceDraft] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        draft = parse_experience_draft(item)
        if draft is not None:
            drafts.append(draft)
    return drafts, ""


__all__ = [
    "ParsedExperienceDraft",
    "normalize_summary",
    "parse_experience_draft",
    "parse_experience_drafts_with_error",
]

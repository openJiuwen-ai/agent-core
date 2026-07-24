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
)
from openjiuwen.agent_evolving.signal.base import EvolutionTarget
from openjiuwen.core.common.logging import logger

# Persist experience summaries in evolutions.json within this limit.
SUMMARY_MAX_CHARS = 100
# Keep root_cause concise for evolutions.json consumers.
ROOT_CAUSE_MAX_CHARS = 200


@dataclass
class ParsedExperienceDraft:
    """Parsed LLM output before it becomes a persisted evolution record."""

    patch: EvolutionPatch
    summary: Optional[str] = None
    keywords: Optional[list[str]] = None
    root_cause: Optional[str] = None


def normalize_keywords(raw: Any) -> Optional[list[str]]:
    """Normalize optional keyword lists from LLM JSON."""
    if not isinstance(raw, list):
        return None
    keywords = [str(item).strip() for item in raw if str(item).strip()]
    return keywords or None


def normalize_summary(
    raw: Any,
    *,
    max_chars: int = SUMMARY_MAX_CHARS,
) -> Optional[str]:
    """Normalize optional one-line experience summaries from LLM JSON.

    Summaries are capped at ``max_chars`` (default 100) for evolutions.json.
    """
    if not isinstance(raw, str):
        return None
    summary = " ".join(raw.split())
    if not summary or summary.lower() == "null":
        return None
    if max_chars > 0 and len(summary) > max_chars:
        summary = summary[:max_chars].rstrip()
    return summary or None


def normalize_root_cause(
    raw: Any,
    *,
    max_chars: int = ROOT_CAUSE_MAX_CHARS,
) -> Optional[str]:
    """Normalize trigger reason to a single string for evolutions.json.

    Accepts a plain string, or legacy list forms (strings / attribution dicts)
    and flattens them into one sentence.
    """
    if raw is None:
        return None

    if isinstance(raw, str):
        text = " ".join(raw.split())
    elif isinstance(raw, list):
        parts: list[str] = []
        for item in raw:
            if isinstance(item, str):
                part = item.strip()
            elif isinstance(item, dict):
                failure_type = str(item.get("failure_type") or "").strip()
                evidence = item.get("evidence")
                if isinstance(evidence, list):
                    ev = "；".join(str(e).strip() for e in evidence if str(e).strip())
                elif evidence is None:
                    ev = ""
                else:
                    ev = str(evidence).strip()
                part = "：".join(p for p in (failure_type, ev) if p)
            else:
                continue
            if part:
                parts.append(part)
        text = "；".join(parts)
    else:
        text = str(raw).strip()

    if not text or text.lower() == "null":
        return None
    text = " ".join(text.split())
    if max_chars > 0 and len(text) > max_chars:
        text = text[:max_chars].rstrip()
    return text or None


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
        logger.warning(
            "[experience_draft_parser] invalid section=%r; falling back to Troubleshooting. "
            "valid=%s",
            section,
            sorted(VALID_SECTIONS),
        )
        section = "Troubleshooting"

    raw_target = data.get("target", "body")
    try:
        target = EvolutionTarget(raw_target)
    except ValueError:
        logger.warning(
            "[experience_draft_parser] invalid target=%r; falling back to BODY",
            raw_target,
        )
        target = EvolutionTarget.BODY

    merge_target = data.get("merge_target")
    if merge_target in ("null", None):
        merge_target = None

    keywords = normalize_keywords(data.get("keywords"))
    summary = normalize_summary(data.get("summary"))
    root_cause = normalize_root_cause(
        data.get("root_cause", data.get("root_causes"))
    )
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
        root_cause=root_cause,
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
    "SUMMARY_MAX_CHARS",
    "ROOT_CAUSE_MAX_CHARS",
    "ParsedExperienceDraft",
    "normalize_keywords",
    "normalize_summary",
    "normalize_root_cause",
    "parse_experience_draft",
    "parse_experience_drafts_with_error",
]

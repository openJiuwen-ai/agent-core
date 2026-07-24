# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""LLM helpers for skill-level experiences summary in evolutions.json."""

from __future__ import annotations

import json
from typing import Any, List, Optional, Sequence

from openjiuwen.agent_evolving.checkpointing.types import EvolutionLog, EvolutionRecord
from openjiuwen.agent_evolving.optimizer.skill_call.experience_draft_parser import (
    SUMMARY_MAX_CHARS,
    normalize_summary,
)
from openjiuwen.core.common.logging import logger

_LLM_MAX_TOKENS = 256

_SKILL_SUMMARY_PROMPT_CN = """\
你是 Skill 经验摘要助手。请根据下方该 Skill 的全部演进经验，写一句不超过 {max_chars} 字的中文总摘要。

要求：
- 只输出这一句摘要正文，不要标题、编号、引号或 Markdown
- 概括该 Skill 已沉淀的核心能力/排查要点，不要罗列经验 ID
- 不超过 {max_chars} 字

Skill: {skill_id}

经验列表:
{records_json}
"""

_SKILL_SUMMARY_PROMPT_EN = """\
You are a skill experience summarizer. Based on all evolution experiences below, write one English summary of at most {max_chars} characters.

Rules:
- Output only the summary sentence (no title, bullets, quotes, or Markdown)
- Capture the skill's core guidance themes; do not list experience IDs
- At most {max_chars} characters

Skill: {skill_id}

Experiences:
{records_json}
"""


def _active_records(entries: Sequence[EvolutionRecord]) -> List[EvolutionRecord]:
    return [entry for entry in entries if not entry.change.skip_reason]


def _record_payload(record: EvolutionRecord) -> dict[str, Any]:
    summary = (
        (record.summary or "").strip()
        or (record.change.summary or "").strip()
    )
    if not summary and record.change.content:
        summary = record.change.content.splitlines()[0].strip()
    payload: dict[str, Any] = {
        "id": record.id,
        "section": record.change.section,
        "target": record.change.target.value,
        "summary": summary[:SUMMARY_MAX_CHARS],
    }
    if record.root_cause:
        payload["root_cause"] = record.root_cause
    return payload


def fallback_skill_experiences_summary(
    skill_id: str,
    entries: Sequence[EvolutionRecord],
    *,
    max_chars: int = SUMMARY_MAX_CHARS,
) -> Optional[str]:
    """Rule-based join of entry summaries when LLM is unavailable."""
    log = EvolutionLog(skill_id=skill_id, entries=list(entries))
    log.refresh_summary(max_chars=max_chars)
    return log.summary


def _normalize_llm_summary(raw: str, *, max_chars: int = SUMMARY_MAX_CHARS) -> Optional[str]:
    text = (raw or "").strip()
    if not text:
        return None
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    # Prefer the first non-empty line if the model returns multiple.
    for line in text.splitlines():
        candidate = normalize_summary(line, max_chars=max_chars)
        if candidate:
            return candidate
    return normalize_summary(text, max_chars=max_chars)


async def _invoke_llm(llm: Any, model: str, prompt: str) -> Optional[str]:
    try:
        response = await llm.invoke(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=_LLM_MAX_TOKENS,
        )
        text = response.content if hasattr(response, "content") else str(response)
        return str(text) if text else None
    except Exception as exc:
        logger.warning("[SkillSummary] LLM call failed: %s", exc)
        return None


async def generate_skill_experiences_summary(
    skill_id: str,
    entries: Sequence[EvolutionRecord],
    *,
    llm: Any = None,
    model: Optional[str] = None,
    language: str = "cn",
    max_chars: int = SUMMARY_MAX_CHARS,
) -> Optional[str]:
    """Generate a ≤100-char skill-level summary of all experiences via LLM.

    Falls back to concatenating entry summaries when LLM is missing or fails.
    """
    active = _active_records(entries)
    if not active:
        return None

    if llm is None or not model:
        return fallback_skill_experiences_summary(
            skill_id, active, max_chars=max_chars
        )

    payload = [_record_payload(record) for record in active]
    records_json = json.dumps(payload, ensure_ascii=False, indent=2)
    template = _SKILL_SUMMARY_PROMPT_CN if language == "cn" else _SKILL_SUMMARY_PROMPT_EN
    prompt = template.format(
        skill_id=skill_id,
        records_json=records_json,
        max_chars=max_chars,
    )
    raw = await _invoke_llm(llm, model, prompt)
    summary = _normalize_llm_summary(raw or "", max_chars=max_chars)
    if summary:
        return summary

    logger.warning(
        "[SkillSummary] LLM summary empty for skill=%s; using fallback",
        skill_id,
    )
    return fallback_skill_experiences_summary(skill_id, active, max_chars=max_chars)


__all__ = [
    "fallback_skill_experiences_summary",
    "generate_skill_experiences_summary",
]

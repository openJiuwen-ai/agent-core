# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Changelog helpers for skill self-evolution rebuild releases."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Callable, Final, Iterable, List, Optional, Sequence

from openjiuwen.agent_evolving.checkpointing.types import EvolutionRecord
from openjiuwen.core.common.logging import logger

CHANGELOG_FILENAME: Final[str] = "changelog.md"

CHANGELOG_CATEGORIES: Final[tuple[str, ...]] = (
    "Added",
    "Changed",
    "Deprecated",
    "Removed",
    "Fixed",
    "Security",
)

_CATEGORY_LOOKUP: Final[dict[str, str]] = {name.lower(): name for name in CHANGELOG_CATEGORIES}
_DEFAULT_CATEGORY: Final[str] = "Changed"
_SUMMARY_MAX_CHARS: Final[int] = 120
_FIELD_MAX_CHARS: Final[int] = 400
_LLM_MAX_TOKENS: Final[int] = 4096

CHANGELOG_HEADER: Final[str] = (
    "# Changelog\n"
    "\n"
    "本Skill所有版本变更与演进经验均记录在此，"
    "格式遵循 Keep a Changelog 1.1.0，版本号遵循语义化版本 2.0.0。\n"
)

_VERSION_HEADER_RE = re.compile(
    r"^## \[([^\]]+)\](?:\s*-\s*(\d{4}-\d{2}-\d{2}))?\s*$",
    re.MULTILINE,
)

_CLASSIFY_PROMPT_CN = """\
你是 Skill 版本变更分类专家。请根据下列演进经验，为每一条选择 Keep a Changelog 分类，并写一句简洁中文摘要。

## 分类含义

- Added：新增的功能、特性、能力（新增触发条件、工具调用、处理分支、核心规则）
- Changed：现有功能的优化、调整（优化 Prompt、调整工作流、改参数、性能优化）
- Deprecated：标记废弃、未来将移除的功能
- Removed：已正式移除的废弃功能
- Fixed：Bug、问题、缺陷修复（边界 Case、工具调用错误、逻辑漏洞）
- Security：安全、合规相关（Prompt 注入防护、敏感信息过滤、合规规则）

## 输出要求

- 仅输出 JSON 数组，不要 Markdown 代码围栏或其他说明
- 每项必须包含：id（与输入一致）、category（上述六类之一）、summary（一句中文，不要包含经验 ID）
- 必须覆盖输入中的每一条 id

## 经验列表

{records_json}
"""

_CLASSIFY_PROMPT_EN = """\
You are a skill changelog classifier. For each evolution experience below, pick a Keep a Changelog category and write a short English summary.

## Categories

- Added: new capabilities (triggers, tools, branches, core rules)
- Changed: optimizations/adjustments (prompt, workflow, params, performance)
- Deprecated: marked for future removal
- Removed: formally removed features
- Fixed: bug/defect fixes
- Security: security/compliance (prompt injection, sensitive filtering, compliance)

## Output

- Output a JSON array only (no markdown fences)
- Each item: id (same as input), category (one of the six), summary (one sentence, no experience id)
- Cover every input id

## Experiences

{records_json}
"""


@dataclass(frozen=True)
class ClassifiedChangelogEntry:
    """One changelog bullet after classification."""

    id: str
    category: str
    summary: str


ClassifyFn = Callable[[Sequence[EvolutionRecord]], Any]


def empty_changelog_template() -> str:
    """Return the initial changelog.md body (header only, no Unreleased)."""
    return CHANGELOG_HEADER.rstrip() + "\n"


def normalize_category(raw: Any) -> str:
    """Map arbitrary category text to a valid Keep a Changelog category."""
    if raw is None:
        return _DEFAULT_CATEGORY
    text = str(raw).strip()
    if not text:
        return _DEFAULT_CATEGORY
    return _CATEGORY_LOOKUP.get(text.lower(), _DEFAULT_CATEGORY)


def fallback_summary(record: EvolutionRecord) -> str:
    """Build a short summary from record content when LLM is unavailable."""
    content = (getattr(record.change, "content", "") or "").strip()
    if not content:
        content = (getattr(record, "context", "") or "").strip()
    first_line = content.split("\n", 1)[0].strip() if content else ""
    if not first_line:
        first_line = f"更新经验 {record.id}"
    if len(first_line) > _SUMMARY_MAX_CHARS:
        return first_line[: _SUMMARY_MAX_CHARS - 1] + "…"
    return first_line


def fallback_classified_entries(records: Iterable[EvolutionRecord]) -> List[ClassifiedChangelogEntry]:
    """Classify all records as Changed with truncated content summaries."""
    entries: List[ClassifiedChangelogEntry] = []
    for record in records:
        if getattr(record.change, "skip_reason", None):
            continue
        entries.append(
            ClassifiedChangelogEntry(
                id=record.id,
                category=_DEFAULT_CATEGORY,
                summary=fallback_summary(record),
            )
        )
    return entries


def _truncate(text: str, max_chars: int = _FIELD_MAX_CHARS) -> str:
    value = (text or "").strip()
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 1] + "…"


def _record_payload(record: EvolutionRecord) -> dict[str, Any]:
    change = record.change
    target = getattr(change.target, "value", change.target)
    return {
        "id": record.id,
        "source": record.source,
        "section": change.section,
        "target": target,
        "action": change.action,
        "merge_target": change.merge_target,
        "context": _truncate(record.context or ""),
        "content": _truncate(change.content or ""),
    }


def _active_records(records: Sequence[EvolutionRecord]) -> List[EvolutionRecord]:
    return [record for record in records if not getattr(record.change, "skip_reason", None)]


def _assistant_text_from_response(response: Any) -> str:
    content = getattr(response, "content", None)
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and part.get("type") == "text":
                parts.append(str(part.get("text", "")))
        text = "".join(parts).strip()
    elif content is None:
        text = ""
    else:
        text = str(content).strip()
    if text:
        return text
    reasoning = getattr(response, "reasoning_content", None)
    if reasoning and str(reasoning).strip():
        return str(reasoning).strip()
    return ""


def _extract_json(raw: str) -> Optional[Any]:
    text = (raw or "").strip()
    if not text:
        return None

    def _try_parse(candidate: str) -> Optional[Any]:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            return None

    parsed = _try_parse(text)
    if parsed is not None:
        return parsed

    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.IGNORECASE)
    if fence:
        parsed = _try_parse(fence.group(1).strip())
        if parsed is not None:
            return parsed

    for pattern in (r"\[[\s\S]*\]", r"\{[\s\S]*\}"):
        matched = re.search(pattern, text)
        if matched:
            parsed = _try_parse(matched.group(0))
            if parsed is not None:
                return parsed
    return None


def _parse_classified_payload(
    raw: str,
    records: Sequence[EvolutionRecord],
) -> Optional[List[ClassifiedChangelogEntry]]:
    data = _extract_json(raw)
    if data is None:
        return None
    if isinstance(data, dict):
        items = data.get("items") or data.get("entries") or data.get("results")
        if not isinstance(items, list):
            return None
        data = items
    if not isinstance(data, list):
        return None

    by_id = {record.id: record for record in records}
    parsed_by_id: dict[str, ClassifiedChangelogEntry] = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        record_id = str(item.get("id") or "").strip()
        if not record_id or record_id not in by_id:
            continue
        summary = str(item.get("summary") or "").strip()
        if not summary:
            summary = fallback_summary(by_id[record_id])
        elif len(summary) > _SUMMARY_MAX_CHARS:
            summary = summary[: _SUMMARY_MAX_CHARS - 1] + "…"
        parsed_by_id[record_id] = ClassifiedChangelogEntry(
            id=record_id,
            category=normalize_category(item.get("category")),
            summary=summary,
        )

    entries: List[ClassifiedChangelogEntry] = []
    for record in records:
        if record.id in parsed_by_id:
            entries.append(parsed_by_id[record.id])
        else:
            entries.append(
                ClassifiedChangelogEntry(
                    id=record.id,
                    category=_DEFAULT_CATEGORY,
                    summary=fallback_summary(record),
                )
            )
    return entries


async def _invoke_llm(llm: Any, model: str, prompt: str) -> Optional[str]:
    try:
        response = await llm.invoke(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=_LLM_MAX_TOKENS,
        )
        return _assistant_text_from_response(response)
    except Exception as exc:
        logger.error("[Changelog] LLM classify call failed: %s", exc)
        return None


async def classify_records_for_changelog(
    records: Sequence[EvolutionRecord],
    *,
    llm: Any = None,
    model: Optional[str] = None,
    language: str = "cn",
    classify_fn: Optional[ClassifyFn] = None,
) -> List[ClassifiedChangelogEntry]:
    """Classify evolution records into changelog categories via LLM or fallback.

    ``classify_fn`` may be sync or async and should return a list of
    ``ClassifiedChangelogEntry`` (or dicts with id/category/summary).
    """
    active = _active_records(records)
    if not active:
        return []

    if classify_fn is not None:
        result = classify_fn(active)
        if hasattr(result, "__await__"):
            result = await result  # type: ignore[misc]
        return _coerce_classified_entries(result, active)

    if llm is None or not model:
        return fallback_classified_entries(active)

    payload = [_record_payload(record) for record in active]
    records_json = json.dumps(payload, ensure_ascii=False, indent=2)
    template = _CLASSIFY_PROMPT_CN if language == "cn" else _CLASSIFY_PROMPT_EN
    prompt = template.format(records_json=records_json)
    raw = await _invoke_llm(llm, model, prompt)
    if raw is None:
        return fallback_classified_entries(active)

    parsed = _parse_classified_payload(raw, active)
    if parsed is None:
        logger.warning("[Changelog] failed to parse LLM classify response; using fallback")
        return fallback_classified_entries(active)
    return parsed


def _coerce_classified_entries(
    result: Any,
    records: Sequence[EvolutionRecord],
) -> List[ClassifiedChangelogEntry]:
    if result is None:
        return fallback_classified_entries(records)
    if isinstance(result, list) and result and isinstance(result[0], ClassifiedChangelogEntry):
        return list(result)

    by_id = {record.id: record for record in records}
    coerced: List[ClassifiedChangelogEntry] = []
    if isinstance(result, list):
        for item in result:
            if isinstance(item, ClassifiedChangelogEntry):
                coerced.append(item)
                continue
            if not isinstance(item, dict):
                continue
            record_id = str(item.get("id") or "").strip()
            if not record_id or record_id not in by_id:
                continue
            summary = str(item.get("summary") or "").strip() or fallback_summary(by_id[record_id])
            coerced.append(
                ClassifiedChangelogEntry(
                    id=record_id,
                    category=normalize_category(item.get("category")),
                    summary=summary,
                )
            )
    if len(coerced) == len(records):
        return coerced
    return fallback_classified_entries(records)


def render_version_section(
    version: str,
    entries: Sequence[ClassifiedChangelogEntry],
    *,
    release_date: Optional[str] = None,
) -> str:
    """Render one ``## [version] - date`` markdown section."""
    day = release_date or date.today().isoformat()
    lines = [f"## [{version}] - {day}", ""]
    grouped: dict[str, List[ClassifiedChangelogEntry]] = {name: [] for name in CHANGELOG_CATEGORIES}
    for entry in entries:
        category = normalize_category(entry.category)
        grouped.setdefault(category, []).append(
            ClassifiedChangelogEntry(id=entry.id, category=category, summary=entry.summary)
        )

    wrote_any = False
    for category in CHANGELOG_CATEGORIES:
        bucket = grouped.get(category) or []
        if not bucket:
            continue
        wrote_any = True
        lines.append(f"### {category}")
        for entry in bucket:
            summary = (entry.summary or "").strip() or f"更新经验 {entry.id}"
            lines.append(f"- {summary} (关联经验 {entry.id})")
        lines.append("")

    if not wrote_any:
        lines.append(f"### {_DEFAULT_CATEGORY}")
        lines.append(f"- 版本 {version} 发布")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def changelog_has_version(content: str, version: str) -> bool:
    """Return True when a ``## [version]`` section already exists."""
    for match in _VERSION_HEADER_RE.finditer(content or ""):
        if match.group(1).strip() == version:
            return True
    return False


def insert_version_section(existing: str, version_section: str) -> str:
    """Insert a new version section after the header, before older versions."""
    text = (existing or "").strip()
    if not text:
        return (CHANGELOG_HEADER.rstrip() + "\n\n" + version_section.strip() + "\n")

    match = _VERSION_HEADER_RE.search(text)
    if match is None:
        base = text.rstrip() + "\n\n"
        return base + version_section.strip() + "\n"

    insert_at = match.start()
    prefix = text[:insert_at].rstrip()
    suffix = text[insert_at:].lstrip()
    return prefix + "\n\n" + version_section.strip() + "\n\n" + suffix


def merge_changelog_for_release(
    existing: str,
    version: str,
    entries: Sequence[ClassifiedChangelogEntry],
    *,
    release_date: Optional[str] = None,
) -> Optional[str]:
    """Build updated changelog content, or None when version already present."""
    content = existing if (existing or "").strip() else empty_changelog_template()
    if changelog_has_version(content, version):
        return None
    section = render_version_section(version, entries, release_date=release_date)
    return insert_version_section(content, section)


def utc_today_iso() -> str:
    """UTC calendar date as YYYY-MM-DD."""
    return datetime.now(tz=timezone.utc).date().isoformat()


__all__ = [
    "CHANGELOG_CATEGORIES",
    "CHANGELOG_FILENAME",
    "CHANGELOG_HEADER",
    "ClassifiedChangelogEntry",
    "changelog_has_version",
    "classify_records_for_changelog",
    "empty_changelog_template",
    "fallback_classified_entries",
    "fallback_summary",
    "insert_version_section",
    "merge_changelog_for_release",
    "normalize_category",
    "render_version_section",
    "utc_today_iso",
]

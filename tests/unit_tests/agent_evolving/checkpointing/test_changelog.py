# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for skill changelog classification and rendering."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openjiuwen.agent_evolving.checkpointing.changelog import (
    ClassifiedChangelogEntry,
    changelog_has_version,
    classify_records_for_changelog,
    empty_changelog_template,
    fallback_classified_entries,
    insert_version_section,
    merge_changelog_for_release,
    normalize_category,
    render_version_section,
)
from openjiuwen.agent_evolving.checkpointing.types import EvolutionPatch, EvolutionRecord
from openjiuwen.agent_evolving.signal.base import EvolutionTarget


def _record(
    record_id: str,
    content: str,
    *,
    skip_reason: str | None = None,
) -> EvolutionRecord:
    return EvolutionRecord(
        id=record_id,
        source="execution_failure",
        timestamp="2026-01-01T00:00:00+00:00",
        context="ctx",
        change=EvolutionPatch(
            section="Troubleshooting",
            action="append",
            content=content,
            target=EvolutionTarget.BODY,
            skip_reason=skip_reason,
        ),
        score=0.8,
    )


def test_normalize_category_and_empty_template():
    assert normalize_category("fixed") == "Fixed"
    assert normalize_category("UNKNOWN") == "Changed"
    template = empty_changelog_template()
    assert template.startswith("# Changelog")
    assert "Unreleased" not in template
    assert "## [" not in template


def test_fallback_skips_skip_reason_records():
    active = _record("ev_aaaa1111", "fix timeout")
    skipped = _record("ev_bbbb2222", "dup", skip_reason="duplicate")
    entries = fallback_classified_entries([active, skipped])
    assert len(entries) == 1
    assert entries[0].id == "ev_aaaa1111"
    assert entries[0].category == "Changed"


def test_render_version_section_groups_and_links():
    section = render_version_section(
        "1.2.1",
        [
            ClassifiedChangelogEntry(
                id="ev_aaaa1111",
                category="Fixed",
                summary="修复超时回退失效",
            ),
            ClassifiedChangelogEntry(
                id="ev_bbbb2222",
                category="Added",
                summary="新增多轮记忆",
            ),
        ],
        release_date="2026-07-17",
    )
    assert section.startswith("## [1.2.1] - 2026-07-17")
    assert "### Added" in section
    assert "### Fixed" in section
    assert "(关联经验 ev_aaaa1111)" in section
    assert "(关联经验 ev_bbbb2222)" in section
    assert "Unreleased" not in section


def test_insert_and_merge_keep_newest_first_and_idempotent():
    existing = empty_changelog_template()
    first = merge_changelog_for_release(
        existing,
        "1.0.1",
        [ClassifiedChangelogEntry(id="ev_1", category="Fixed", summary="fix a")],
        release_date="2026-07-01",
    )
    assert first is not None
    assert changelog_has_version(first, "1.0.1")

    second = merge_changelog_for_release(
        first,
        "1.1.0",
        [ClassifiedChangelogEntry(id="ev_2", category="Added", summary="add b")],
        release_date="2026-07-10",
    )
    assert second is not None
    assert second.index("## [1.1.0]") < second.index("## [1.0.1]")

    again = merge_changelog_for_release(
        second,
        "1.1.0",
        [ClassifiedChangelogEntry(id="ev_3", category="Changed", summary="dup")],
        release_date="2026-07-11",
    )
    assert again is None


def test_insert_version_section_after_header():
    base = empty_changelog_template()
    section = render_version_section(
        "1.0.1",
        [ClassifiedChangelogEntry(id="ev_1", category="Changed", summary="x")],
        release_date="2026-07-17",
    )
    merged = insert_version_section(base, section)
    assert merged.startswith("# Changelog")
    assert "## [1.0.1]" in merged


@pytest.mark.asyncio
async def test_classify_records_uses_llm_json():
    record = _record("ev_aaaa1111", "timeout fallback broken")
    llm = AsyncMock()
    llm.invoke = AsyncMock(
        return_value=SimpleNamespace(
            content='[{"id":"ev_aaaa1111","category":"Fixed","summary":"修复超时回退"}]'
        )
    )
    entries = await classify_records_for_changelog(
        [record],
        llm=llm,
        model="m",
        language="cn",
    )
    assert len(entries) == 1
    assert entries[0].category == "Fixed"
    assert entries[0].summary == "修复超时回退"
    llm.invoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_classify_records_falls_back_on_llm_failure():
    record = _record("ev_aaaa1111", "timeout fallback broken")
    llm = AsyncMock()
    llm.invoke = AsyncMock(side_effect=RuntimeError("boom"))
    entries = await classify_records_for_changelog(
        [record],
        llm=llm,
        model="m",
    )
    assert len(entries) == 1
    assert entries[0].category == "Changed"
    assert "timeout fallback broken" in entries[0].summary


@pytest.mark.asyncio
async def test_classify_records_invalid_category_and_missing_id_fallback():
    record_a = _record("ev_aaaa1111", "add rule")
    record_b = _record("ev_bbbb2222", "other")
    llm = AsyncMock()
    llm.invoke = AsyncMock(
        return_value=SimpleNamespace(
            content='[{"id":"ev_aaaa1111","category":"Nope","summary":"bad cat"}]'
        )
    )
    entries = await classify_records_for_changelog(
        [record_a, record_b],
        llm=llm,
        model="m",
    )
    by_id = {entry.id: entry for entry in entries}
    assert by_id["ev_aaaa1111"].category == "Changed"
    assert by_id["ev_aaaa1111"].summary == "bad cat"
    assert by_id["ev_bbbb2222"].category == "Changed"
    assert "other" in by_id["ev_bbbb2222"].summary


@pytest.mark.asyncio
async def test_classify_fn_override():
    record = _record("ev_aaaa1111", "content")

    def _classify(_records):
        return [
            ClassifiedChangelogEntry(
                id="ev_aaaa1111",
                category="Security",
                summary="新增注入防护",
            )
        ]

    entries = await classify_records_for_changelog([record], classify_fn=_classify)
    assert entries[0].category == "Security"
    assert entries[0].summary == "新增注入防护"

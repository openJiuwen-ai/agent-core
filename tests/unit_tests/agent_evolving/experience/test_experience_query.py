# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest

from openjiuwen.agent_evolving.checkpointing.types import EvolutionPatch, EvolutionRecord, EvolutionTarget
from openjiuwen.agent_evolving.experience.query import (
    ExperienceQueryService,
    filter_experience_index_records,
    split_experience_index_query_terms,
)


def _make_record(
    record_id: str,
    *,
    summary: str,
    content: str,
    score: float,
    timestamp: str,
    target: EvolutionTarget | str = EvolutionTarget.BODY,
    section: str = "Troubleshooting",
) -> EvolutionRecord:
    record = EvolutionRecord.make(
        source="test",
        context="ctx",
        summary=summary,
        change=EvolutionPatch(
            section=section,
            action="append",
            content=content,
            target=target,
        ),
    )
    record.id = record_id
    record.score = score
    record.timestamp = timestamp
    return record


def test_filter_experience_index_records_filters_and_sorts_by_score():
    high = _make_record("ev_high", summary="Parser fields", content="A", score=0.9, timestamp="2026-01-02")
    low = _make_record("ev_low", summary="Other", content="B", score=0.5, timestamp="2026-01-03")

    result = filter_experience_index_records(
        [low, high],
        target="body",
        section="Troubleshooting",
        query="parser",
        sort="score_desc",
    )

    assert [record.id for record in result] == ["ev_high"]


def test_filter_experience_index_records_uses_pipe_separated_literal_or_terms():
    parser = _make_record("ev_parser", summary="Use parser fields", content="A", score=0.9, timestamp="1")
    report = _make_record("ev_report", summary="Keep final report concise", content="B", score=0.8, timestamp="2")

    result = filter_experience_index_records(
        [report, parser],
        target=None,
        section=None,
        query="capture|parser|lesson",
        sort="score_desc",
    )

    assert [record.id for record in result] == ["ev_parser"]


@pytest.mark.asyncio
async def test_list_experiences_returns_bounded_metadata():
    store = Mock()
    store.skill_exists.return_value = True
    record = _make_record(
        "ev_1",
        summary="Use structured fields.",
        content="Full content should not appear in list output.",
        score=0.82,
        timestamp="2026-01-01T00:00:00Z",
    )
    lower = _make_record(
        "ev_2",
        summary="Lower score.",
        content="Full content should not appear in list output.",
        score=0.5,
        timestamp="2026-01-02T00:00:00Z",
    )
    store.get_records_by_score = AsyncMock(return_value=[record, lower])
    query_service = ExperienceQueryService(store=store)

    result = await query_service.list_experiences({"kind": "skill", "name": "skill-a"}, min_score=0.5, limit=1)

    assert result["success"] is True
    assert result["operation"] == "list"
    assert result["subject"] == {"kind": "skill", "name": "skill-a"}
    assert result["has_more"] is True
    assert result["next_cursor"] == "1"
    assert result["items"][0]["record_id"] == "ev_1"
    assert result["items"][0]["summary"] == "Use structured fields."
    assert "content" not in result["items"][0]
    store.skill_exists.assert_called_once_with("skill-a", subject_kind="skill")
    store.get_records_by_score.assert_awaited_once_with("skill-a", min_score=0.5, subject_kind="skill")


@pytest.mark.asyncio
async def test_read_experiences_truncates_content():
    store = Mock()
    store.skill_exists.return_value = True
    record = _make_record(
        "ev_1",
        summary="Use structured fields.",
        content="Keep structured fields.",
        score=0.82,
        timestamp="2026-01-01T00:00:00Z",
    )
    store.load_records_by_ids = AsyncMock(return_value=[record])
    query_service = ExperienceQueryService(store=store)

    result = await query_service.read_experiences(
        {"kind": "skill", "name": "skill-a"},
        record_ids=["ev_1"],
        max_content_chars=8,
    )

    assert result["operation"] == "read"
    assert result["items"][0]["content"] == "Keep str"
    assert result["items"][0]["content_truncated"] is True
    store.skill_exists.assert_called_once_with("skill-a", subject_kind="skill")
    store.load_records_by_ids.assert_awaited_once_with("skill-a", ["ev_1"], subject_kind="skill")


@pytest.mark.asyncio
async def test_read_experiences_rejects_unknown_record_ids():
    store = Mock()
    store.skill_exists.return_value = True
    record = _make_record(
        "ev_1",
        summary="Use structured fields.",
        content="Keep structured fields.",
        score=0.82,
        timestamp="2026-01-01T00:00:00Z",
    )
    store.load_records_by_ids = AsyncMock(return_value=[record])
    query_service = ExperienceQueryService(store=store)

    with pytest.raises(ValueError, match="unknown record_ids: ev_missing"):
        await query_service.read_experiences(
            {"kind": "skill", "name": "skill-a"},
            record_ids=["ev_1", "ev_missing"],
        )


@pytest.mark.asyncio
async def test_read_experiences_rejects_non_positive_max_content_chars():
    store = Mock()
    store.skill_exists.return_value = True
    query_service = ExperienceQueryService(store=store)

    with pytest.raises(ValueError, match="max_content_chars must be between 1 and 20000"):
        await query_service.read_experiences(
            {"kind": "skill", "name": "browser"},
            record_ids=["ev_1"],
            max_content_chars=0,
        )


def test_split_experience_index_query_terms_uses_literal_or_terms():
    assert split_experience_index_query_terms(" parser | parser | json fields ") == ["parser", "json fields"]


@pytest.mark.asyncio
async def test_query_service_default_accepts_supported_subject_kinds():
    store = Mock()
    store.skill_exists.return_value = True
    store.get_records_by_score = AsyncMock(return_value=[])
    query_service = ExperienceQueryService(store=store)

    result = await query_service.list_experiences({"kind": "team-skill", "name": "team-a"})

    assert result["subject"] == {"kind": "swarm-skill", "name": "team-a"}
    store.skill_exists.assert_called_once_with("team-a", subject_kind="swarm-skill")
    store.get_records_by_score.assert_awaited_once_with("team-a", min_score=None, subject_kind="swarm-skill")


@pytest.mark.asyncio
async def test_query_service_normalizes_team_skill_alias_to_swarm_skill():
    store = Mock()
    store.skill_exists.return_value = True
    store.get_records_by_score = AsyncMock(return_value=[])
    query_service = ExperienceQueryService(store=store)

    result = await query_service.list_experiences({"kind": "team-skill", "name": "team-a"})

    assert result["subject"] == {"kind": "swarm-skill", "name": "team-a"}


@pytest.mark.asyncio
async def test_query_service_accepts_legacy_team_skill_subject_alias():
    store = Mock()
    store.skill_exists.return_value = True
    store.get_records_by_score = AsyncMock(return_value=[])
    query_service = ExperienceQueryService(store=store)

    result = await query_service.list_experiences({"kind": "team-skill", "name": "team-a"})

    assert result["subject"] == {"kind": "swarm-skill", "name": "team-a"}


@pytest.mark.asyncio
async def test_query_service_accepts_shared_subject_kinds_without_configuration():
    store = Mock()
    store.skill_exists.return_value = True
    store.get_records_by_score = AsyncMock(return_value=[])
    shared_query_service = ExperienceQueryService(store=store)

    result = await shared_query_service.list_experiences(
        {"kind": "team-skill", "name": "team-a"},
        min_score=None,
        limit=20,
    )

    assert result["subject"] == {"kind": "swarm-skill", "name": "team-a"}

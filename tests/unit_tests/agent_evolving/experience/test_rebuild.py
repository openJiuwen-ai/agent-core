# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest

from openjiuwen.agent_evolving.checkpointing.types import EvolutionPatch, EvolutionRecord, EvolutionTarget
from openjiuwen.agent_evolving.experience.rebuild import ExperienceRebuildService


def _make_record(content: str, *, score: float = 0.8) -> EvolutionRecord:
    record = EvolutionRecord.make(
        source="test",
        context="ctx",
        summary="summary",
        change=EvolutionPatch(
            section="Troubleshooting",
            action="append",
            content=content,
            target=EvolutionTarget.BODY,
        ),
    )
    record.id = f"ev_{content[:4]}"
    record.score = score
    return record


def _make_archive_service(*, pair: Mock | None = None) -> Mock:
    archive_service = Mock()
    archive_service.archive_current_pair = AsyncMock(return_value=pair)
    archive_service.prune = Mock()
    return archive_service


def _make_archive_pair() -> Mock:
    pair = Mock()
    pair.version = "v1"
    pair.evolution_archive_name = "evolutions.v1.json"
    pair.to_payload.return_value = {
        "version": "v1",
        "skill_archive": "SKILL.v1.md",
        "evolution_archive": "evolutions.v1.json",
    }
    return pair


@pytest.mark.asyncio
async def test_prepare_rebuild_context_returns_none_when_skill_missing():
    store = Mock()
    store.skill_exists.return_value = False
    rebuild_service = ExperienceRebuildService(store=store)

    result = await rebuild_service.prepare_rebuild_context({"kind": "skill", "name": "missing"})

    assert result is None
    store.skill_exists.assert_called_once_with("missing", subject_kind="skill")


@pytest.mark.asyncio
async def test_prepare_rebuild_context_archives_filters_and_clears():
    high = _make_record("good experience", score=0.8)
    low = _make_record("bad experience", score=0.3)
    pair = _make_archive_pair()
    archive_service = _make_archive_service(pair=pair)
    store = Mock()
    store.skill_exists.return_value = True
    store.load_full_evolution_log = AsyncMock(return_value=Mock(entries=[high, low]))
    store.clear_evolutions = AsyncMock()
    rebuild_service = ExperienceRebuildService(store=store, archive_service=archive_service)

    result = await rebuild_service.prepare_rebuild_context(
        {"kind": "skill", "name": "skill-a"}, user_intent="optimize", min_score=0.5
    )

    assert result is not None
    assert result["subject"] == {"kind": "skill", "name": "skill-a"}
    assert result["user_intent"] == "optimize"
    assert result["records"][0]["content"] == "good experience"
    assert result["archive_path"] == "evolutions.v1.json"
    assert result["archive_version"] == "v1"
    assert result["archive_pair"]["skill_archive"] == "SKILL.v1.md"
    assert all(item["content"] != "bad experience" for item in result["records"])
    store.skill_exists.assert_called_once_with("skill-a", subject_kind="skill")
    store.load_full_evolution_log.assert_awaited_once_with("skill-a", subject_kind="skill")
    archive_service.archive_current_pair.assert_awaited_once_with("skill-a", subject_kind="skill")
    store.clear_evolutions.assert_awaited_once_with("skill-a", subject_kind="skill")
    archive_service.prune.assert_called_once_with("skill-a", subject_kind="skill")


@pytest.mark.asyncio
async def test_prepare_rebuild_context_does_not_clear_when_evolution_archive_fails():
    record = _make_record("good experience", score=0.8)
    archive_service = _make_archive_service(pair=None)
    store = Mock()
    store.skill_exists.return_value = True
    store.load_full_evolution_log = AsyncMock(return_value=Mock(entries=[record]))
    store.clear_evolutions = AsyncMock()
    rebuild_service = ExperienceRebuildService(store=store, archive_service=archive_service)

    result = await rebuild_service.prepare_rebuild_context({"kind": "skill", "name": "skill-a"})

    assert result is not None
    store.clear_evolutions.assert_not_called()
    archive_service.prune.assert_not_called()


@pytest.mark.asyncio
async def test_prepare_rebuild_context_uses_subject_envelope():
    record = _make_record("good experience", score=0.8)
    pair = _make_archive_pair()
    archive_service = _make_archive_service(pair=pair)
    store = Mock()
    store.skill_exists.return_value = True
    store.load_full_evolution_log = AsyncMock(return_value=Mock(entries=[record]))
    store.clear_evolutions = AsyncMock()

    rebuild_service = ExperienceRebuildService(store=store, archive_service=archive_service)

    result = await rebuild_service.prepare_rebuild_context(
        {"kind": "team-skill", "name": "team-skill-a"},
        min_score=0.5,
    )

    assert result is not None
    assert result["subject"] == {"kind": "swarm-skill", "name": "team-skill-a"}
    store.skill_exists.assert_called_once_with("team-skill-a", subject_kind="swarm-skill")
    store.load_full_evolution_log.assert_awaited_once_with("team-skill-a", subject_kind="swarm-skill")
    archive_service.archive_current_pair.assert_awaited_once_with("team-skill-a", subject_kind="swarm-skill")
    store.clear_evolutions.assert_awaited_once_with("team-skill-a", subject_kind="swarm-skill")

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


@pytest.mark.asyncio
async def test_prepare_rebuild_context_returns_none_when_skill_missing():
    store = Mock()
    store.skill_exists.return_value = False
    rebuild_service = ExperienceRebuildService(store=store)

    result = await rebuild_service.prepare_rebuild_context({"kind": "skill", "name": "missing"})

    assert result is None
    store.skill_exists.assert_called_once_with("missing", subject_kind="skill")
    store.archive_skill_body.assert_not_called()


@pytest.mark.asyncio
async def test_prepare_rebuild_context_archives_filters_and_clears():
    high = _make_record("good experience", score=0.8)
    low = _make_record("bad experience", score=0.3)
    store = Mock()
    store.skill_exists.return_value = True
    store.archive_skill_body = AsyncMock(return_value="SKILL.v1.md")
    store.archive_evolutions = AsyncMock(return_value="evolutions.v1.json")
    store.load_full_evolution_log = AsyncMock(return_value=Mock(entries=[high, low]))
    store.clear_evolutions = AsyncMock()
    rebuild_service = ExperienceRebuildService(store=store)

    result = await rebuild_service.prepare_rebuild_context(
        {"kind": "skill", "name": "skill-a"}, user_intent="optimize", min_score=0.5
    )

    assert result is not None
    assert result["subject"] == {"kind": "skill", "name": "skill-a"}
    assert result["user_intent"] == "optimize"
    assert result["records"][0]["content"] == "good experience"
    assert all(item["content"] != "bad experience" for item in result["records"])
    store.skill_exists.assert_called_once_with("skill-a", subject_kind="skill")
    store.archive_skill_body.assert_awaited_once_with("skill-a", subject_kind="skill")
    store.archive_evolutions.assert_awaited_once_with("skill-a", subject_kind="skill")
    store.load_full_evolution_log.assert_awaited_once_with("skill-a", subject_kind="skill")
    store.clear_evolutions.assert_awaited_once_with("skill-a", subject_kind="skill")


@pytest.mark.asyncio
async def test_prepare_rebuild_context_does_not_clear_when_evolution_archive_fails():
    record = _make_record("good experience", score=0.8)
    store = Mock()
    store.skill_exists.return_value = True
    store.archive_skill_body = AsyncMock(side_effect=RuntimeError("body archive failed"))
    store.archive_evolutions = AsyncMock(return_value=None)
    store.load_full_evolution_log = AsyncMock(return_value=Mock(entries=[record]))
    store.clear_evolutions = AsyncMock()
    rebuild_service = ExperienceRebuildService(store=store)

    result = await rebuild_service.prepare_rebuild_context({"kind": "skill", "name": "skill-a"})

    assert result is not None
    store.clear_evolutions.assert_not_called()


@pytest.mark.asyncio
async def test_prepare_rebuild_context_uses_subject_envelope():
    record = _make_record("good experience", score=0.8)
    store = Mock()
    store.skill_exists.return_value = True
    store.archive_skill_body = AsyncMock(return_value="SKILL.v1.md")
    store.archive_evolutions = AsyncMock(return_value="evolutions.v1.json")
    store.load_full_evolution_log = AsyncMock(return_value=Mock(entries=[record]))
    store.clear_evolutions = AsyncMock()

    rebuild_service = ExperienceRebuildService(store=store)

    result = await rebuild_service.prepare_rebuild_context(
        {"kind": "team-skill", "name": "team-skill-a"},
        min_score=0.5,
    )

    assert result is not None
    assert result["subject"] == {"kind": "swarm-skill", "name": "team-skill-a"}
    store.skill_exists.assert_called_once_with("team-skill-a", subject_kind="swarm-skill")
    store.archive_skill_body.assert_awaited_once_with("team-skill-a", subject_kind="swarm-skill")
    store.archive_evolutions.assert_awaited_once_with("team-skill-a", subject_kind="swarm-skill")
    store.load_full_evolution_log.assert_awaited_once_with("team-skill-a", subject_kind="swarm-skill")
    store.clear_evolutions.assert_awaited_once_with("team-skill-a", subject_kind="swarm-skill")

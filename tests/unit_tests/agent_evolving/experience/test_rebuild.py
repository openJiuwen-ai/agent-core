# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, Mock

from openjiuwen.agent_evolving.checkpointing.types import EvolutionPatch, EvolutionRecord
from openjiuwen.agent_evolving.experience.rebuild import ExperienceRebuildService
from openjiuwen.agent_evolving.signal.base import EvolutionTarget


def _make_record(content: str, *, score: float = 0.8, skip_reason: str | None = None) -> EvolutionRecord:
    record = EvolutionRecord.make(
        source="test",
        context="ctx",
        change=EvolutionPatch(
            section="Troubleshooting",
            action="append",
            content=content,
            target=EvolutionTarget.BODY,
            skip_reason=skip_reason,
        ),
        score=score,
    )
    
    record.id = f"ev_{content[:4]}"
    return record


def test_prepare_rebuild_context_returns_none_when_skill_missing():
    store = Mock()
    store.skill_exists.return_value = False
    rebuild_service = ExperienceRebuildService(store=store)

    result = asyncio.run(rebuild_service.prepare_rebuild_context({"kind": "skill", "name": "missing"}))

    assert result is None
    store.skill_exists.assert_called_once_with("missing")
    store.archive_skill_body.assert_not_called()


def test_prepare_rebuild_context_archives_filters_and_keeps_evolution_log():
    high = _make_record("good experience", score=0.8)
    low = _make_record("bad experience", score=0.3)
    skipped = _make_record("skipped experience", score=0.9, skip_reason="duplicate")
    store = Mock()
    store.skill_exists.return_value = True
    store.archive_skill_body = AsyncMock(return_value="SKILL.v1.md")
    store.archive_evolutions = AsyncMock(return_value="evolutions.v1.json")
    store.load_evolution_log = AsyncMock(return_value=Mock(entries=[high, low, skipped]))
    store.clear_evolutions = AsyncMock()
    rebuild_service = ExperienceRebuildService(store=store)

    result = asyncio.run(
        rebuild_service.prepare_rebuild_context(
            {"kind": "skill", "name": "skill-a"}, user_intent="optimize", min_score=0.5
        )
    )

    assert result is not None
    assert result["subject"] == {"kind": "skill", "name": "skill-a"}
    assert result["user_intent"] == "optimize"
    assert result["records"][0]["content"] == "good experience"
    assert all(item["content"] != "bad experience" for item in result["records"])
    assert all(item["content"] != "skipped experience" for item in result["records"])
    store.skill_exists.assert_called_once_with("skill-a")
    store.archive_skill_body.assert_awaited_once_with("skill-a")
    store.archive_evolutions.assert_awaited_once_with("skill-a")
    store.load_evolution_log.assert_awaited_once_with("skill-a")
    store.clear_evolutions.assert_not_called()


def test_prepare_rebuild_context_does_not_clear_when_evolution_archive_missing():
    record = _make_record("good experience", score=0.8)
    store = Mock()
    store.skill_exists.return_value = True
    store.archive_skill_body = AsyncMock(side_effect=RuntimeError("body archive failed"))
    store.archive_evolutions = AsyncMock(return_value=None)
    store.load_evolution_log = AsyncMock(return_value=Mock(entries=[record]))
    store.clear_evolutions = AsyncMock()
    rebuild_service = ExperienceRebuildService(store=store)

    result = asyncio.run(rebuild_service.prepare_rebuild_context({"kind": "skill", "name": "skill-a"}))

    assert result is not None
    store.clear_evolutions.assert_not_called()


def test_prepare_rebuild_context_uses_subject_envelope():
    record = _make_record("good experience", score=0.8)
    store = Mock()
    store.skill_exists.return_value = True
    store.archive_skill_body = AsyncMock(return_value="SKILL.v1.md")
    store.archive_evolutions = AsyncMock(return_value="evolutions.v1.json")
    store.load_evolution_log = AsyncMock(return_value=Mock(entries=[record]))
    store.clear_evolutions = AsyncMock()

    rebuild_service = ExperienceRebuildService(store=store)

    result = asyncio.run(
        rebuild_service.prepare_rebuild_context(
            {"kind": "team-skill", "name": "team-skill-a", "scope": {}},
            min_score=0.5,
        )
    )

    assert result is not None
    assert result["subject"] == {"kind": "swarm-skill", "name": "team-skill-a", "scope": {}}
    store.skill_exists.assert_called_once_with("team-skill-a")
    store.archive_skill_body.assert_awaited_once_with("team-skill-a")
    store.archive_evolutions.assert_awaited_once_with("team-skill-a")
    store.load_evolution_log.assert_awaited_once_with("team-skill-a")
    store.clear_evolutions.assert_not_called()


def test_complete_rebuild_clears_when_archive_path_present():
    store = Mock()
    store.clear_evolutions = AsyncMock()
    rebuild_service = ExperienceRebuildService(store=store)

    cleared = asyncio.run(
        rebuild_service.complete_rebuild(
            {"skill_name": "skill-a", "archive_path": "evolutions.v1.json"},
        )
    )

    assert cleared is True
    store.clear_evolutions.assert_awaited_once_with("skill-a")


def test_complete_rebuild_skips_when_archive_path_missing():
    store = Mock()
    store.clear_evolutions = AsyncMock()
    rebuild_service = ExperienceRebuildService(store=store)

    cleared = asyncio.run(
        rebuild_service.complete_rebuild({"skill_name": "skill-a", "archive_path": None}),
    )

    assert cleared is False
    store.clear_evolutions.assert_not_called()

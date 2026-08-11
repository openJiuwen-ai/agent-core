# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from openjiuwen.agent_evolving.checkpointing import EvolutionStore
from openjiuwen.agent_evolving.checkpointing.changelog import ClassifiedChangelogEntry
from openjiuwen.agent_evolving.checkpointing.types import EvolutionPatch, EvolutionRecord, EvolutionTarget
from openjiuwen.agent_evolving.experience.rebuild import ExperienceRebuildService


def _make_record(
    content: str,
    *,
    score: float = 0.8,
    source: str = "test",
    section: str = "Troubleshooting",
) -> EvolutionRecord:
    record = EvolutionRecord.make(
        source=source,
        context="ctx",
        summary="summary",
        change=EvolutionPatch(
            section=section,
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
    pair.version = "v1.0.0"
    pair.evolution_archive_name = "evolutions.v1.0.0.json"
    pair.to_payload.return_value = {
        "version": "v1.0.0",
        "skill_archive": "SKILL.v1.0.0.md",
        "evolution_archive": "evolutions.v1.0.0.json",
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
async def test_prepare_rebuild_context_archives_filters_without_clearing():
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
    assert result["archive_path"] == "evolutions.v1.0.0.json"
    assert result["archive_version"] == "v1.0.0"
    assert result["archive_pair"]["skill_archive"] == "SKILL.v1.0.0.md"
    assert all(item["content"] != "bad experience" for item in result["records"])
    store.skill_exists.assert_called_once_with("skill-a", subject_kind="skill")
    store.load_full_evolution_log.assert_awaited_once_with("skill-a", subject_kind="skill")
    archive_service.archive_current_pair.assert_awaited_once_with("skill-a", subject_kind="skill")
    store.clear_evolutions.assert_not_called()
    archive_service.prune.assert_not_called()


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
    store.clear_evolutions.assert_not_called()


@pytest.mark.asyncio
async def test_complete_rebuild_skips_when_archive_error():
    store = Mock()
    store.bump_version_for_rebuild = AsyncMock()
    store.clear_evolutions = AsyncMock()
    rebuild_service = ExperienceRebuildService(store=store)

    cleared = await rebuild_service.complete_rebuild(
        {"skill_name": "skill-a", "archive_error": RuntimeError("boom")}
    )

    assert cleared is False
    store.bump_version_for_rebuild.assert_not_called()
    store.clear_evolutions.assert_not_called()


@pytest.mark.asyncio
async def test_complete_rebuild_bumps_patch_and_clears(tmp_path: Path):
    root = tmp_path / "skills"
    skill_dir = root / "skill-a"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: skill-a\nversion: v1.0.0\n---\n\n# Skill\n",
        encoding="utf-8",
    )
    store = EvolutionStore(str(root))
    record = _make_record("fix tip", source="execution_failure", section="Examples")
    evo_log = await store.load_full_evolution_log("skill-a")
    evo_log.entries = [record]
    evo_log.version = "v1.0.0"
    await store.save_evolution_log("skill-a", evo_log, skill_dir=skill_dir)

    rebuild_service = ExperienceRebuildService(store=store, classify_fn=lambda entries: [
        ClassifiedChangelogEntry(id=entries[0].id, category="Fixed", summary="fix tip")
    ])
    cleared = await rebuild_service.complete_rebuild(
        {"skill_name": "skill-a", "subject_kind": "skill", "archive_path": "evolutions.v1.0.0.json"}
    )

    assert cleared is True
    content = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    assert "version: v1.0.1" in content
    log = json.loads((skill_dir / "evolutions.json").read_text(encoding="utf-8"))
    assert log["entries"] == []
    assert log["version"] == "v1.0.1"
    changelog = (skill_dir / "changelog.md").read_text(encoding="utf-8")
    assert "## [v1.0.1]" in changelog or "## [1.0.1]" in changelog or "v1.0.1" in changelog


@pytest.mark.asyncio
async def test_complete_rebuild_bumps_minor_when_any_instruction(tmp_path: Path):
    root = tmp_path / "skills"
    skill_dir = root / "skill-a"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: skill-a\nversion: v1.0.0\n---\n\n# Skill\n",
        encoding="utf-8",
    )
    store = EvolutionStore(str(root))
    patch = _make_record("patch tip", source="execution_failure", section="Examples")
    minor = _make_record("new rule", source="user_feedback", section="Instructions")
    evo_log = await store.load_full_evolution_log("skill-a")
    evo_log.entries = [patch, minor]
    evo_log.version = "v1.0.0"
    await store.save_evolution_log("skill-a", evo_log, skill_dir=skill_dir)

    rebuild_service = ExperienceRebuildService(store=store)
    cleared = await rebuild_service.complete_rebuild(
        {"skill_name": "skill-a", "subject_kind": "skill"}
    )

    assert cleared is True
    content = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    assert "version: v1.1.0" in content

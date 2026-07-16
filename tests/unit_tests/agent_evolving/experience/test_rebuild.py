# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from openjiuwen.agent_evolving.checkpointing import EvolutionStore
from openjiuwen.agent_evolving.checkpointing.types import (
    EvolutionPatch,
    EvolutionRecord,
    EvolutionRecordSpec,
)
from openjiuwen.agent_evolving.experience.rebuild import ExperienceRebuildService
from openjiuwen.agent_evolving.signal.base import EvolutionTarget


def _make_record(content: str, *, score: float = 0.8, skip_reason: str | None = None) -> EvolutionRecord:
    record = EvolutionRecord.make(
        EvolutionRecordSpec(
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
    store.archive_current_state.assert_not_called()


def test_prepare_rebuild_context_archives_filters_and_keeps_evolution_log():
    high = _make_record("good experience", score=0.8)
    low = _make_record("bad experience", score=0.3)
    skipped = _make_record("skipped experience", score=0.9, skip_reason="duplicate")
    store = Mock()
    store.skill_exists.return_value = True
    store.archive_current_state = AsyncMock(return_value=("SKILL.1.0.0.md", "evolutions.1.0.0.json"))
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
    store.archive_current_state.assert_awaited_once_with("skill-a")
    store.load_evolution_log.assert_awaited_once_with("skill-a")
    store.clear_evolutions.assert_not_called()


def test_prepare_rebuild_context_does_not_clear_when_evolution_archive_missing():
    record = _make_record("good experience", score=0.8)
    store = Mock()
    store.skill_exists.return_value = True
    store.archive_current_state = AsyncMock(side_effect=RuntimeError("archive failed"))
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
    store.archive_current_state = AsyncMock(return_value=("SKILL.1.0.0.md", "evolutions.1.0.0.json"))
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
    store.archive_current_state.assert_awaited_once_with("team-skill-a")
    store.load_evolution_log.assert_awaited_once_with("team-skill-a")
    store.clear_evolutions.assert_not_called()


def test_complete_rebuild_clears_when_archive_path_present():
    store = Mock()
    store.bump_version_for_rebuild = AsyncMock(return_value="1.0.1")
    store.clear_evolutions = AsyncMock()
    rebuild_service = ExperienceRebuildService(store=store)

    cleared = asyncio.run(
        rebuild_service.complete_rebuild(
            {"skill_name": "skill-a", "archive_path": "evolutions.1.0.0.json"},
        )
    )

    assert cleared is True
    store.bump_version_for_rebuild.assert_awaited_once_with("skill-a")
    store.clear_evolutions.assert_awaited_once_with("skill-a", retain_version="1.0.1")


def test_complete_rebuild_skips_when_archive_path_missing():
    store = Mock()
    store.bump_version_for_rebuild = AsyncMock()
    store.clear_evolutions = AsyncMock()
    rebuild_service = ExperienceRebuildService(store=store)

    cleared = asyncio.run(
        rebuild_service.complete_rebuild({"skill_name": "skill-a", "archive_path": None}),
    )

    assert cleared is False
    store.bump_version_for_rebuild.assert_not_called()
    store.clear_evolutions.assert_not_called()


@pytest.mark.asyncio
async def test_complete_rebuild_bumps_patch_from_all_entries(tmp_path: Path):
    root = tmp_path / "skills"
    skill_dir = root / "skill-a"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: skill-a\ndescription: d\nversion: 1.0.0\n---\n\n# Skill\n",
        encoding="utf-8",
    )
    store = EvolutionStore(str(root))
    await store.append_record(
        "skill-a",
        EvolutionRecord.make(
            source="execution_failure",
            context="ctx",
            change=EvolutionPatch(
                section="Troubleshooting",
                action="append",
                content="fix",
                target=EvolutionTarget.BODY,
            ),
        ),
    )
    rebuild_service = ExperienceRebuildService(store=store)

    cleared = await rebuild_service.complete_rebuild(
        {"skill_name": "skill-a", "archive_path": "evolutions.1.0.0.json"},
    )

    assert cleared is True
    evo_log = await store.load_full_evolution_log("skill-a")
    assert evo_log.entries == []
    assert evo_log.version == "1.0.1"
    skill_md = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    assert "version: 1.0.1" in skill_md


@pytest.mark.asyncio
async def test_complete_rebuild_bumps_minor_when_any_instruction(tmp_path: Path):
    root = tmp_path / "skills"
    skill_dir = root / "skill-a"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nversion: 1.0.5\n---\n\n# Skill\n",
        encoding="utf-8",
    )
    store = EvolutionStore(str(root))
    await store.append_record(
        "skill-a",
        EvolutionRecord.make(
            source="execution_failure",
            context="ctx",
            change=EvolutionPatch(
                section="Troubleshooting",
                action="append",
                content="patch",
                target=EvolutionTarget.BODY,
            ),
        ),
    )
    await store.append_record(
        "skill-a",
        EvolutionRecord.make(
            source="user_correction",
            context="ctx",
            change=EvolutionPatch(
                section="Instructions",
                action="append",
                content="minor",
                target=EvolutionTarget.BODY,
            ),
        ),
    )
    rebuild_service = ExperienceRebuildService(store=store)

    cleared = await rebuild_service.complete_rebuild(
        {"skill_name": "skill-a", "archive_path": "evolutions.1.0.5.json"},
    )

    assert cleared is True
    evo_log = await store.load_full_evolution_log("skill-a")
    assert evo_log.version == "1.1.0"
    assert "version: 1.1.0" in (skill_dir / "SKILL.md").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_complete_rebuild_no_bump_when_no_entries(tmp_path: Path):
    root = tmp_path / "skills"
    skill_dir = root / "skill-a"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nversion: 2.0.0\n---\n\n# Skill\n",
        encoding="utf-8",
    )
    store = EvolutionStore(str(root))
    rebuild_service = ExperienceRebuildService(store=store)

    cleared = await rebuild_service.complete_rebuild(
        {"skill_name": "skill-a", "archive_path": "evolutions.2.0.0.json"},
    )

    assert cleared is True
    evo_log = await store.load_full_evolution_log("skill-a")
    assert evo_log.entries == []
    assert evo_log.version == "2.0.0"
    assert "version: 2.0.0" in (skill_dir / "SKILL.md").read_text(encoding="utf-8")


def test_prepare_rebuild_context_injects_resolved_paths_for_external_skill(tmp_path: Path):
    external_root = tmp_path / "external-skills"
    builtin_root = tmp_path / "builtin-skills"
    external_root.mkdir()
    builtin_root.mkdir()
    skill_dir = external_root / "downloaded-skill"
    skill_dir.mkdir()
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text("# Downloaded\n", encoding="utf-8")

    store = EvolutionStore([str(external_root), str(builtin_root)])
    rebuild_service = ExperienceRebuildService(store=store)

    result = asyncio.run(
        rebuild_service.prepare_rebuild_context({"kind": "skill", "name": "downloaded-skill"}),
    )

    assert result is not None
    assert result["skill_md_path"] == str(skill_md.resolve())
    assert result["skills_base"] == str(external_root.resolve())
    archive_dir = skill_dir / "archive"
    assert archive_dir.is_dir()
    archived_bodies = list(archive_dir.glob("SKILL.*.md"))
    assert len(archived_bodies) == 1
    assert archived_bodies[0].name == "SKILL.1.0.0.md"
    assert archived_bodies[0].read_text(encoding="utf-8") == "# Downloaded\n"

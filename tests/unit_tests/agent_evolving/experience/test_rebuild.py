# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
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
    store.archive_current_state = AsyncMock(return_value=("SKILL.v1.0.0.md", "evolutions.v1.0.0.json"))
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
    store.archive_current_state = AsyncMock(return_value=("SKILL.v1.0.0.md", "evolutions.v1.0.0.json"))
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
    store.load_evolution_log = AsyncMock(return_value=Mock(entries=[]))
    store.append_changelog_for_rebuild = AsyncMock(return_value=True)
    store.clear_evolutions = AsyncMock()
    rebuild_service = ExperienceRebuildService(store=store)

    cleared = asyncio.run(
        rebuild_service.complete_rebuild(
            {"skill_name": "skill-a", "archive_path": "evolutions.v1.0.0.json"},
        )
    )

    assert cleared is True
    store.bump_version_for_rebuild.assert_awaited_once_with("skill-a", entries=[])
    store.append_changelog_for_rebuild.assert_awaited_once()
    store.clear_evolutions.assert_awaited_once_with("skill-a", retain_version="1.0.1")


def test_complete_rebuild_proceeds_when_archive_path_missing():
    """Archive skip (already exists) must not permanently block bump/clear."""
    store = Mock()
    store.bump_version_for_rebuild = AsyncMock(return_value="1.0.1")
    store.load_evolution_log = AsyncMock(return_value=Mock(entries=[]))
    store.append_changelog_for_rebuild = AsyncMock(return_value=True)
    store.clear_evolutions = AsyncMock()
    rebuild_service = ExperienceRebuildService(store=store)

    cleared = asyncio.run(
        rebuild_service.complete_rebuild({"skill_name": "skill-a", "archive_path": None}),
    )

    assert cleared is True
    store.bump_version_for_rebuild.assert_awaited_once_with("skill-a", entries=[])
    store.clear_evolutions.assert_awaited_once_with("skill-a", retain_version="1.0.1")


def test_complete_rebuild_skips_when_archive_error():
    store = Mock()
    store.bump_version_for_rebuild = AsyncMock()
    store.append_changelog_for_rebuild = AsyncMock()
    store.clear_evolutions = AsyncMock()
    rebuild_service = ExperienceRebuildService(store=store)

    cleared = asyncio.run(
        rebuild_service.complete_rebuild(
            {
                "skill_name": "skill-a",
                "archive_path": None,
                "archive_error": RuntimeError("archive failed"),
            },
        ),
    )

    assert cleared is False
    store.bump_version_for_rebuild.assert_not_called()
    store.append_changelog_for_rebuild.assert_not_called()
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
            EvolutionRecordSpec(
                source="execution_failure",
                context="ctx",
                change=EvolutionPatch(
                    section="Troubleshooting",
                    action="append",
                    content="fix",
                    target=EvolutionTarget.BODY,
                ),
            )
        ),
    )
    rebuild_service = ExperienceRebuildService(store=store)

    cleared = await rebuild_service.complete_rebuild(
        {"skill_name": "skill-a", "archive_path": "evolutions.v1.0.0.json"},
    )

    assert cleared is True
    evo_log = await store.load_full_evolution_log("skill-a")
    assert evo_log.entries == []
    assert evo_log.version == "1.0.1"
    skill_md = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    assert "version: 1.0.1" in skill_md
    changelog = (skill_dir / "changelog.md").read_text(encoding="utf-8")
    assert "## [1.0.1]" in changelog
    assert "Unreleased" not in changelog
    assert "关联经验" in changelog


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
            EvolutionRecordSpec(
                source="execution_failure",
                context="ctx",
                change=EvolutionPatch(
                    section="Troubleshooting",
                    action="append",
                    content="patch",
                    target=EvolutionTarget.BODY,
                ),
            )
        ),
    )
    await store.append_record(
        "skill-a",
        EvolutionRecord.make(
            EvolutionRecordSpec(
                source="user_correction",
                context="ctx",
                change=EvolutionPatch(
                    section="Instructions",
                    action="append",
                    content="minor",
                    target=EvolutionTarget.BODY,
                ),
            )
        ),
    )
    rebuild_service = ExperienceRebuildService(store=store)

    cleared = await rebuild_service.complete_rebuild(
        {"skill_name": "skill-a", "archive_path": "evolutions.v1.0.5.json"},
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
        {"skill_name": "skill-a", "archive_path": "evolutions.v2.0.0.json"},
    )

    assert cleared is True
    evo_log = await store.load_full_evolution_log("skill-a")
    assert evo_log.entries == []
    assert evo_log.version == "2.0.0"
    assert "version: 2.0.0" in (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    assert not (skill_dir / "changelog.md").exists()


@pytest.mark.asyncio
async def test_complete_rebuild_uses_llm_classification(tmp_path: Path):
    root = tmp_path / "skills"
    skill_dir = root / "skill-a"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: skill-a\ndescription: d\nversion: 1.0.0\n---\n\n# Skill\n",
        encoding="utf-8",
    )
    store = EvolutionStore(str(root))
    record = EvolutionRecord.make(
        EvolutionRecordSpec(
            source="execution_failure",
            context="ctx",
            change=EvolutionPatch(
                section="Troubleshooting",
                action="append",
                content="timeout fallback broken",
                target=EvolutionTarget.BODY,
            ),
        )
    )
    await store.append_record("skill-a", record)

    llm = AsyncMock()
    llm.invoke = AsyncMock(
        return_value=SimpleNamespace(
            content=json.dumps(
                [
                    {
                        "id": record.id,
                        "category": "Fixed",
                        "summary": "修复工具调用超时后回退逻辑失效问题",
                    }
                ],
                ensure_ascii=False,
            )
        )
    )
    rebuild_service = ExperienceRebuildService(store=store, llm=llm, model="test-model")

    cleared = await rebuild_service.complete_rebuild(
        {"skill_name": "skill-a", "archive_path": "evolutions.v1.0.0.json"},
    )

    assert cleared is True
    llm.invoke.assert_awaited()
    changelog = (skill_dir / "changelog.md").read_text(encoding="utf-8")
    assert "### Fixed" in changelog
    assert "修复工具调用超时后回退逻辑失效问题" in changelog
    assert f"(关联经验 {record.id})" in changelog
    assert "Unreleased" not in changelog


@pytest.mark.asyncio
async def test_complete_rebuild_changelog_idempotent_same_version(tmp_path: Path):
    root = tmp_path / "skills"
    skill_dir = root / "skill-a"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nversion: 1.0.0\n---\n\n# Skill\n",
        encoding="utf-8",
    )
    store = EvolutionStore(str(root))
    await store.append_record(
        "skill-a",
        EvolutionRecord.make(
            EvolutionRecordSpec(
                source="execution_failure",
                context="ctx",
                change=EvolutionPatch(
                    section="Troubleshooting",
                    action="append",
                    content="fix once",
                    target=EvolutionTarget.BODY,
                ),
            )
        ),
    )
    rebuild_service = ExperienceRebuildService(store=store)
    await rebuild_service.complete_rebuild(
        {"skill_name": "skill-a", "archive_path": "evolutions.v1.0.0.json"},
    )
    first = (skill_dir / "changelog.md").read_text(encoding="utf-8")
    assert first.count("## [1.0.1]") == 1

    # Force rewrite attempt for same version with empty entries (no bump path).
    wrote = await store.append_changelog_for_rebuild(
        "skill-a",
        "1.0.1",
        [],
    )
    assert wrote is False
    second = (skill_dir / "changelog.md").read_text(encoding="utf-8")
    assert second == first


def test_prepare_rebuild_context_whitelist_keeps_low_score_and_skip_reason():
    high = _make_record("good experience", score=0.8)
    low = _make_record("bad experience", score=0.3)
    skipped = _make_record("skipped experience", score=0.9, skip_reason="duplicate")
    other = _make_record("other experience", score=0.9)
    low.id = "ev_low"
    skipped.id = "ev_skipped"
    high.id = "ev_high"
    other.id = "ev_other"
    store = Mock()
    store.skill_exists.return_value = True
    store.archive_current_state = AsyncMock(return_value=("SKILL.v1.0.0.md", "evolutions.v1.0.0.json"))
    store.load_evolution_log = AsyncMock(return_value=Mock(entries=[high, low, skipped, other]))
    rebuild_service = ExperienceRebuildService(store=store)

    result = asyncio.run(
        rebuild_service.prepare_rebuild_context(
            {"kind": "skill", "name": "skill-a"},
            min_score=0.5,
            record_ids=["ev_low", "ev_skipped"],
        )
    )

    assert result is not None
    assert result["record_ids"] == ["ev_low", "ev_skipped"]
    contents = [item["content"] for item in result["records"]]
    assert set(contents) == {"bad experience", "skipped experience"}
    assert contents[0] == "skipped experience"
    assert all(item["content"] != "good experience" for item in result["records"])
    assert all(item["content"] != "other experience" for item in result["records"])


def test_prepare_rebuild_context_empty_record_ids_uses_score_filters():
    high = _make_record("good experience", score=0.8)
    low = _make_record("bad experience", score=0.3)
    store = Mock()
    store.skill_exists.return_value = True
    store.archive_current_state = AsyncMock(return_value=("SKILL.v1.0.0.md", "evolutions.v1.0.0.json"))
    store.load_evolution_log = AsyncMock(return_value=Mock(entries=[high, low]))
    rebuild_service = ExperienceRebuildService(store=store)

    result = asyncio.run(
        rebuild_service.prepare_rebuild_context(
            {"kind": "skill", "name": "skill-a"},
            min_score=0.5,
            record_ids=["  ", ""],
        )
    )

    assert result is not None
    assert "record_ids" not in result
    assert [item["content"] for item in result["records"]] == ["good experience"]


@pytest.mark.asyncio
async def test_complete_rebuild_whitelist_bumps_from_selected_then_clears_all(tmp_path: Path):
    root = tmp_path / "skills"
    skill_dir = root / "skill-a"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nversion: 1.0.0\n---\n\n# Skill\n",
        encoding="utf-8",
    )
    store = EvolutionStore(str(root))
    patch_record = EvolutionRecord.make(
        EvolutionRecordSpec(
            source="execution_failure",
            context="ctx",
            change=EvolutionPatch(
                section="Troubleshooting",
                action="append",
                content="patch only",
                target=EvolutionTarget.BODY,
            ),
        )
    )
    minor_record = EvolutionRecord.make(
        EvolutionRecordSpec(
            source="user_correction",
            context="ctx",
            change=EvolutionPatch(
                section="Instructions",
                action="append",
                content="would bump minor",
                target=EvolutionTarget.BODY,
            ),
        )
    )
    await store.append_record("skill-a", patch_record)
    await store.append_record("skill-a", minor_record)
    rebuild_service = ExperienceRebuildService(store=store)

    cleared = await rebuild_service.complete_rebuild(
        {
            "skill_name": "skill-a",
            "archive_path": "evolutions.v1.0.0.json",
            "record_ids": [patch_record.id],
            "min_score": 0.5,
        },
    )

    assert cleared is True
    evo_log = await store.load_full_evolution_log("skill-a")
    assert evo_log.entries == []
    assert evo_log.version == "1.0.1"
    assert "version: 1.0.1" in (skill_dir / "SKILL.md").read_text(encoding="utf-8")


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
    assert archived_bodies[0].name == "SKILL.v1.0.0.md"
    assert archived_bodies[0].read_text(encoding="utf-8") == "# Downloaded\n"

# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from openjiuwen.agent_evolving.checkpointing import EvolutionStore
from openjiuwen.agent_evolving.checkpointing.changelog import ClassifiedChangelogEntry
from openjiuwen.agent_evolving.checkpointing.store_archive import StoreArchiveHelper
from openjiuwen.agent_evolving.checkpointing.types import EvolutionPatch, EvolutionRecord, EvolutionTarget
from openjiuwen.agent_evolving.experience.rebuild import ExperienceRebuildService
from openjiuwen.agent_evolving.utils import split_markdown_frontmatter


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
    assert isinstance(result.get("entries_snapshot"), list)
    assert len(result["entries_snapshot"]) == 2
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
    assert "version: 1.0.1" in content
    log = json.loads((skill_dir / "evolutions.json").read_text(encoding="utf-8"))
    assert log["entries"] == []
    assert log["version"] == "1.0.1"
    changelog = (skill_dir / "changelog.md").read_text(encoding="utf-8")
    assert "## [1.0.1]" in changelog
    # Archive filenames still use the v-prefixed key.
    assert StoreArchiveHelper.archive_version_key("1.0.1") == "v1.0.1"


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
    assert "version: 1.1.0" in content


@pytest.mark.asyncio
async def test_set_skill_md_version_keeps_body_horizontal_rule(tmp_path: Path):
    """Body Markdown ``---`` must not break frontmatter version writes."""
    root = tmp_path / "skills"
    skill_dir = root / "skill-a"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: skill-a\n"
        "version: v1.0.0\n"
        "---\n"
        "\n"
        "# Skill\n"
        "\n"
        "Before rule\n"
        "\n"
        "---\n"
        "\n"
        "After rule\n",
        encoding="utf-8",
    )
    store = EvolutionStore(str(root))
    await store._archive.set_skill_md_version(skill_dir, "v1.0.1")

    content = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    front, body = split_markdown_frontmatter(content)
    assert front is not None
    assert "version: v1.0.1" in front
    assert content.count("version:") == 1
    assert "Before rule" in body
    assert "After rule" in body
    assert "\n---\n" in body
    assert StoreArchiveHelper.extract_version_from_skill_md(content) == "v1.0.1"


def test_extract_version_prefers_frontmatter_over_body():
    content = (
        "---\n"
        "name: skill-a\n"
        "version: v2.0.0\n"
        "---\n"
        "\n"
        "# Skill\n"
        "\n"
        "version: v9.9.9\n"
    )
    assert StoreArchiveHelper.extract_version_from_skill_md(content) == "v2.0.0"


def test_extract_version_from_body_when_frontmatter_missing_version():
    content = (
        "---\n"
        "name: skill-a\n"
        "---\n"
        "\n"
        "# Skill\n"
        "\n"
        "version: v1.2.3\n"
    )
    assert StoreArchiveHelper.extract_version_from_skill_md(content) == "v1.2.3"


def test_extract_version_from_full_doc_without_frontmatter():
    content = "# Skill\n\nversion: \"v3.1.0\"\n"
    assert StoreArchiveHelper.extract_version_from_skill_md(content) == "v3.1.0"


def test_extract_version_returns_none_when_absent():
    content = "---\nname: skill-a\n---\n\n# Skill\n"
    assert StoreArchiveHelper.extract_version_from_skill_md(content) is None


@pytest.mark.asyncio
async def test_resolve_current_version_reads_body_without_writing_frontmatter(tmp_path: Path):
    root = tmp_path / "skills"
    skill_dir = root / "skill-a"
    skill_dir.mkdir(parents=True)
    original = (
        "---\n"
        "name: skill-a\n"
        "---\n"
        "\n"
        "# Skill\n"
        "\n"
        "version: v1.2.3\n"
    )
    (skill_dir / "SKILL.md").write_text(original, encoding="utf-8")
    store = EvolutionStore(str(root))

    version = await store.resolve_current_version("skill-a")

    assert version == "v1.2.3"
    content = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    front, _ = split_markdown_frontmatter(content)
    assert front is not None
    assert "version:" not in front
    assert content == original


@pytest.mark.asyncio
async def test_resolve_current_version_writes_default_when_missing(tmp_path: Path):
    root = tmp_path / "skills"
    skill_dir = root / "skill-a"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: skill-a\n"
        "---\n"
        "\n"
        "# Skill\n",
        encoding="utf-8",
    )
    store = EvolutionStore(str(root))

    version = await store.resolve_current_version("skill-a")

    assert version == "1.0.0"
    content = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    front, body = split_markdown_frontmatter(content)
    assert front is not None
    assert "version: 1.0.0" in front
    assert "version: v1.0.0" not in front
    assert "# Skill" in body
    assert StoreArchiveHelper.archive_version_key(version) == "v1.0.0"


@pytest.mark.asyncio
async def test_complete_rebuild_bumps_from_snapshot_when_live_cleared(tmp_path: Path):
    """Agent clearing evolutions.json before finalize must not skip bump/changelog."""
    root = tmp_path / "skills"
    skill_dir = root / "skill-a"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: skill-a\nversion: 1.0.0\n---\n\n# Skill\n",
        encoding="utf-8",
    )
    store = EvolutionStore(str(root))
    record = _make_record("fix tip", source="execution_failure", section="Examples")
    evo_log = await store.load_full_evolution_log("skill-a")
    evo_log.entries = [record]
    evo_log.version = "1.0.0"
    await store.save_evolution_log("skill-a", evo_log, skill_dir=skill_dir)

    rebuild_service = ExperienceRebuildService(
        store=store,
        classify_fn=lambda entries: [
            ClassifiedChangelogEntry(id=entries[0].id, category="Fixed", summary="fix tip")
        ],
    )
    context = await rebuild_service.prepare_rebuild_context(
        {"kind": "skill", "name": "skill-a"},
        min_score=0.5,
    )
    assert context is not None
    assert context["entries_snapshot"]

    # Simulate Agent incorrectly clearing live evolutions before complete_rebuild.
    empty = await store.load_full_evolution_log("skill-a")
    empty.entries = []
    await store.save_evolution_log("skill-a", empty, skill_dir=skill_dir)

    cleared = await rebuild_service.complete_rebuild(context)

    assert cleared is True
    content = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    assert "version: 1.0.1" in content
    log = json.loads((skill_dir / "evolutions.json").read_text(encoding="utf-8"))
    assert log["entries"] == []
    assert log["version"] == "1.0.1"
    changelog = (skill_dir / "changelog.md").read_text(encoding="utf-8")
    assert "## [1.0.1]" in changelog

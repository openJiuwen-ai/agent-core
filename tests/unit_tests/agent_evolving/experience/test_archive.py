# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from openjiuwen.agent_evolving.checkpointing import EvolutionStore
from openjiuwen.agent_evolving.checkpointing.types import (
    EvolutionLog,
    EvolutionPatch,
    EvolutionRecord,
    EvolutionTarget,
)
from openjiuwen.agent_evolving.experience.archive import EvolutionArchiveService


def _prepare_skill(root: Path, name: str, content: str = "# Skill\n", *, version: str = "v1.0.0") -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\nversion: {version}\n---\n\n{content}",
        encoding="utf-8",
    )
    return skill_dir


def _write_pair(
    skill_dir: Path,
    version: str,
    *,
    skill_content: str = "# Archived\n",
    evolution_entries: list[EvolutionRecord] | None = None,
) -> None:
    archive_dir = skill_dir / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    (archive_dir / f"SKILL.{version}.md").write_text(skill_content, encoding="utf-8")
    log = EvolutionLog.empty("skill-a")
    log.version = version
    if evolution_entries is not None:
        log.entries = list(evolution_entries)
    (archive_dir / f"evolutions.{version}.json").write_text(
        json.dumps(log.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _make_record(record_id: str, content: str = "tip") -> EvolutionRecord:
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
        ),
    )


@pytest.mark.asyncio
async def test_archive_current_pair_uses_semver_and_creates_empty_log(tmp_path: Path):
    root = tmp_path / "skills"
    skill_dir = _prepare_skill(root, "skill-a", "# Current Skill\n", version="v1.2.3")
    store = EvolutionStore(str(root))
    service = EvolutionArchiveService(store=store)

    pair = await service.archive_current_pair({"kind": "skill", "name": "skill-a"})

    assert pair is not None
    assert pair.version == "v1.2.3"
    assert pair.skill_archive_name == "SKILL.v1.2.3.md"
    assert pair.evolution_archive_name == "evolutions.v1.2.3.json"
    assert "# Current Skill\n" in pair.skill_archive.read_text(encoding="utf-8")
    current_log = json.loads((skill_dir / "evolutions.json").read_text(encoding="utf-8"))
    archived_log = json.loads(pair.evolution_archive.read_text(encoding="utf-8"))
    assert current_log["entries"] == []
    assert archived_log["entries"] == []


@pytest.mark.asyncio
async def test_archive_current_pair_writes_empty_evo_and_keeps_live_entries(tmp_path: Path):
    root = tmp_path / "skills"
    skill_dir = _prepare_skill(root, "skill-a", "# Current Skill\n", version="v1.2.3")
    store = EvolutionStore(str(root))
    await store.append_record("skill-a", _make_record("ev_1", content="first"))
    live_body = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    service = EvolutionArchiveService(store=store)

    pair = await service.archive_current_pair("skill-a")

    assert pair is not None
    assert pair.skill_archive.read_text(encoding="utf-8") == live_body
    archived_log = json.loads(pair.evolution_archive.read_text(encoding="utf-8"))
    assert archived_log.get("entries") == []
    assert archived_log.get("skill_id") == "skill-a"
    assert archived_log.get("version") == "v1.2.3"
    live = await store.load_full_evolution_log("skill-a")
    assert len(live.entries) == 1
    assert live.entries[0].id == "ev_1"


@pytest.mark.asyncio
async def test_archive_current_pair_is_idempotent_for_same_version(tmp_path: Path):
    root = tmp_path / "skills"
    _prepare_skill(root, "skill-a", "# Current\n", version="v1.0.0")
    store = EvolutionStore(str(root))
    service = EvolutionArchiveService(store=store)

    first = await service.archive_current_pair("skill-a")
    second = await service.archive_current_pair("skill-a")

    assert first is not None
    assert second is not None
    assert first.version == second.version == "v1.0.0"
    assert first.skill_archive == second.skill_archive


def test_list_pairs_ignores_non_semver_and_normalizes_versions(tmp_path: Path):
    root = tmp_path / "skills"
    skill_dir = _prepare_skill(root, "skill-a")
    archive_dir = skill_dir / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    _write_pair(skill_dir, "v1.0.0")
    time.sleep(0.01)
    _write_pair(skill_dir, "v1.0.1")
    (archive_dir / "SKILL.v20260102T000000.md").write_text("# orphan skill\n", encoding="utf-8")
    (archive_dir / "evolutions.v20260103T000000.json").write_text("{}", encoding="utf-8")
    (archive_dir / "SKILL.not-a-version.md").write_text("# ignored\n", encoding="utf-8")
    store = EvolutionStore(str(root))
    service = EvolutionArchiveService(store=store)

    pairs = service.list_pairs("skill-a")

    assert [pair.version for pair in pairs] == ["v1.0.1", "v1.0.0"]
    assert service.normalize_version("latest") == "latest"
    assert service.normalize_version("SKILL.v1.0.1.md") == "v1.0.1"
    assert service.normalize_version("1.0.1") == "v1.0.1"
    assert service.normalize_version("v1.0.1") == "v1.0.1"
    assert service.normalize_version("20260101T000001") is None
    assert service.normalize_version("v20260101T000001") is None


@pytest.mark.asyncio
async def test_rollback_to_latest_archives_current_state_and_clears_live_evolutions(tmp_path: Path):
    root = tmp_path / "skills"
    skill_dir = _prepare_skill(root, "skill-a", "# Current\n", version="v1.2.0")
    store = EvolutionStore(str(root))
    await store.append_record("skill-a", _make_record("ev_live", content="live tip"))
    _write_pair(
        skill_dir,
        "v1.0.0",
        skill_content="# Older\n",
        evolution_entries=[_make_record("ev_old")],
    )
    time.sleep(0.01)
    _write_pair(
        skill_dir,
        "v1.1.0",
        skill_content="# Target\n",
        evolution_entries=[_make_record("ev_archived")],
    )
    service = EvolutionArchiveService(store=store)

    restored = await service.rollback_to_pair("skill-a", "latest", prune=False)

    assert restored is True
    assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == "# Target\n"
    restored_log = await store.load_full_evolution_log("skill-a")
    assert restored_log.entries == []
    assert restored_log.skill_id == "skill-a"

    archive_dir = skill_dir / "archive"
    assert not (archive_dir / "SKILL.v1.1.0.md").exists()
    assert not (archive_dir / "evolutions.v1.1.0.json").exists()
    assert (archive_dir / "SKILL.v1.0.0.md").exists()
    assert (archive_dir / "evolutions.v1.0.0.json").exists()

    current_archives = [
        pair
        for pair in service.list_pairs("skill-a")
        if pair.version not in {"v1.0.0"}
    ]
    assert len(current_archives) == 1
    assert "# Current\n" in current_archives[0].skill_archive.read_text(encoding="utf-8")
    archived_current = json.loads(current_archives[0].evolution_archive.read_text(encoding="utf-8"))
    assert archived_current.get("entries") == []


def test_prune_removes_old_complete_pairs(tmp_path: Path):
    root = tmp_path / "skills"
    skill_dir = _prepare_skill(root, "skill-a")
    _write_pair(skill_dir, "v1.0.0")
    time.sleep(0.01)
    _write_pair(skill_dir, "v1.0.1")
    time.sleep(0.01)
    _write_pair(skill_dir, "v1.0.2")
    service = EvolutionArchiveService(store=EvolutionStore(str(root)))

    pruned = service.prune("skill-a", keep_latest=2)

    assert pruned == 1
    assert [pair.version for pair in service.list_pairs("skill-a")] == [
        "v1.0.2",
        "v1.0.1",
    ]
    archive_dir = skill_dir / "archive"
    assert not (archive_dir / "SKILL.v1.0.0.md").exists()
    assert not (archive_dir / "evolutions.v1.0.0.json").exists()


@pytest.mark.asyncio
async def test_rollback_to_specific_version_removes_target_pair(tmp_path: Path):
    root = tmp_path / "skills"
    skill_dir = _prepare_skill(root, "skill-a", "# Current\n", version="v1.2.0")
    store = EvolutionStore(str(root))
    await store.save_evolution_log("skill-a", EvolutionLog.empty("skill-a"), skill_dir=skill_dir)
    _write_pair(skill_dir, "v1.0.0", skill_content="# Older\n")
    _write_pair(skill_dir, "v1.1.0", skill_content="# Target\n")
    service = EvolutionArchiveService(store=store)

    restored = await service.rollback_to_pair("skill-a", "v1.0.0", prune=False)

    assert restored is True
    assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == "# Older\n"
    assert (await store.load_full_evolution_log("skill-a")).entries == []

    archive_dir = skill_dir / "archive"
    assert not (archive_dir / "SKILL.v1.0.0.md").exists()
    assert not (archive_dir / "evolutions.v1.0.0.json").exists()
    assert (archive_dir / "SKILL.v1.1.0.md").exists()
    assert (archive_dir / "evolutions.v1.1.0.json").exists()

    remaining = service.list_pairs("skill-a")
    assert len(remaining) == 2  # v1.1.0 + archived current v1.2.0


@pytest.mark.asyncio
async def test_rollback_does_not_rewrite_restored_skill_md_via_projection(tmp_path: Path):
    root = tmp_path / "skills"
    skill_dir = _prepare_skill(
        root,
        "skill-a",
        "# Current\n\n<!-- evolution-index-start -->\n- live\n<!-- evolution-index-end -->\n",
        version="v1.1.0",
    )
    store = EvolutionStore(str(root))
    await store.append_record("skill-a", _make_record("ev_live"))
    archived_body = (
        "# Archived\n\n<!-- evolution-index-start -->\n- archived\n<!-- evolution-index-end -->\n"
    )
    _write_pair(skill_dir, "v1.0.0", skill_content=archived_body)
    service = EvolutionArchiveService(store=store)

    restored = await service.rollback_to_pair("skill-a", "v1.0.0", prune=False)

    assert restored is True
    assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == archived_body
    assert (await store.load_full_evolution_log("skill-a")).entries == []

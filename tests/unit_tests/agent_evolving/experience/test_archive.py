# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openjiuwen.agent_evolving.checkpointing import EvolutionStore
from openjiuwen.agent_evolving.checkpointing.types import EvolutionLog
from openjiuwen.agent_evolving.experience.archive import EvolutionArchiveService


def _prepare_skill(root: Path, name: str, content: str = "# Skill\n") -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
    return skill_dir


def _write_pair(skill_dir: Path, version: str, *, skill_content: str = "# Archived\n") -> None:
    archive_dir = skill_dir / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    (archive_dir / f"SKILL.{version}.md").write_text(skill_content, encoding="utf-8")
    (archive_dir / f"evolutions.{version}.json").write_text(
        json.dumps(EvolutionLog.empty("skill-a").to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_archive_current_pair_creates_empty_evolution_log_when_missing(tmp_path: Path):
    root = tmp_path / "skills"
    skill_dir = _prepare_skill(root, "skill-a", "# Current Skill\n")
    store = EvolutionStore(str(root))
    service = EvolutionArchiveService(store=store)

    pair = await service.archive_current_pair({"kind": "skill", "name": "skill-a"})

    assert pair is not None
    assert pair.version.startswith("v")
    assert pair.skill_archive_name == f"SKILL.{pair.version}.md"
    assert pair.evolution_archive_name == f"evolutions.{pair.version}.json"
    assert pair.skill_archive.read_text(encoding="utf-8") == "# Current Skill\n"
    current_log = json.loads((skill_dir / "evolutions.json").read_text(encoding="utf-8"))
    archived_log = json.loads(pair.evolution_archive.read_text(encoding="utf-8"))
    assert current_log["entries"] == []
    assert archived_log["entries"] == []


def test_list_pairs_ignores_unpaired_archives_and_normalizes_versions(tmp_path: Path):
    root = tmp_path / "skills"
    skill_dir = _prepare_skill(root, "skill-a")
    archive_dir = skill_dir / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    _write_pair(skill_dir, "v20260101T000000")
    _write_pair(skill_dir, "v20260101T000001")
    (archive_dir / "SKILL.v20260102T000000.md").write_text("# orphan skill\n", encoding="utf-8")
    (archive_dir / "evolutions.v20260103T000000.json").write_text("{}", encoding="utf-8")
    (archive_dir / "SKILL.not-a-version.md").write_text("# ignored\n", encoding="utf-8")
    store = EvolutionStore(str(root))
    service = EvolutionArchiveService(store=store)

    pairs = service.list_pairs("skill-a")

    assert [pair.version for pair in pairs] == ["v20260101T000001", "v20260101T000000"]
    assert service.normalize_version("latest") == "latest"
    assert service.normalize_version("SKILL.v20260101T000001.md") == "v20260101T000001"
    assert service.normalize_version("20260101T000001") is None


@pytest.mark.asyncio
async def test_rollback_to_latest_archives_current_state_and_restores_pair(tmp_path: Path):
    root = tmp_path / "skills"
    skill_dir = _prepare_skill(root, "skill-a", "# Current\n")
    store = EvolutionStore(str(root))
    await store.save_evolution_log("skill-a", EvolutionLog.empty("skill-a"), skill_dir=skill_dir)
    _write_pair(skill_dir, "v20260101T000000", skill_content="# Older\n")
    _write_pair(skill_dir, "v20260102T000000", skill_content="# Target\n")
    service = EvolutionArchiveService(store=store)

    restored = await service.rollback_to_pair("skill-a", "latest", prune=False)

    assert restored is True
    assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == "# Target\n"
    restored_log = json.loads((skill_dir / "evolutions.json").read_text(encoding="utf-8"))
    assert restored_log["skill_id"] == "skill-a"
    current_archives = [
        pair
        for pair in service.list_pairs("skill-a")
        if pair.version not in {"v20260101T000000", "v20260102T000000"}
    ]
    assert len(current_archives) == 1
    assert current_archives[0].skill_archive.read_text(encoding="utf-8") == "# Current\n"


def test_prune_removes_old_complete_pairs(tmp_path: Path):
    root = tmp_path / "skills"
    skill_dir = _prepare_skill(root, "skill-a")
    _write_pair(skill_dir, "v20260101T000000")
    _write_pair(skill_dir, "v20260102T000000")
    _write_pair(skill_dir, "v20260103T000000")
    service = EvolutionArchiveService(store=EvolutionStore(str(root)))

    pruned = service.prune("skill-a", keep_latest=2)

    assert pruned == 1
    assert [pair.version for pair in service.list_pairs("skill-a")] == [
        "v20260103T000000",
        "v20260102T000000",
    ]
    archive_dir = skill_dir / "archive"
    assert not (archive_dir / "SKILL.v20260101T000000.md").exists()
    assert not (archive_dir / "evolutions.v20260101T000000.json").exists()

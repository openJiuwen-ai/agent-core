# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
# pylint: disable=protected-access
"""Tests for checkpointing evolution store (EvolutionStore)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.agent_evolving.checkpointing import EvolutionStore
from openjiuwen.agent_evolving.checkpointing.types import (
    EvolutionPatch,
    EvolutionRecord,
    EvolutionTarget,
)


def make_record(
    record_id: str,
    *,
    target: EvolutionTarget = EvolutionTarget.BODY,
    section: str = "Troubleshooting",
    content: str = "fix issue",
    summary: str | None = None,
    merge_target: str | None = None,
    applied: bool = False,
    source: str = "execution_failure",
) -> EvolutionRecord:
    return EvolutionRecord(
        id=record_id,
        source=source,
        timestamp="2026-01-01T00:00:00+00:00",
        context="ctx",
        change=EvolutionPatch(
            section=section,
            action="append",
            content=content,
            target=target,
            merge_target=merge_target,
        ),
        applied=applied,
        summary=summary,
    )


def prepare_skill(root: Path, name: str, content: str = "# Skill\n\n## Troubleshooting\n- old\n") -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
    return skill_dir


class TestEvolutionStoreBasics:
    @staticmethod
    def test_init_path_parse_and_deduplicate(tmp_path: Path):
        root_a = tmp_path / "a"
        root_b = tmp_path / "b"
        root_a.mkdir()
        root_b.mkdir()

        store = EvolutionStore(f"{root_a}; {root_b}, {root_a}")
        assert len(store.base_dirs) == 2
        assert store.base_dir == store.base_dirs[0]

    @staticmethod
    def test_init_with_empty_path_raises():
        with pytest.raises(ValueError):
            EvolutionStore("  ")

    @staticmethod
    @pytest.mark.asyncio
    async def test_list_skill_names_and_read_content(tmp_path: Path):
        root = tmp_path / "skills"
        root.mkdir()
        prepare_skill(root, "skill-a", "# A")
        prepare_skill(root, "skill-b", "# B")
        (root / "_hidden").mkdir()

        store = EvolutionStore(str(root))
        assert store.list_skill_names() == ["skill-a", "skill-b"]
        assert store.skill_exists("skill-a") is True
        assert await store.read_skill_content("skill-a") == "# A"
        assert await store.read_skill_content("missing") == ""


class TestEvolutionStoreLogCRUD:
    @staticmethod
    @pytest.mark.asyncio
    async def test_load_full_log_handles_invalid_json(tmp_path: Path):
        root = tmp_path / "skills"
        skill_dir = prepare_skill(root, "skill-a")
        (skill_dir / "evolutions.json").write_text("{not-json", encoding="utf-8")

        store = EvolutionStore(str(root))
        evo_log = await store._load_full_evolution_log("skill-a")
        assert evo_log.skill_id == "skill-a"
        assert evo_log.entries == []

    @staticmethod
    @pytest.mark.asyncio
    async def test_append_record_and_load_with_target_filter(tmp_path: Path):
        root = tmp_path / "skills"
        prepare_skill(root, "skill-a")
        store = EvolutionStore(str(root))

        rec_desc = make_record("ev_desc", target=EvolutionTarget.DESCRIPTION, section="Instructions")
        rec_body = make_record("ev_body", target=EvolutionTarget.BODY, section="Troubleshooting")
        await store.append_record("skill-a", rec_desc)
        await store.append_record("skill-a", rec_body)

        full_log = await store.load_evolution_log("skill-a")
        body_log = await store.load_evolution_log("skill-a", target=EvolutionTarget.BODY)
        assert len(full_log.entries) == 2
        assert [record.id for record in body_log.entries] == ["ev_body"]

    @staticmethod
    @pytest.mark.asyncio
    async def test_append_record_merges_when_merge_target_hit(tmp_path: Path):
        root = tmp_path / "skills"
        prepare_skill(root, "skill-a")
        store = EvolutionStore(str(root))

        old = make_record("ev_old", content="old")
        await store.append_record("skill-a", old)

        replacement = make_record("ev_new", content="new", merge_target="ev_old")
        await store.append_record("skill-a", replacement)

        evo_log = await store.load_evolution_log("skill-a")
        assert [item.id for item in evo_log.entries] == ["ev_new"]
        assert evo_log.entries[0].change.content == "new"

    @staticmethod
    @pytest.mark.asyncio
    async def test_refresh_skill_summary_writes_llm_result(tmp_path: Path):
        root = tmp_path / "skills"
        prepare_skill(root, "skill-a")
        store = EvolutionStore(str(root))
        await store.append_record(
            "skill-a",
            make_record("ev_1", content="## 超时重试\n- retry", summary="超时先重试"),
        )

        llm = MagicMock()
        llm.invoke = AsyncMock(return_value=SimpleNamespace(content="本技能聚焦超时重试与备用切换。"))
        summary = await store.refresh_skill_summary(
            "skill-a",
            llm=llm,
            model="dummy",
            language="cn",
        )
        assert summary == "本技能聚焦超时重试与备用切换。"
        evo_log = await store.load_full_evolution_log("skill-a")
        assert evo_log.summary == "本技能聚焦超时重试与备用切换。"
        llm.invoke.assert_awaited_once()


class TestEvolutionStoreVersionBump:
    @staticmethod
    @pytest.mark.asyncio
    async def test_append_record_does_not_bump_version(tmp_path: Path):
        root = tmp_path / "skills"
        prepare_skill(
            root,
            "skill-a",
            "---\nname: skill-a\ndescription: weather\nversion: 1.0.0\n---\n\n# Skill\n",
        )
        store = EvolutionStore(str(root))

        record = make_record(
            "ev_1",
            source="execution_failure",
            section="Instructions",
        )
        await store.append_record("skill-a", record)

        evo_log = await store.load_full_evolution_log("skill-a")
        assert evo_log.version == "1.0.0"
        assert evo_log.entries[0].skill_version is None
        skill_md = await store.read_skill_content("skill-a")
        assert EvolutionStore._extract_version_from_skill_md(skill_md) == "1.0.0"

    @staticmethod
    @pytest.mark.asyncio
    async def test_bump_version_for_rebuild_patch(tmp_path: Path):
        root = tmp_path / "skills"
        prepare_skill(
            root,
            "skill-a",
            "---\nname: skill-a\ndescription: weather\nversion: 1.0.0\n---\n\n# Skill\n",
        )
        store = EvolutionStore(str(root))
        await store.append_record(
            "skill-a",
            make_record("ev_1", source="execution_failure", section="Troubleshooting"),
        )

        new_version = await store.bump_version_for_rebuild("skill-a")
        assert new_version == "1.0.1"
        evo_log = await store.load_full_evolution_log("skill-a")
        assert evo_log.version == "1.0.1"
        skill_md = await store.read_skill_content("skill-a")
        assert EvolutionStore._extract_version_from_skill_md(skill_md) == "1.0.1"

    @staticmethod
    @pytest.mark.asyncio
    async def test_bump_version_for_rebuild_minor_when_any_instruction(tmp_path: Path):
        root = tmp_path / "skills"
        prepare_skill(
            root,
            "skill-a",
            "---\nname: skill-a\ndescription: weather\nversion: 1.0.5\n---\n\n# Skill\n",
        )
        store = EvolutionStore(str(root))
        await store.append_record(
            "skill-a",
            make_record("ev_patch", source="execution_failure", section="Troubleshooting"),
        )
        await store.append_record(
            "skill-a",
            make_record("ev_minor", source="user_correction", section="Instructions"),
        )

        new_version = await store.bump_version_for_rebuild("skill-a")
        assert new_version == "1.1.0"
        skill_md = await store.read_skill_content("skill-a")
        assert EvolutionStore._extract_version_from_skill_md(skill_md) == "1.1.0"
        assert "description: weather" in skill_md

    @staticmethod
    @pytest.mark.asyncio
    async def test_bump_version_for_rebuild_none_when_empty(tmp_path: Path):
        root = tmp_path / "skills"
        prepare_skill(
            root,
            "skill-a",
            "---\nversion: 2.0.0\n---\n\n# Skill\n",
        )
        store = EvolutionStore(str(root))
        assert await store.bump_version_for_rebuild("skill-a") is None
        skill_md = await store.read_skill_content("skill-a")
        assert EvolutionStore._extract_version_from_skill_md(skill_md) == "2.0.0"

    @staticmethod
    @pytest.mark.asyncio
    async def test_bump_version_for_rebuild_uses_provided_entries(tmp_path: Path):
        root = tmp_path / "skills"
        prepare_skill(
            root,
            "skill-a",
            "---\nname: skill-a\ndescription: weather\nversion: 1.0.0\n---\n\n# Skill\n",
        )
        store = EvolutionStore(str(root))
        patch = make_record("ev_patch", source="execution_failure", section="Troubleshooting")
        minor = make_record("ev_minor", source="user_correction", section="Instructions")
        await store.append_record("skill-a", patch)
        await store.append_record("skill-a", minor)

        new_version = await store.bump_version_for_rebuild("skill-a", entries=[patch])
        assert new_version == "1.0.1"
        skill_md = await store.read_skill_content("skill-a")
        assert EvolutionStore._extract_version_from_skill_md(skill_md) == "1.0.1"

    @staticmethod
    @pytest.mark.asyncio
    async def test_bump_version_for_rebuild_none_when_entries_empty(tmp_path: Path):
        root = tmp_path / "skills"
        prepare_skill(
            root,
            "skill-a",
            "---\nversion: 1.0.0\n---\n\n# Skill\n",
        )
        store = EvolutionStore(str(root))
        await store.append_record(
            "skill-a",
            make_record("ev_1", source="user_correction", section="Instructions"),
        )
        assert await store.bump_version_for_rebuild("skill-a", entries=[]) is None
        skill_md = await store.read_skill_content("skill-a")
        assert EvolutionStore._extract_version_from_skill_md(skill_md) == "1.0.0"

    @staticmethod
    @pytest.mark.asyncio
    async def test_clear_evolutions_retains_version(tmp_path: Path):
        root = tmp_path / "skills"
        prepare_skill(
            root,
            "skill-a",
            "---\nversion: 1.2.3\n---\n\n# Skill\n",
        )
        store = EvolutionStore(str(root))
        await store.append_record("skill-a", make_record("ev_1"))
        await store.clear_evolutions("skill-a", retain_version="1.3.0")

        evo_log = await store.load_full_evolution_log("skill-a")
        assert evo_log.entries == []
        assert evo_log.version == "1.3.0"

    @staticmethod
    @pytest.mark.asyncio
    async def test_create_skill_includes_default_version(tmp_path: Path):
        root = tmp_path / "skills"
        root.mkdir()
        store = EvolutionStore(str(root))

        skill_dir = await store.create_skill("new-skill", "a skill", body="body text")
        assert skill_dir is not None

        skill_md = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        assert "version: 1.0.0" in skill_md
        evo_log = await store.load_full_evolution_log("new-skill")
        assert evo_log.version == "1.0.0"
        changelog = (skill_dir / "changelog.md").read_text(encoding="utf-8")
        assert changelog.startswith("# Changelog")
        assert "Unreleased" not in changelog
        assert "## [" not in changelog


class TestEvolutionStoreArchive:
    @staticmethod
    @pytest.mark.asyncio
    async def test_append_record_does_not_archive_existing_skill_body(tmp_path: Path):
        root = tmp_path / "skills"
        skill_dir = prepare_skill(root, "skill-a", "# Skill\n\nold body\n")
        store = EvolutionStore(str(root))

        await store.append_record("skill-a", make_record("ev_1"))

        archive = skill_dir / "archive"
        assert not archive.exists() or list(archive.glob("SKILL.*.md")) == []

    @staticmethod
    @pytest.mark.asyncio
    async def test_append_record_does_not_archive_previous_evolution_log(tmp_path: Path):
        root = tmp_path / "skills"
        skill_dir = prepare_skill(root, "skill-a")
        store = EvolutionStore(str(root))

        await store.append_record("skill-a", make_record("ev_1", content="first"))
        await store.append_record("skill-a", make_record("ev_2", content="second"))

        archive = skill_dir / "archive"
        assert not archive.exists() or list(archive.glob("evolutions.*.json")) == []
        current_log = json.loads((skill_dir / "evolutions.json").read_text(encoding="utf-8"))
        assert [entry["id"] for entry in current_log["entries"]] == ["ev_1", "ev_2"]

    @staticmethod
    @pytest.mark.asyncio
    async def test_solidify_does_not_archive_before_writing_skill_body_and_log(tmp_path: Path):
        root = tmp_path / "skills"
        skill_dir = prepare_skill(root, "skill-a", "# Skill\n\n## Troubleshooting\n- old\n")
        store = EvolutionStore(str(root))
        await store.append_record("skill-a", make_record("ev_body_1", content="- check logs"))

        archive = skill_dir / "archive"
        existing_archives = {path.name for path in archive.iterdir()} if archive.is_dir() else set()

        await store.solidify("skill-a")

        new_archives = []
        if archive.is_dir():
            new_archives = [path for path in archive.iterdir() if path.name not in existing_archives]
        assert new_archives == []
        assert "- check logs" in (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        solidified_log = json.loads((skill_dir / "evolutions.json").read_text(encoding="utf-8"))
        assert solidified_log["entries"][0]["applied"] is True

    @staticmethod
    @pytest.mark.asyncio
    async def test_archive_public_helpers_read_and_restore_log(tmp_path: Path):
        root = tmp_path / "skills"
        skill_dir = prepare_skill(root, "skill-a", "# Current\n")
        archive = skill_dir / "archive"
        archive.mkdir(parents=True)
        archived_log = {
            "skill_id": "skill-a",
            "version": "1.0.0",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "entries": [],
        }
        (archive / "SKILL.v1.0.0.md").write_text("# Archived\n", encoding="utf-8")
        (archive / "evolutions.v1.0.0.json").write_text(
            json.dumps(archived_log, ensure_ascii=False),
            encoding="utf-8",
        )
        (skill_dir / "evolutions.json").write_text("{}", encoding="utf-8")
        store = EvolutionStore(str(root))

        assert store.get_skill_archive_dir("skill-a") == archive
        assert store.get_skill_archive_file("skill-a", "SKILL.v1.0.0.md") == archive / "SKILL.v1.0.0.md"
        assert store.get_skill_archive_file("skill-a", "../SKILL.md") is None
        assert await store.read_archive_text("skill-a", "SKILL.v1.0.0.md") == "# Archived\n"
        assert await store.read_archive_text("skill-a", "../SKILL.md") == ""
        assert await store.restore_evolution_log_from_archive("skill-a", "evolutions.v1.0.0.json") is True
        assert json.loads((skill_dir / "evolutions.json").read_text(encoding="utf-8")) == archived_log
        assert await store.restore_evolution_log_from_archive("skill-a", "missing.json") is False

    @staticmethod
    @pytest.mark.asyncio
    async def test_public_archive_operations_directly(tmp_path: Path):
        root = tmp_path / "skills"
        skill_dir = prepare_skill(root, "skill-a", "# Skill\n")
        store = EvolutionStore(str(root))
        await store.append_record("skill-a", make_record("ev_1", content="first"))

        body_archive = await store.archive_skill_body("skill-a", version="1.0.1")
        evo_archive = await store.archive_evolutions("skill-a", version="1.0.1")
        assert body_archive == "SKILL.v1.0.1.md"
        assert evo_archive == "evolutions.v1.0.1.json"
        archives = store.list_archives("skill-a")
        assert body_archive in archives
        assert evo_archive in archives

        assert await store.write_skill_content("skill-a", "# Rewritten\n") is True
        assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == "# Rewritten\n"
        await store.clear_evolutions("skill-a")
        assert (await store.load_evolution_log("skill-a")).entries == []

    @staticmethod
    @pytest.mark.asyncio
    async def test_archive_current_state_uses_semver_names(tmp_path: Path):
        root = tmp_path / "skills"
        prepare_skill(
            root,
            "skill-a",
            "---\nname: skill-a\ndescription: d\nversion: 1.2.3\n---\n\n# Skill\n",
        )
        store = EvolutionStore(str(root))
        await store.append_record("skill-a", make_record("ev_1", content="first"))
        # append no longer bumps; archive uses current frontmatter version
        body_archive, evo_archive = await store.archive_current_state("skill-a")

        assert body_archive == "SKILL.v1.2.3.md"
        assert evo_archive == "evolutions.v1.2.3.json"

    @staticmethod
    @pytest.mark.asyncio
    async def test_archive_current_state_skips_when_version_exists(tmp_path: Path):
        root = tmp_path / "skills"
        skill_dir = prepare_skill(
            root,
            "skill-a",
            "---\nname: skill-a\ndescription: d\nversion: 1.0.0\n---\n\n# Original\n",
        )
        archive = skill_dir / "archive"
        archive.mkdir(parents=True)
        (archive / "SKILL.v1.0.0.md").write_text("# Already archived\n", encoding="utf-8")
        (archive / "evolutions.v1.0.0.json").write_text('{"entries": []}', encoding="utf-8")
        store = EvolutionStore(str(root))

        body_archive, evo_archive = await store.archive_current_state("skill-a")
        # Skip rewrite but return existing names so rebuild is not blocked.
        assert body_archive == "SKILL.v1.0.0.md"
        assert evo_archive == "evolutions.v1.0.0.json"
        assert (archive / "SKILL.v1.0.0.md").read_text(encoding="utf-8") == "# Already archived\n"

    @staticmethod
    def test_archive_name_helpers_validate_and_pair():
        assert EvolutionStore.is_valid_skill_archive_name("SKILL.v1.0.0.md") is True
        assert EvolutionStore.is_valid_skill_archive_name("../SKILL.v1.0.0.md") is False
        assert EvolutionStore.is_valid_skill_archive_name("nested/SKILL.v1.0.0.md") is False
        assert EvolutionStore.paired_evolution_archive_name("SKILL.v1.0.0.md") == "evolutions.v1.0.0.json"
        assert EvolutionStore.paired_evolution_archive_name("bad.md") is None
        assert EvolutionStore.paired_evolution_archive_name("SKILL.1.0.0.md") is None
        assert EvolutionStore.normalize_body_archive_name("1.0.0") == "SKILL.v1.0.0.md"
        assert EvolutionStore.normalize_body_archive_name("v1.0.0") == "SKILL.v1.0.0.md"
        assert EvolutionStore.normalize_body_archive_name("SKILL.v1.0.0.md") == "SKILL.v1.0.0.md"
        assert EvolutionStore.normalize_body_archive_name("SKILL.1.0.0.md") is None
        assert EvolutionStore.normalize_body_archive_name("latest") is None
        assert EvolutionStore.normalize_body_archive_name("not-a-version") is None
        assert EvolutionStore.normalize_body_archive_name("../SKILL.v1.0.0.md") is None
        assert EvolutionStore.is_body_archive_filename("SKILL.v1.0.0.md") is True
        assert EvolutionStore.is_body_archive_filename("SKILL.1.0.0.md") is False

    @staticmethod
    @pytest.mark.asyncio
    async def test_delete_archive_version_removes_paired_files(tmp_path: Path):
        root = tmp_path / "skills"
        skill_dir = prepare_skill(root, "skill-a", "# Skill\n")
        archive = skill_dir / "archive"
        archive.mkdir(parents=True)
        body_name = "SKILL.v1.0.0.md"
        evo_name = "evolutions.v1.0.0.json"
        (archive / body_name).write_text("# Archived\n", encoding="utf-8")
        (archive / evo_name).write_text("{\"entries\": []}", encoding="utf-8")
        store = EvolutionStore(str(root))

        assert await store.delete_archive_version("skill-a", body_name) is True
        assert not (archive / body_name).exists()
        assert not (archive / evo_name).exists()

        assert await store.delete_archive_version("skill-a", "../SKILL.v1.0.0.md") is False
        assert await store.delete_archive_version("skill-a", "missing.md") is False

    @staticmethod
    @pytest.mark.asyncio
    async def test_delete_archive_version_returns_false_when_paired_evo_delete_fails(tmp_path: Path):
        root = tmp_path / "skills"
        skill_dir = prepare_skill(root, "skill-a", "# Skill\n")
        archive = skill_dir / "archive"
        archive.mkdir(parents=True)
        body_name = "SKILL.v1.0.0.md"
        evo_name = "evolutions.v1.0.0.json"
        (archive / body_name).write_text("# Archived\n", encoding="utf-8")
        (archive / evo_name).write_text("{\"entries\": []}", encoding="utf-8")
        store = EvolutionStore(str(root))

        original_unlink = Path.unlink

        def unlink_side_effect(self, *args, **kwargs):
            if self.name.startswith("evolutions."):
                raise PermissionError("denied")
            return original_unlink(self, *args, **kwargs)

        with pytest.MonkeyPatch.context() as monkeypatch:
            monkeypatch.setattr(Path, "unlink", unlink_side_effect)
            assert await store.delete_archive_version("skill-a", body_name) is False

        assert not (archive / body_name).exists()
        assert (archive / evo_name).exists()

    @staticmethod
    def test_resolve_paired_evolution_archive_exact_only(tmp_path: Path):
        root = tmp_path / "skills"
        skill_dir = prepare_skill(root, "skill-a", "# Skill\n")
        archive = skill_dir / "archive"
        archive.mkdir(parents=True)
        body_name = "SKILL.v1.0.0.md"
        (archive / body_name).write_text("# Archived\n", encoding="utf-8")
        exact = archive / "evolutions.v1.0.0.json"
        other = archive / "evolutions.v1.0.1.json"
        exact.write_text("{\"entries\": [\"exact\"]}", encoding="utf-8")
        other.write_text("{\"entries\": [\"other\"]}", encoding="utf-8")
        store = EvolutionStore(str(root))

        resolved = store.resolve_paired_evolution_archive("skill-a", body_name)
        assert resolved == exact
        assert store.resolve_paired_evolution_archive("skill-a", "SKILL.v9.9.9.md") is None


class TestEvolutionStoreSolidifyAndFormatting:
    @staticmethod
    @pytest.mark.asyncio
    async def test_solidify_injects_pending_body_and_marks_applied(tmp_path: Path):
        root = tmp_path / "skills"
        prepare_skill(root, "skill-a", "# Skill\n\n## Troubleshooting\n- old\n")
        store = EvolutionStore(str(root))
        await store.append_record("skill-a", 
        make_record("ev_body_1", target=EvolutionTarget.BODY, content="- check logs"))
        await store.append_record("skill-a", 
        make_record("ev_desc_1", target=EvolutionTarget.DESCRIPTION, content="desc only"))

        count = await store.solidify("skill-a")
        skill_md = (root / "skill-a" / "SKILL.md").read_text(encoding="utf-8")
        full_log = await store.load_evolution_log("skill-a")

        assert count == 1
        assert "- check logs" in skill_md
        status = {item.id: item.applied for item in full_log.entries}
        assert status["ev_body_1"] is True
        assert status["ev_desc_1"] is False

    @staticmethod
    def test_inject_section_appends_new_header_when_missing():
        patch = EvolutionPatch(
            section="Examples",
            action="append",
            content="- do this",
            target=EvolutionTarget.BODY,
        )
        result = EvolutionStore._inject_section("# Skill", patch)
        assert "## Examples" in result
        assert "- do this" in result

    @staticmethod
    @pytest.mark.asyncio
    async def test_formatting_helpers_and_pending_summary(tmp_path: Path):
        root = tmp_path / "skills"
        prepare_skill(root, "skill-a")
        prepare_skill(root, "skill-b")
        store = EvolutionStore(str(root))

        await store.append_record(
            "skill-a",
            make_record(
                "ev_desc",
                target=EvolutionTarget.DESCRIPTION,
                section="Instructions",
                content="标题\n- line1\n- line2",
            ),
        )
        await store.append_record(
            "skill-a",
            make_record(
                "ev_body",
                target=EvolutionTarget.BODY,
                section="Troubleshooting",
                content="Body fix",
            ),
        )

        desc_text = await store.format_desc_experience_text("skill-a")
        body_text = await store.format_body_experience_text("skill-a")
        all_desc = await store.format_all_desc_experiences(["skill-a", "skill-b"])
        summary = await store.list_pending_summary(["skill-a", "skill-b"])

        assert "标题" in desc_text
        assert "body 演进经验" in body_text
        assert "skill-a" in all_desc
        assert "skill-b" not in all_desc
        assert "skill-a" in summary
        assert "description: 1" in summary

    @staticmethod
    @pytest.mark.asyncio
    async def test_pending_summary_returns_empty_text_when_no_records(tmp_path: Path):
        root = tmp_path / "skills"
        prepare_skill(root, "skill-a")
        store = EvolutionStore(str(root))
        assert await store.list_pending_summary(["skill-a"]) == "当前所有 Skill 暂无演进信息。"


class TestEvolutionStoreSysOperationPath:
    @staticmethod
    @pytest.mark.asyncio
    async def test_read_file_text_with_sys_operation(tmp_path: Path):
        store = EvolutionStore(str(tmp_path))
        fs_mock = MagicMock()
        fs_mock.read_file = AsyncMock(
            return_value=SimpleNamespace(
                code=0,
                data=SimpleNamespace(content=123),
                message="ok",
            )
        )
        store.sys_operation = SimpleNamespace(fs=lambda: fs_mock)

        text = await store._read_file_text(tmp_path / "x.txt")
        assert text == "123"

    @staticmethod
    @pytest.mark.asyncio
    async def test_write_file_text_with_sys_operation(tmp_path: Path):
        store = EvolutionStore(str(tmp_path))
        fs_mock = MagicMock()
        fs_mock.write_file = AsyncMock(
            return_value=SimpleNamespace(code=0, message="ok")
        )
        store.sys_operation = SimpleNamespace(fs=lambda: fs_mock)

        await store._write_file_text(tmp_path / "x.txt", "hello")
        fs_mock.write_file.assert_awaited_once()

    @staticmethod
    @pytest.mark.asyncio
    async def test_write_file_text_without_sys_operation(tmp_path: Path):
        store = EvolutionStore(str(tmp_path))
        target = tmp_path / "x.txt"
        await store._write_file_text(target, "hello")
        assert target.read_text(encoding="utf-8") == "hello"


class TestEvolutionStorePersistScript:
    @staticmethod
    @pytest.mark.asyncio
    async def test_persist_script_writes_file_and_replaces_content(tmp_path: Path):
        root = tmp_path / "skills"
        skill_dir = prepare_skill(root, "skill-a")
        store = EvolutionStore(str(root))

        record = make_record(
            "ev_script_1",
            target=EvolutionTarget.SCRIPT,
            section="Scripts",
            content="import matplotlib\nprint('chart')",
        )
        record.change.script_language = "python"
        record.change.script_purpose = "chart generation"

        await store._persist_script(skill_dir, record)

        scripts_dir = skill_dir / "evolution" / "scripts"
        assert scripts_dir.exists()

        written_files = list(scripts_dir.glob("*.py"))
        assert len(written_files) == 1
        assert "import matplotlib" in written_files[0].read_text(encoding="utf-8")

        assert record.change.content.startswith("Script:")
        assert "python" in record.change.content
        assert record.change.script_filename is not None

    @staticmethod
    @pytest.mark.asyncio
    async def test_persist_script_uses_provided_filename(tmp_path: Path):
        root = tmp_path / "skills"
        skill_dir = prepare_skill(root, "skill-a")
        store = EvolutionStore(str(root))

        record = make_record(
            "ev_script_2",
            target=EvolutionTarget.SCRIPT,
            section="Scripts",
            content="console.log('hi')",
        )
        record.change.script_filename = "hello.js"
        record.change.script_language = "javascript"

        await store._persist_script(skill_dir, record)

        script_path = skill_dir / "evolution" / "scripts" / "hello.js"
        assert script_path.exists()
        assert "console.log" in script_path.read_text(encoding="utf-8")


class TestEvolutionStoreAppendScriptRecord:
    @staticmethod
    @pytest.mark.asyncio
    async def test_append_script_record_persists_and_renders(tmp_path: Path):
        root = tmp_path / "skills"
        prepare_skill(root, "skill-a", "# Skill A\n\n## Troubleshooting\n- old\n")
        store = EvolutionStore(str(root))

        record = make_record(
            "ev_s1",
            target=EvolutionTarget.SCRIPT,
            section="Scripts",
            content="import pandas\ndf = pandas.read_csv('data.csv')",
        )
        record.change.script_language = "python"
        record.change.script_purpose = "data processing"

        await store.append_record("skill-a", record)

        script_file = root / "skill-a" / "evolution" / "scripts"
        assert script_file.exists()
        py_files = list(script_file.glob("*.py"))
        assert len(py_files) == 1

        skill_md = (root / "skill-a" / "SKILL.md").read_text(encoding="utf-8")
        assert "evolution-index-start" in skill_md
        assert "Scripts" in skill_md


class TestEvolutionStoreRenderMarkdown:
    @staticmethod
    @pytest.mark.asyncio
    async def test_render_creates_section_files(tmp_path: Path):
        root = tmp_path / "skills"
        prepare_skill(root, "skill-a")
        store = EvolutionStore(str(root))

        await store.append_record(
            "skill-a",
            make_record("ev_body_1", target=EvolutionTarget.BODY, section="Troubleshooting", content="fix bug"),
        )
        await store.append_record(
            "skill-a",
            make_record("ev_body_2", target=EvolutionTarget.BODY, section="Examples", content="example case"),
        )

        evo_dir = root / "skill-a" / "evolution"
        assert (evo_dir / "troubleshooting.md").exists()
        assert (evo_dir / "examples.md").exists()

        ts_content = (evo_dir / "troubleshooting.md").read_text(encoding="utf-8")
        assert "fix bug" in ts_content
        assert "Auto-generated" in ts_content

    @staticmethod
    @pytest.mark.asyncio
    async def test_render_creates_script_index(tmp_path: Path):
        root = tmp_path / "skills"
        prepare_skill(root, "skill-a")
        store = EvolutionStore(str(root))

        record = make_record(
            "ev_s1",
            target=EvolutionTarget.SCRIPT,
            section="Scripts",
            content="print('hello')",
        )
        record.change.script_language = "python"
        record.change.script_purpose = "greeting"

        await store.append_record("skill-a", record)

        index_path = root / "skill-a" / "evolution" / "scripts" / "_index.md"
        assert index_path.exists()
        index_content = index_path.read_text(encoding="utf-8")
        assert "Script Index" in index_content
        assert "python" in index_content
        assert "greeting" in index_content
        skill_md = (root / "skill-a" / "SKILL.md").read_text(encoding="utf-8")
        assert "### Script Assets" in skill_md
        assert "| greeting | python | 0.60 |" in skill_md

    @staticmethod
    @pytest.mark.asyncio
    async def test_render_updates_skill_md_index_block(tmp_path: Path):
        root = tmp_path / "skills"
        prepare_skill(root, "skill-a", "# Skill A\n\nSome content\n")
        store = EvolutionStore(str(root))

        await store.append_record(
            "skill-a",
            make_record(
                "ev_1",
                target=EvolutionTarget.BODY,
                content="## Body fix\n\nUse the safer fallback.",
                summary="Use safer fallback for unstable services",
            ),
        )

        skill_md = (root / "skill-a" / "SKILL.md").read_text(encoding="utf-8")
        assert "<!-- evolution-index-start -->" in skill_md
        assert "<!-- evolution-index-end -->" in skill_md
        assert "Evolution Experiences" in skill_md
        assert "**1**" in skill_md
        assert "### Experience Index" in skill_md
        assert "| Summary | Type | Score | Detail |" in skill_md
        assert "Use safer fallback for unstable services" in skill_md
        assert "[evolution/troubleshooting.md#ev_1]" in skill_md
        assert "Before applying this skill" in skill_md
        assert "Top Experiences" not in skill_md

        detail = (root / "skill-a" / "evolution" / "troubleshooting.md").read_text(encoding="utf-8")
        assert '<a id="ev_1"></a>' in detail
        assert "### [ev_1] Use safer fallback for unstable services" in detail
        assert "## Body fix" in detail

    @staticmethod
    @pytest.mark.asyncio
    async def test_render_replaces_existing_index_block(tmp_path: Path):
        root = tmp_path / "skills"
        initial_content = (
            "# Skill A\n\nContent\n\n"
            "<!-- evolution-index-start -->\nold index\n<!-- evolution-index-end -->\n"
        )
        prepare_skill(root, "skill-a", initial_content)
        store = EvolutionStore(str(root))

        await store.append_record(
            "skill-a",
            make_record("ev_new", target=EvolutionTarget.BODY, content="new fix"),
        )

        skill_md = (root / "skill-a" / "SKILL.md").read_text(encoding="utf-8")
        assert "old index" not in skill_md
        assert "Evolution Experiences" in skill_md
        assert skill_md.count("evolution-index-start") == 1

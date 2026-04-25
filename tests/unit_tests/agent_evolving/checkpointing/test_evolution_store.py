# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
# pylint: disable=protected-access
"""Tests for checkpointing evolution store (EvolutionStore)."""

from __future__ import annotations

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
    merge_target: str | None = None,
    applied: bool = False,
) -> EvolutionRecord:
    return EvolutionRecord(
        id=record_id,
        source="execution_failure",
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
        evo_log = await store.load_full_evolution_log("skill-a")
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


class TestEvolutionStoreFormatting:
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

        text = await store.read_file_text(tmp_path / "x.txt")
        assert text == "123"

    @staticmethod
    @pytest.mark.asyncio
    async def test_write_file_text_with_sys_operation(tmp_path: Path):
        store = EvolutionStore(str(tmp_path))
        fs_mock = MagicMock()
        fs_mock.write_file = AsyncMock(return_value=SimpleNamespace(code=0, message="ok"))
        store.sys_operation = SimpleNamespace(fs=lambda: fs_mock)

        await store.write_file_text(tmp_path / "x.txt", "hello")
        fs_mock.write_file.assert_awaited_once()

    @staticmethod
    @pytest.mark.asyncio
    async def test_write_file_text_without_sys_operation(tmp_path: Path):
        store = EvolutionStore(str(tmp_path))
        target = tmp_path / "x.txt"
        await store.write_file_text(target, "hello")
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

    @staticmethod
    @pytest.mark.asyncio
    async def test_render_updates_skill_md_index_block(tmp_path: Path):
        root = tmp_path / "skills"
        prepare_skill(root, "skill-a", "# Skill A\n\nSome content\n")
        store = EvolutionStore(str(root))

        await store.append_record(
            "skill-a",
            make_record("ev_1", target=EvolutionTarget.BODY, content="body fix"),
        )

        skill_md = (root / "skill-a" / "SKILL.md").read_text(encoding="utf-8")
        assert "<!-- evolution-index-start -->" in skill_md
        assert "<!-- evolution-index-end -->" in skill_md
        assert "Evolution Experiences" in skill_md
        assert "**1**" in skill_md

    @staticmethod
    @pytest.mark.asyncio
    async def test_render_replaces_existing_index_block(tmp_path: Path):
        root = tmp_path / "skills"
        initial_content = (
            "# Skill A\n\nContent\n\n<!-- evolution-index-start -->\nold index\n<!-- evolution-index-end -->\n"
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

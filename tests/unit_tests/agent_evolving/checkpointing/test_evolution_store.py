# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
# pylint: disable=protected-access
"""Tests for checkpointing evolution store (EvolutionStore)."""

from __future__ import annotations

import asyncio
import io
import tarfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.agent_evolving.checkpointing import EvolutionStore
from openjiuwen.agent_evolving.checkpointing.skill_package import pack_skill_directory
from openjiuwen.agent_evolving.checkpointing.store_records import MergeRecordsRequest, StoreRecordsHelper
from openjiuwen.agent_evolving.checkpointing.types import (
    EvolutionLog,
    EvolutionPatch,
    EvolutionRecord,
    EvolutionTarget,
)
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError, build_error


def make_record(
    record_id: str,
    *,
    target: EvolutionTarget = EvolutionTarget.BODY,
    section: str = "Troubleshooting",
    content: str = "fix issue",
    merge_target: str | None = None,
    applied: bool = False,
    summary: str | None = None,
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
        summary=summary,
    )


def prepare_skill(root: Path, name: str, content: str = "# Skill\n\n## Troubleshooting\n- old\n") -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
    return skill_dir


def write_invalid_evolution_log(
    fallback_write: Callable[[Path, str], Awaitable[None]],
) -> Callable[[Path, str], Awaitable[None]]:
    async def write_file(path: Path, content: str) -> None:
        if "evolutions.json" in path.name:
            path.write_text("{not-json", encoding="utf-8")
            return
        await fallback_write(path, content)

    return write_file


def assert_no_files(path: Path, pattern: str = "*") -> None:
    assert not path.exists() or not any(path.glob(pattern))


def make_skill_package(tmp_path: Path) -> bytes:
    source_dir = prepare_skill(tmp_path / "package-source", "source-skill")
    return pack_skill_directory(source_dir)


def make_named_member_package(member_name: str) -> bytes:
    payload = b"# Skill\n"
    member = tarfile.TarInfo(name=member_name)
    member.size = len(payload)
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        archive.addfile(member, io.BytesIO(payload))
    return buffer.getvalue()


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

    @staticmethod
    @pytest.mark.asyncio
    async def test_strict_read_requires_skill_md_definition(tmp_path: Path):
        root = tmp_path / "skills"
        skill_dir = root / "skill-a"
        skill_dir.mkdir(parents=True)
        (skill_dir / "README.md").write_text("# fallback", encoding="utf-8")

        store = EvolutionStore(str(root))

        assert store.skill_exists("skill-a") is True
        assert store.skill_definition_exists("skill-a") is False
        assert await store.read_skill_content("skill-a") == "# fallback"
        with pytest.raises(BaseError) as exc_info:
            await store.read_skill_content("skill-a", strict=True)
        assert exc_info.value.status == StatusCode.TOOLCHAIN_EVOLVING_SKILL_DEFINITION_NOT_FOUND

    @staticmethod
    @pytest.mark.asyncio
    async def test_kind_aware_resolution_keeps_same_name_skill_logs_separate(tmp_path: Path):
        skill_root = tmp_path / "skills"
        swarm_root = tmp_path / "swarm-skills"
        regular_dir = prepare_skill(skill_root, "foo")
        swarm_dir = prepare_skill(
            swarm_root,
            "foo",
            "---\nname: foo\nkind: team-skill\n---\n\n# Team Skill\n",
        )
        store = EvolutionStore([str(skill_root), str(swarm_root)])

        assert store.resolve_skill_dir("foo", subject_kind="skill") == regular_dir
        assert store.resolve_skill_dir("foo", subject_kind="swarm-skill") == swarm_dir
        assert store.skill_exists("foo", subject_kind="swarm-skill") is True

        await store.append_record("foo", make_record("ev_skill", content="regular"), subject_kind="skill")
        await store.append_record("foo", make_record("ev_swarm", content="swarm"), subject_kind="swarm-skill")
        await store.append_record("foo", make_record("ev_swarm_extra", content="extra"), subject_kind="swarm-skill")

        skill_log = await store.load_full_evolution_log("foo", subject_kind="skill")
        swarm_log = await store.load_full_evolution_log("foo", subject_kind="swarm-skill")
        skill_records = await store.get_records_by_score("foo", subject_kind="skill")
        swarm_records = await store.load_records_by_ids("foo", ["ev_swarm"], subject_kind="swarm-skill")

        assert [record.id for record in skill_log.entries] == ["ev_skill"]
        assert [record.id for record in swarm_log.entries] == ["ev_swarm", "ev_swarm_extra"]
        assert [record.id for record in skill_records] == ["ev_skill"]
        assert [record.id for record in swarm_records] == ["ev_swarm"]
        assert "ev_skill" in (regular_dir / "evolutions.json").read_text(encoding="utf-8")
        assert "ev_swarm" in (swarm_dir / "evolutions.json").read_text(encoding="utf-8")

        merged = await store.merge_records(
            MergeRecordsRequest(
                name="foo",
                primary_id="ev_swarm",
                remove_ids=["ev_swarm_extra"],
                new_content="merged swarm",
                subject_kind="swarm-skill",
            )
        )
        updated = await store.update_record_content(
            "foo",
            "ev_swarm",
            "updated swarm",
            subject_kind="swarm-skill",
        )
        deleted = await store.delete_records("foo", ["ev_skill"], subject_kind="skill")
        archive_name = await store.archive_evolutions("foo", subject_kind="swarm-skill")
        await store.clear_evolutions("foo", subject_kind="skill")

        assert merged is not None
        assert merged.change.content == "merged swarm"
        assert updated is not None
        assert updated.change.content == "updated swarm"
        assert deleted == 1
        assert archive_name is not None
        assert (swarm_dir / "archive" / archive_name).exists()
        assert (await store.load_full_evolution_log("foo", subject_kind="skill")).entries == []
        persisted_swarm_log = await store.load_full_evolution_log("foo", subject_kind="swarm-skill")
        assert [record.id for record in persisted_swarm_log.entries] == ["ev_swarm"]

    @staticmethod
    def test_subject_kind_none_preserves_name_only_resolution_order(tmp_path: Path):
        first_root = tmp_path / "first"
        second_root = tmp_path / "second"
        first_dir = prepare_skill(first_root, "foo")
        prepare_skill(second_root, "foo", "---\nname: foo\nkind: team-skill\n---\n\n# Team Skill\n")

        store = EvolutionStore([str(first_root), str(second_root)])

        assert store.resolve_skill_dir("foo") == first_dir
        assert store.skill_exists("foo") is True
        assert store.resolve_skill_dir("bar", create=True) == first_root.resolve() / "bar"

    @staticmethod
    def test_resolve_subject_payload_reads_actual_skill_kind(tmp_path: Path):
        root = tmp_path / "skills"
        prepare_skill(root, "plain")
        prepare_skill(root, "team", "---\nname: team\nkind: team-skill\n---\n\n# Team Skill\n")
        prepare_skill(root, "swarm", "---\nname: swarm\nkind: swarm-skill\n---\n\n# Swarm Skill\n")
        store = EvolutionStore(str(root))

        assert store.resolve_subject_payload("plain") == {"kind": "skill", "name": "plain"}
        assert store.resolve_subject_payload("team") == {"kind": "swarm-skill", "name": "team"}
        assert store.resolve_subject_payload("swarm") == {"kind": "swarm-skill", "name": "swarm"}
        assert store.resolve_subject_payload("missing") is None


class TestEvolutionStoreLogCRUD:
    @staticmethod
    def test_record_summary_serializes_and_old_json_remains_compatible():
        patch = EvolutionPatch(
            section="Troubleshooting",
            action="append",
            content="Check CSV inputs",
            target=EvolutionTarget.BODY,
        )
        record = EvolutionRecord.make(
            source="execution_failure",
            context="ctx",
            change=patch,
            summary="Check CSV encoding and delimiters before parsing.",
        )

        payload = record.to_dict()
        restored = EvolutionRecord.from_dict(payload)
        legacy_payload = dict(payload)
        legacy_payload.pop("summary")
        legacy = EvolutionRecord.from_dict(legacy_payload)

        assert payload["summary"] == "Check CSV encoding and delimiters before parsing."
        assert restored.summary == "Check CSV encoding and delimiters before parsing."
        assert legacy.summary is None

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

    @staticmethod
    @pytest.mark.asyncio
    async def test_append_record_rolls_back_log_on_failure(tmp_path: Path):
        root = tmp_path / "skills"
        prepare_skill(root, "skill-a")
        store = EvolutionStore(str(root))
        store.write_file_text = write_invalid_evolution_log(store.write_file_text)

        with pytest.raises(BaseError) as exc_info:
            await store.append_record("skill-a", make_record("ev_1"))
        assert exc_info.value.status == StatusCode.TOOLCHAIN_EVOLVING_SKILL_STORE_EXECUTION_ERROR

        evo_log = await store.load_evolution_log("skill-a")
        assert evo_log.entries == []

    @staticmethod
    @pytest.mark.asyncio
    async def test_append_record_rolls_back_script_file_and_keeps_payload(tmp_path: Path):
        root = tmp_path / "skills"
        prepare_skill(root, "skill-a")
        store = EvolutionStore(str(root))
        record = make_record(
            "ev_script",
            target=EvolutionTarget.SCRIPT,
            section="Scripts",
            content="print('new')",
        )
        record.change.script_language = "python"
        original_content = record.change.content
        store.write_file_text = write_invalid_evolution_log(store.write_file_text)

        with pytest.raises(BaseError) as exc_info:
            await store.append_record("skill-a", record)
        assert exc_info.value.status == StatusCode.TOOLCHAIN_EVOLVING_SKILL_STORE_EXECUTION_ERROR

        scripts_dir = root / "skill-a" / "evolution" / "scripts"
        assert_no_files(scripts_dir, "*.py")
        assert record.change.content == original_content
        assert record.change.script_filename is None

    @staticmethod
    @pytest.mark.asyncio
    async def test_append_record_rolls_back_projection_on_failure(tmp_path: Path):
        root = tmp_path / "skills"
        skill_dir = prepare_skill(root, "skill-a")
        original_skill_md = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        store = EvolutionStore(str(root))
        original_write_file_text = store.write_file_text

        async def fail_skill_md_projection(path: Path, content: str) -> None:
            if path.name == "SKILL.md":
                raise build_error(
                    StatusCode.TOOLCHAIN_EVOLVING_SKILL_STORE_EXECUTION_ERROR,
                    error_msg="projection failed",
                )
            await original_write_file_text(path, content)

        store.write_file_text = fail_skill_md_projection

        with pytest.raises(BaseError) as exc_info:
            await store.append_record("skill-a", make_record("ev_1"))
        assert exc_info.value.status == StatusCode.TOOLCHAIN_EVOLVING_SKILL_STORE_EXECUTION_ERROR

        evo_log = await store.load_evolution_log("skill-a")
        assert evo_log.entries == []
        assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == original_skill_md
        assert_no_files(skill_dir / "evolution", "*.md")

    @staticmethod
    @pytest.mark.asyncio
    async def test_append_record_preserves_existing_binary_evolution_assets(tmp_path: Path):
        root = tmp_path / "skills"
        skill_dir = prepare_skill(root, "xlsx")
        assets_dir = skill_dir / "evolution" / "assets"
        assets_dir.mkdir(parents=True)
        workbook_bytes = b"PK\x03\x04binary-workbook-\x86-content"
        workbook_path = assets_dir / "sample.xlsx"
        workbook_path.write_bytes(workbook_bytes)
        store = EvolutionStore(str(root))

        await store.append_record("xlsx", make_record("ev_xlsx", content="Handle workbook templates."))

        assert workbook_path.read_bytes() == workbook_bytes
        evo_log = await store.load_full_evolution_log("xlsx")
        assert [record.id for record in evo_log.entries] == ["ev_xlsx"]

    @staticmethod
    @pytest.mark.asyncio
    async def test_save_evolution_log_raises_when_readback_is_invalid_json(tmp_path: Path):
        root = tmp_path / "skills"
        prepare_skill(root, "skill-a")
        store = EvolutionStore(str(root))

        async def write_invalid_json(path: Path, content: str) -> None:
            path.write_text("{not-json", encoding="utf-8")

        store.write_file_text = write_invalid_json

        with pytest.raises(BaseError, match="read back") as exc_info:
            await store.save_evolution_log("skill-a", EvolutionLog.empty("skill-a"))
        assert exc_info.value.status == StatusCode.TOOLCHAIN_EVOLVING_SKILL_STORE_EXECUTION_ERROR


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
    async def test_write_file_text_raises_on_sys_operation_failure(tmp_path: Path):
        store = EvolutionStore(str(tmp_path))
        fs_mock = MagicMock()
        fs_mock.write_file = AsyncMock(return_value=SimpleNamespace(code=1, message="disk full"))
        store.sys_operation = SimpleNamespace(fs=lambda: fs_mock)

        with pytest.raises(BaseError, match="disk full") as exc_info:
            await store.write_file_text(tmp_path / "x.txt", "hello")
        assert exc_info.value.status == StatusCode.TOOLCHAIN_EVOLVING_SKILL_STORE_EXECUTION_ERROR

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

        await StoreRecordsHelper(store).persist_script(skill_dir, record)

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

        await StoreRecordsHelper(store).persist_script(skill_dir, record)

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


class TestEvolutionStoreConcurrentSafety:
    """Tests for skill-level semantic locks preventing Read-Modify-Write races."""

    @staticmethod
    @pytest.mark.asyncio
    async def test_concurrent_append_record_no_data_loss(tmp_path: Path):
        """Two coroutines appending to the same skill must not lose records."""
        root = tmp_path / "skills"
        prepare_skill(root, "skill-a")
        store = EvolutionStore(str(root))

        # Create 10 records to append concurrently
        records = [make_record(f"ev_{i}", content=f"record {i}") for i in range(10)]

        # Run 10 concurrent appends
        await asyncio.gather(*[store.append_record("skill-a", r) for r in records])

        evo_log = await store.load_full_evolution_log("skill-a")
        assert len(evo_log.entries) == 10, (
            f"Expected 10 entries, got {len(evo_log.entries)} — data lost due to race condition"
        )

    @staticmethod
    @pytest.mark.asyncio
    async def test_skill_lock_isolation(tmp_path: Path):
        """Writes to different skills should not block each other."""
        root = tmp_path / "skills"
        prepare_skill(root, "skill-a")
        prepare_skill(root, "skill-b")
        store = EvolutionStore(str(root))

        rec_a = make_record("ev_a", content="record a")
        rec_b = make_record("ev_b", content="record b")

        # Both should complete without deadlock
        await asyncio.gather(
            store.append_record("skill-a", rec_a),
            store.append_record("skill-b", rec_b),
        )

        log_a = await store.load_full_evolution_log("skill-a")
        log_b = await store.load_full_evolution_log("skill-b")
        assert len(log_a.entries) == 1
        assert len(log_b.entries) == 1


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
        assert "Use this section as an index of lessons learned from previous executions." in skill_md
        assert "For narrative guidance, read the relevant `evolution/*.md#...` detail section." in skill_md
        assert "### Experience Index" in skill_md
        assert "| Summary | Type | Score | Detail |" in skill_md
        assert "body fix" in skill_md
        assert "[evolution/troubleshooting.md#ev_1](evolution/troubleshooting.md#ev_1)" in skill_md
        assert "### Highlighted Evolution Records" not in skill_md
        assert "### Top Experiences" not in skill_md
        assert "### Narrative Guidance" not in skill_md

    @staticmethod
    @pytest.mark.asyncio
    async def test_render_updates_skill_md_index_with_script_assets(tmp_path: Path):
        root = tmp_path / "skills"
        prepare_skill(root, "skill-a", "# Skill A\n\nSome content\n")
        store = EvolutionStore(str(root))

        record = make_record(
            "ev_script",
            target=EvolutionTarget.SCRIPT,
            section="Scripts",
            content="print('validate csv')",
        )
        record.change.script_language = "python"
        record.change.script_purpose = "CSV validation helper"
        record.change.script_filename = "validate_csv.py"

        await store.append_record("skill-a", record)

        skill_md = (root / "skill-a" / "SKILL.md").read_text(encoding="utf-8")
        assert "Scripts are implementation aids, not mandatory steps." in skill_md
        assert "### Script Assets" in skill_md
        assert "### Experience Index" not in skill_md
        assert "### Narrative Guidance" not in skill_md
        assert "evolution/scripts/_index.md" in skill_md
        assert "[evolution/scripts/validate_csv.py](evolution/scripts/validate_csv.py)" in skill_md
        assert "CSV validation helper" in skill_md

    @staticmethod
    @pytest.mark.asyncio
    async def test_full_experience_index_includes_low_score_records_and_anchors(tmp_path: Path):
        root = tmp_path / "skills"
        prepare_skill(root, "skill-a", "# Skill A\n\nSome content\n")
        store = EvolutionStore(str(root))

        low = make_record(
            "ev_low",
            target=EvolutionTarget.BODY,
            section="Troubleshooting",
            content="### Legacy title\n- details",
            summary="Use explicit retry budget before rerunning flaky tools.",
        )
        low.score = 0.2
        high = make_record(
            "ev_high",
            target=EvolutionTarget.DESCRIPTION,
            section="Instructions",
            content="# Match this skill when users mention audits\n- details",
        )
        high.score = 0.9
        await store.append_record("skill-a", low)
        await store.append_record("skill-a", high)

        skill_md = (root / "skill-a" / "SKILL.md").read_text(encoding="utf-8")
        troubleshooting = (root / "skill-a" / "evolution" / "troubleshooting.md").read_text(encoding="utf-8")
        instructions = (root / "skill-a" / "evolution" / "instructions.md").read_text(encoding="utf-8")

        assert "Use explicit retry budget before rerunning flaky tools." in skill_md
        assert "| 0.20 | [evolution/troubleshooting.md#ev_low](evolution/troubleshooting.md#ev_low) |" in skill_md
        assert "Match this skill when users mention audits" in skill_md
        assert "[evolution/instructions.md#ev_high](evolution/instructions.md#ev_high)" in skill_md
        assert '<a id="ev_low"></a>' in troubleshooting
        assert "### [ev_low] Use explicit retry budget before rerunning flaky tools." in troubleshooting
        assert "### Legacy title" in troubleshooting
        assert "- details" in troubleshooting
        assert '<a id="ev_high"></a>' in instructions
        assert "### [ev_high] Match this skill when users mention audits" in instructions

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


class TestEvolutionStoreRecordMaintenance:
    @staticmethod
    @pytest.mark.asyncio
    async def test_delete_mark_merge_and_update_preserve_facade_behavior(tmp_path: Path):
        root = tmp_path / "skills"
        prepare_skill(root, "skill-a")
        store = EvolutionStore(str(root))

        await store.append_record("skill-a", make_record("ev_1", content="one"))
        await store.append_record("skill-a", make_record("ev_2", content="two"))
        await store.append_record("skill-a", make_record("ev_3", content="three"))

        marked = await store.mark_records_applied("skill-a", ["ev_1"])
        merged = await store.merge_records(
            MergeRecordsRequest(
                name="skill-a",
                primary_id="ev_2",
                remove_ids=["ev_3"],
                new_content="two+three",
                new_score=0.9,
            )
        )
        updated = await store.update_record_content("skill-a", "ev_2", "two+three+updated", new_score=0.8)
        deleted = await store.delete_records("skill-a", ["ev_1"])

        evo_log = await store.load_full_evolution_log("skill-a")
        assert marked == 1
        assert merged is not None
        assert merged.change.content == "two+three"
        assert updated is not None
        assert updated.change.content == "two+three+updated"
        assert updated.score == 0.8
        assert deleted == 1
        assert [record.id for record in evo_log.entries] == ["ev_2"]

    @staticmethod
    @pytest.mark.asyncio
    async def test_merge_and_update_clear_stale_summary(tmp_path: Path):
        root = tmp_path / "skills"
        prepare_skill(root, "skill-a")
        store = EvolutionStore(str(root))

        await store.append_record("skill-a", make_record("ev_1", content="one", summary="old one summary"))
        await store.append_record("skill-a", make_record("ev_2", content="two", summary="old two summary"))

        merged = await store.merge_records(
            MergeRecordsRequest(
                name="skill-a",
                primary_id="ev_1",
                remove_ids=["ev_2"],
                new_content="merged content",
            )
        )
        updated = await store.update_record_content("skill-a", "ev_1", "updated content")

        assert merged is not None
        assert merged.summary is None
        assert updated is not None
        assert updated.summary is None

    @staticmethod
    @pytest.mark.asyncio
    async def test_update_record_scores_and_get_records_by_score(tmp_path: Path):
        root = tmp_path / "skills"
        prepare_skill(root, "skill-a")
        store = EvolutionStore(str(root))

        low = make_record("ev_low", content="low")
        high = make_record("ev_high", content="high")
        low.score = 0.2
        high.score = 0.7
        await store.append_record("skill-a", low)
        await store.append_record("skill-a", high)

        updated = await store.update_record_scores(
            "skill-a",
            {
                "ev_low": {
                    "score": 0.9,
                    "usage_stats": {"times_presented": 3, "times_used": 2, "times_positive": 1},
                }
            },
        )
        ranked = await store.get_records_by_score("skill-a", min_score=0.5)

        assert updated == 1
        assert [record.id for record in ranked] == ["ev_low", "ev_high"]
        assert ranked[0].usage_stats.times_presented == 3
        assert ranked[0].usage_stats.times_used == 2

    @staticmethod
    @pytest.mark.asyncio
    async def test_load_records_by_ids_preserves_requested_order_and_skips_missing(tmp_path: Path):
        root = tmp_path / "skills"
        prepare_skill(root, "skill-a")
        store = EvolutionStore(str(root))

        await store.append_record("skill-a", make_record("ev_1", content="one"))
        await store.append_record("skill-a", make_record("ev_2", content="two"))
        await store.append_record("skill-a", make_record("ev_3", content="three"))

        records = await store.load_records_by_ids("skill-a", ["ev_3", "ev_missing", "ev_1"])

        assert [record.id for record in records] == ["ev_3", "ev_1"]


class TestPackSkillForSharing:
    @staticmethod
    def _read_tar_skill_md(package_bytes: bytes) -> str:
        with tarfile.open(fileobj=io.BytesIO(package_bytes), mode="r:gz") as archive:
            for member in archive.getmembers():
                if member.name.endswith("SKILL.md"):
                    extracted = archive.extractfile(member)
                    assert extracted is not None
                    return extracted.read().decode("utf-8")
        raise AssertionError("SKILL.md not found in package")

    @staticmethod
    @pytest.mark.asyncio
    async def test_pack_skill_for_sharing_omits_evolution_index_block(tmp_path: Path):
        root = tmp_path / "skills"
        prepare_skill(root, "skill-a", "# Skill A\n\nContent\n")
        store = EvolutionStore(str(root))

        await store.append_record(
            "skill-a",
            make_record("ev_1", content="body fix", summary="check bounds"),
        )

        local_skill_md = (root / "skill-a" / "SKILL.md").read_text(encoding="utf-8")
        assert "evolution-index-start" in local_skill_md

        package = await store.pack_skill_for_sharing("skill-a")
        packed_skill_md = TestPackSkillForSharing._read_tar_skill_md(package)

        assert "evolution-index-start" not in packed_skill_md
        assert "evolution-index-end" not in packed_skill_md
        assert "Experience Index" not in packed_skill_md
        assert "# Skill A" in packed_skill_md


class TestInstallSkillPackage:
    @staticmethod
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "unsafe_name",
        [
            "../outside",
            "nested/name",
            r"nested\name",
            ".",
            "..",
            "skill.name",
            "skill name",
        ],
    )
    async def test_rejects_unsafe_explicit_name(tmp_path: Path, unsafe_name: str):
        root = tmp_path / "skills"
        store = EvolutionStore(str(root))

        installed = await store.install_skill_package(
            make_skill_package(tmp_path),
            skill_name=unsafe_name,
        )

        assert installed is None
        assert_no_files(root)
        assert not (tmp_path / "outside").exists()

    @staticmethod
    @pytest.mark.asyncio
    async def test_rejects_absolute_explicit_name(tmp_path: Path):
        root = tmp_path / "skills"
        outside = tmp_path / "absolute-target"
        store = EvolutionStore(str(root))

        installed = await store.install_skill_package(
            make_skill_package(tmp_path),
            skill_name=str(outside),
        )

        assert installed is None
        assert_no_files(root)
        assert not outside.exists()

    @staticmethod
    @pytest.mark.asyncio
    async def test_rejects_unsafe_inferred_name(tmp_path: Path):
        root = tmp_path / "skills"
        store = EvolutionStore(str(root))

        installed = await store.install_skill_package(make_named_member_package("./SKILL.md"))

        assert installed is None
        assert_no_files(root)

    @staticmethod
    @pytest.mark.asyncio
    async def test_rejects_symlinked_destination_outside_root(tmp_path: Path):
        root = tmp_path / "skills"
        outside = tmp_path / "outside"
        root.mkdir()
        outside.mkdir()
        try:
            (root / "linked-skill").symlink_to(outside, target_is_directory=True)
        except (NotImplementedError, OSError):
            pytest.skip("directory symlinks are unavailable")
        store = EvolutionStore(str(root))

        installed = await store.install_skill_package(
            make_skill_package(tmp_path),
            skill_name="linked-skill",
        )

        assert installed is None
        assert_no_files(outside)

    @staticmethod
    def test_create_resolution_rejects_path_outside_root(tmp_path: Path):
        root = tmp_path / "skills"
        store = EvolutionStore(str(root))

        assert store.resolve_skill_dir("../outside", create=True) is None


class TestEvolutionStoreArchiveAndCreate:
    @staticmethod
    @pytest.mark.asyncio
    async def test_create_skill_archive_and_clear_keep_facade_stable(tmp_path: Path):
        root = tmp_path / "skills"
        root.mkdir()
        store = EvolutionStore(str(root))

        created = await store.create_skill("skill-a", "desc", "body text")
        assert created == root / "skill-a"

        await store.append_record("skill-a", make_record("ev_1", content="body fix"))
        body_archive = await store.archive_skill_body("skill-a")
        evo_archive = await store.archive_evolutions("skill-a")

        assert body_archive is not None
        assert evo_archive is not None
        assert (root / "skill-a" / "archive" / body_archive).exists()
        assert (root / "skill-a" / "archive" / evo_archive).exists()
        assert store.list_archives("skill-a") == sorted(store.list_archives("skill-a"), reverse=True)

        await store.clear_evolutions("skill-a")
        cleared = await store.load_full_evolution_log("skill-a")
        assert cleared.entries == []

    @staticmethod
    @pytest.mark.asyncio
    async def test_delete_or_clear_last_record_removes_stale_projection_outputs(tmp_path: Path):
        root = tmp_path / "skills"
        root.mkdir()
        store = EvolutionStore(str(root))
        await store.create_skill("skill-a", "desc", "body text")

        record = make_record("ev_1", content="body fix")
        await store.append_record("skill-a", record)

        skill_md_path = root / "skill-a" / "SKILL.md"
        section_path = root / "skill-a" / "evolution" / "troubleshooting.md"
        assert section_path.exists()
        assert "evolution-index-start" in skill_md_path.read_text(encoding="utf-8")

        await store.delete_records("skill-a", [record.id])

        assert not section_path.exists()
        assert "evolution-index-start" not in skill_md_path.read_text(encoding="utf-8")

        await store.append_record("skill-a", make_record("ev_2", content="body fix 2"))
        assert section_path.exists()

        await store.clear_evolutions("skill-a")

        assert not section_path.exists()
        assert "evolution-index-start" not in skill_md_path.read_text(encoding="utf-8")

    @staticmethod
    @pytest.mark.asyncio
    async def test_create_skill_rejects_existing_or_invalid_names(tmp_path: Path):
        root = tmp_path / "skills"
        root.mkdir()
        prepare_skill(root, "skill-a")
        store = EvolutionStore(str(root))

        assert await store.create_skill("skill-a", "desc", "body") is None
        assert await store.create_skill("../escape", "desc", "body") is None

    # ── Subject-aware store Interface ──

    @staticmethod
    @pytest.mark.asyncio
    async def test_subject_aware_resolve_dir_regular_skill(tmp_path: Path):
        root = tmp_path / "skills"
        prepare_skill(root, "foo")
        store = EvolutionStore(str(root))
        path = store.resolve_subject_dir({"kind": "skill", "name": "foo"})
        assert path is not None
        assert path.name == "foo"

    @staticmethod
    @pytest.mark.asyncio
    async def test_subject_aware_resolve_dir_nonexistent(tmp_path: Path):
        root = tmp_path / "skills"
        root.mkdir()
        store = EvolutionStore(str(root))
        assert store.resolve_subject_dir({"kind": "skill", "name": "nonexistent"}) is None

    @staticmethod
    @pytest.mark.asyncio
    async def test_subject_aware_skill_subject_exists(tmp_path: Path):
        root = tmp_path / "skills"
        prepare_skill(root, "foo")
        store = EvolutionStore(str(root))
        assert store.skill_subject_exists({"kind": "skill", "name": "foo"}) is True
        assert store.skill_subject_exists({"kind": "skill", "name": "bar"}) is False

    @staticmethod
    @pytest.mark.asyncio
    async def test_subject_aware_read_content(tmp_path: Path):
        root = tmp_path / "skills"
        prepare_skill(root, "foo", content="# Skill\n\nHello world\n")
        store = EvolutionStore(str(root))
        content = await store.read_subject_content({"kind": "skill", "name": "foo"})
        assert "Hello world" in content

    @staticmethod
    @pytest.mark.asyncio
    async def test_subject_aware_load_evolution_log(tmp_path: Path):
        root = tmp_path / "skills"
        prepare_skill(root, "foo")
        store = EvolutionStore(str(root))
        record = make_record("ev_1", content="test")
        await store.append_subject_record({"kind": "skill", "name": "foo"}, record)
        log = await store.load_subject_evolution_log({"kind": "skill", "name": "foo"})
        assert len(log.entries) == 1
        assert log.entries[0].id == "ev_1"

    @staticmethod
    @pytest.mark.asyncio
    async def test_subject_aware_append_and_load_by_ids(tmp_path: Path):
        root = tmp_path / "skills"
        prepare_skill(root, "foo")
        store = EvolutionStore(str(root))
        record = make_record("ev_1", content="test")
        await store.append_subject_record({"kind": "skill", "name": "foo"}, record)
        records = await store.load_subject_records_by_ids({"kind": "skill", "name": "foo"}, ["ev_1"])
        assert len(records) == 1
        assert records[0].id == "ev_1"

    @staticmethod
    @pytest.mark.asyncio
    async def test_subject_aware_delete_records(tmp_path: Path):
        root = tmp_path / "skills"
        prepare_skill(root, "foo")
        store = EvolutionStore(str(root))
        record = make_record("ev_1", content="test")
        await store.append_subject_record({"kind": "skill", "name": "foo"}, record)
        count = await store.delete_subject_records({"kind": "skill", "name": "foo"}, ["ev_1"])
        assert count == 1

    @staticmethod
    @pytest.mark.asyncio
    async def test_subject_aware_merge_records(tmp_path: Path):
        root = tmp_path / "skills"
        prepare_skill(root, "foo")
        store = EvolutionStore(str(root))
        r1 = make_record("ev_1", content="content 1")
        r2 = make_record("ev_2", content="content 2")
        await store.append_subject_record({"kind": "skill", "name": "foo"}, r1)
        await store.append_subject_record({"kind": "skill", "name": "foo"}, r2)
        merged = await store.merge_subject_records({"kind": "skill", "name": "foo"}, "ev_1", ["ev_2"], "merged content")
        assert merged is not None
        assert merged.change.content == "merged content"

    @staticmethod
    @pytest.mark.asyncio
    async def test_subject_aware_update_record_content(tmp_path: Path):
        root = tmp_path / "skills"
        prepare_skill(root, "foo")
        store = EvolutionStore(str(root))
        record = make_record("ev_1", content="old content")
        await store.append_subject_record({"kind": "skill", "name": "foo"}, record)
        updated = await store.update_subject_record_content({"kind": "skill", "name": "foo"}, "ev_1", "new content")
        assert updated is not None
        assert updated.change.content == "new content"

    @staticmethod
    @pytest.mark.asyncio
    async def test_subject_aware_normalizes_team_skill_alias(tmp_path: Path):
        root = tmp_path / "skills"
        prepare_skill(root, "team-a", content="---\nkind: team-skill\n---\n# Skill\n")
        store = EvolutionStore(str(root))
        path = store.resolve_subject_dir({"kind": "team-skill", "name": "team-a"})
        assert path is not None
        assert path.name == "team-a"
        assert store.skill_subject_exists({"kind": "team-skill", "name": "team-a"}) is True

    @staticmethod
    @pytest.mark.asyncio
    async def test_subject_aware_accepts_evolution_subject_instance(tmp_path: Path):
        from openjiuwen.agent_evolving.experience.draft_schema import EvolutionSubject

        root = tmp_path / "skills"
        prepare_skill(root, "foo")
        store = EvolutionStore(str(root))
        subject = EvolutionSubject(kind="skill", name="foo")
        assert store.skill_subject_exists(subject) is True

    @staticmethod
    @pytest.mark.asyncio
    async def test_subject_aware_existing_name_based_methods_still_work(tmp_path: Path):
        root = tmp_path / "skills"
        prepare_skill(root, "foo")
        store = EvolutionStore(str(root))
        assert store.skill_exists("foo") is True
        content = await store.read_skill_content("foo")
        assert content
        record = make_record("ev_1", content="test")
        await store.append_record("foo", record)
        log = await store.load_full_evolution_log("foo")
        assert len(log.entries) == 1

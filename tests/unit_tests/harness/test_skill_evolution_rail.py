# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
# pylint: disable=protected-access

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from openjiuwen.agent_evolving.checkpointing import EvolutionStore
from openjiuwen.agent_evolving.checkpointing.types import (
    EvolutionPatch,
    EvolutionRecord,
    EvolutionRecordSpec,
    EvolutionTarget,
)
from openjiuwen.agent_evolving.signal import (
    SignalDetector,
    EvolutionSignal,
    EvolutionCategory,
)
from openjiuwen.agent_evolving.trajectory import (
    LLMCallDetail,
    LegacyTrajectory,
    TrajectoryStep,
    trajectory_from_legacy,
)
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ToolCallInputs
from openjiuwen.harness.rails.skill_evolution_rail import (
    SkillEvolutionRail,
    _MAX_PROCESSED_SIGNAL_KEYS,
)


def _make_rail(tmp_path, *, auto_scan: bool = True, auto_save: bool = True) -> SkillEvolutionRail:
    rail = SkillEvolutionRail(
        skills_dir=str(tmp_path / "skills"),
        llm=Mock(),
        model="dummy-model",
        auto_scan=auto_scan,
        auto_save=auto_save,
    )
    rail._evolution_store = Mock()
    rail._evolver = Mock()
    return rail


def _make_record(skill_name: str, *, content: str = "experience content") -> EvolutionRecord:
    return EvolutionRecord.make(
        EvolutionRecordSpec(
            source=f"signal:{skill_name}",
            context="ctx",
            change=EvolutionPatch(
                section="Troubleshooting",
                action="append",
                content=content,
                target=EvolutionTarget.BODY,
            ),
        )
    )


def _make_signal(skill_name: str | None, *, excerpt: str = "signal excerpt") -> EvolutionSignal:
    return EvolutionSignal(
        signal_type="tool_failure",
        evolution_type=EvolutionCategory.SKILL_EXPERIENCE,
        section="Troubleshooting",
        excerpt=excerpt,
        tool_name="bash",
        skill_name=skill_name,
    )


def _sync_returning(value):
    """Build a sync replacement for ``SignalDetector.detect_trajectory_signals``."""

    def _detect_sync(self, trajectory, *, messages=None, signal_types=None):
        return value

    return _detect_sync


def _patch_detected_signals(monkeypatch, signals):
    """Stub trajectory signal detection used inline by ``run_evolution``."""
    monkeypatch.setattr(
        SignalDetector,
        "detect_trajectory_signals",
        _sync_returning(signals),
    )


class _DummyToolMsg:
    def __init__(self, content: Any, *, metadata: dict | None = None):
        self.content = content
        self.metadata = metadata or {}


# =============================================================================
# Basic Utility Tests
# =============================================================================


def test_extract_file_path():
    rail = SkillEvolutionRail(skills_dir="skills", llm=Mock(), model="dummy")
    cases = [
        ({"file_path": "/a/b/SKILL.md"}, "/a/b/SKILL.md"),
        ({}, ""),
        ('{"file_path":"C:/skills/x/SKILL.md"}', "C:/skills/x/SKILL.md"),
        ("not-json", ""),
        (None, ""),
        (123, ""),
    ]
    for args, expected in cases:
        assert rail._extract_file_path(args) == expected


def test_properties_and_clear_processed_signals(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=True)
    rail.processed_signal_keys.add(("a", "b"))
    rail.auto_scan = False
    rail.auto_save = False
    rail.clear_processed_signals()

    assert rail.auto_scan is False
    assert rail.auto_save is False
    assert rail.processed_signal_keys == set()


@pytest.mark.asyncio
async def test_rollback_skill_uses_public_store_interfaces(tmp_path):
    root = tmp_path / "skills"
    skill_dir = root / "skill-a"
    archive = skill_dir / "archive"
    archive.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: skill-a\ndescription: d\nversion: 1.0.1\n---\n\n# Current\n",
        encoding="utf-8",
    )
    (skill_dir / "evolutions.json").write_text(
        '{"skill_id": "skill-a", "version": "1.0.1", "entries": []}',
        encoding="utf-8",
    )
    (archive / "SKILL.v1.0.0.md").write_text("# Archived\n", encoding="utf-8")
    (archive / "evolutions.v1.0.0.json").write_text('{"entries": []}', encoding="utf-8")

    rail = _make_rail(tmp_path)
    rail._evolution_store = EvolutionStore(str(root))

    assert await rail.rollback_skill("skill-a", "SKILL.v1.0.0.md") is True
    assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == "# Archived\n"
    assert (skill_dir / "evolutions.json").read_text(encoding="utf-8") == "{\"entries\": []}"
    assert not (archive / "SKILL.v1.0.0.md").exists()
    assert not (archive / "evolutions.v1.0.0.json").exists()
    current_archives = [
        archive / name
        for name in rail._evolution_store.list_archives("skill-a")
        if name.startswith("SKILL.")
    ]
    assert len(current_archives) == 1
    assert current_archives[0].name == "SKILL.v1.0.1.md"
    assert "# Current\n" in current_archives[0].read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_rollback_skill_accepts_bare_semver(tmp_path):
    root = tmp_path / "skills"
    skill_dir = root / "skill-a"
    archive = skill_dir / "archive"
    archive.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nversion: 1.0.1\n---\n\n# Current\n",
        encoding="utf-8",
    )
    (skill_dir / "evolutions.json").write_text(
        '{"skill_id": "skill-a", "version": "1.0.1", "entries": []}',
        encoding="utf-8",
    )
    (archive / "SKILL.v1.0.0.md").write_text("# Archived\n", encoding="utf-8")
    (archive / "evolutions.v1.0.0.json").write_text('{"entries": ["archived"]}', encoding="utf-8")

    rail = _make_rail(tmp_path)
    rail._evolution_store = EvolutionStore(str(root))

    assert await rail.rollback_skill("skill-a", "1.0.0") is True
    assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == "# Archived\n"
    assert (skill_dir / "evolutions.json").read_text(encoding="utf-8") == '{"entries": ["archived"]}'
    assert not (archive / "SKILL.v1.0.0.md").exists()


@pytest.mark.asyncio
async def test_rollback_skill_without_version_uses_latest_archive(tmp_path):
    root = tmp_path / "skills"
    skill_dir = root / "skill-a"
    archive = skill_dir / "archive"
    archive.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nversion: 1.0.2\n---\n\n# Current\n",
        encoding="utf-8",
    )
    (skill_dir / "evolutions.json").write_text(
        '{"skill_id": "skill-a", "version": "1.0.2", "entries": []}',
        encoding="utf-8",
    )
    older = archive / "SKILL.v1.0.0.md"
    newer = archive / "SKILL.v1.0.1.md"
    older.write_text("# Old\n", encoding="utf-8")
    (archive / "evolutions.v1.0.0.json").write_text('{"entries": ["old"]}', encoding="utf-8")
    newer.write_text("# Latest\n", encoding="utf-8")
    (archive / "evolutions.v1.0.1.json").write_text('{"entries": []}', encoding="utf-8")
    # Ensure mtime order: older file older than newer
    import os
    import time

    older_ts = time.time() - 10
    newer_ts = time.time() - 5
    os.utime(older, (older_ts, older_ts))
    os.utime(newer, (newer_ts, newer_ts))

    rail = _make_rail(tmp_path)
    rail._evolution_store = EvolutionStore(str(root))

    assert await rail.rollback_skill("skill-a") is True
    assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == "# Latest\n"
    assert (skill_dir / "evolutions.json").read_text(encoding="utf-8") == "{\"entries\": []}"
    assert not (archive / "SKILL.v1.0.1.md").exists()
    assert not (archive / "evolutions.v1.0.1.json").exists()
    assert (archive / "SKILL.v1.0.0.md").exists()
    assert (archive / "evolutions.v1.0.0.json").exists()


@pytest.mark.asyncio
async def test_rollback_skill_rejects_invalid_or_missing_version(tmp_path):
    root = tmp_path / "skills"
    skill_dir = root / "skill-a"
    archive = skill_dir / "archive"
    archive.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# Current\n", encoding="utf-8")

    rail = _make_rail(tmp_path)
    rail._evolution_store = EvolutionStore(str(root))

    assert await rail.rollback_skill("skill-a", "../SKILL.v1.0.0.md") is False
    assert await rail.rollback_skill("skill-a", "README.md") is False
    assert await rail.rollback_skill("skill-a", "SKILL.v1.0.0.md") is False
    assert await rail.rollback_skill("missing-skill") is False
    assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == "# Current\n"


@pytest.mark.asyncio
async def test_rollback_skill_returns_false_when_archive_dir_missing(tmp_path):
    root = tmp_path / "skills"
    skill_dir = root / "skill-a"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# Current\n", encoding="utf-8")

    rail = _make_rail(tmp_path)
    rail._evolution_store = EvolutionStore(str(root))

    assert await rail.rollback_skill("skill-a") is False
    assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == "# Current\n"


@pytest.mark.asyncio
async def test_rollback_skill_empty_archived_body_does_not_overwrite(tmp_path):
    root = tmp_path / "skills"
    skill_dir = root / "skill-a"
    archive = skill_dir / "archive"
    archive.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# Current\n", encoding="utf-8")
    (archive / "SKILL.v1.0.0.md").write_text("", encoding="utf-8")

    rail = _make_rail(tmp_path)
    rail._evolution_store = EvolutionStore(str(root))

    assert await rail.rollback_skill("skill-a", "SKILL.v1.0.0.md") is False
    assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == "# Current\n"


@pytest.mark.asyncio
async def test_rollback_skill_clears_evolutions_when_pair_missing(tmp_path):
    root = tmp_path / "skills"
    skill_dir = root / "skill-a"
    archive = skill_dir / "archive"
    archive.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nversion: 1.0.1\n---\n\n# Current\n",
        encoding="utf-8",
    )
    (skill_dir / "evolutions.json").write_text(
        '{"skill_id": "skill-a", "version": "1.0.1", "entries": []}',
        encoding="utf-8",
    )
    (archive / "SKILL.v1.0.0.md").write_text("# Archived\n", encoding="utf-8")

    rail = _make_rail(tmp_path)
    rail._evolution_store = EvolutionStore(str(root))

    assert await rail.rollback_skill("skill-a", "SKILL.v1.0.0.md") is True
    assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == "# Archived\n"
    assert (await rail._evolution_store.load_evolution_log("skill-a")).entries == []
    assert not (archive / "SKILL.v1.0.0.md").exists()


@pytest.mark.asyncio
async def test_rollback_skill_keeps_target_archive_when_restore_fails(tmp_path):
    root = tmp_path / "skills"
    skill_dir = root / "skill-a"
    archive = skill_dir / "archive"
    archive.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nversion: 1.0.1\n---\n\n# Current\n",
        encoding="utf-8",
    )
    (skill_dir / "evolutions.json").write_text(
        '{"skill_id": "skill-a", "version": "1.0.1", "entries": []}',
        encoding="utf-8",
    )
    (archive / "SKILL.v1.0.0.md").write_text("# Archived\n", encoding="utf-8")
    (archive / "evolutions.v1.0.0.json").write_text("{\"entries\": []}", encoding="utf-8")

    rail = _make_rail(tmp_path)
    store = EvolutionStore(str(root))
    store.restore_evolution_log_from_archive = AsyncMock(return_value=False)
    rail._evolution_store = store

    assert await rail.rollback_skill("skill-a", "SKILL.v1.0.0.md") is False
    assert (archive / "SKILL.v1.0.0.md").exists()
    assert (archive / "evolutions.v1.0.0.json").exists()


# =============================================================================
# Skill file resolution helpers
# (_parse_tool_args_dict, _resolve_skill_file_context, _is_skill_md_path, _resolve_skill_name)
# =============================================================================


@pytest.mark.parametrize(
    ("tool_args", "expected"),
    [
        ({"skill_name": "a", "relative_file_path": "SKILL.md"}, {"skill_name": "a", "relative_file_path": "SKILL.md"}),
        ('{"skill_name":"b","relative_file_path":"SKILL"}', {"skill_name": "b", "relative_file_path": "SKILL"}),
        ("not-json", {}),
        ('["array"]', {}),
        (None, {}),
        (42, {}),
    ],
)
def test_parse_tool_args_dict(tool_args, expected):
    assert SkillEvolutionRail._parse_tool_args_dict(tool_args) == expected


@pytest.mark.parametrize(
    ("relative_path", "expected"),
    [
        ("SKILL.md", True),
        ("skill.md", True),
        ("SKILL", True),
        ("./SKILL", True),
        ("./SKILL.md", True),
        (r"subdir\SKILL.md", True),
        ("docs/nested/SKILL.md", True),
        ("docs/REF.md", False),
        ("README.md", False),
        ("", True),  # normalize empty -> SKILL.md
    ],
)
def test_is_skill_md_path(relative_path, expected):
    assert SkillEvolutionRail._is_skill_md_path(relative_path) is expected


@pytest.mark.parametrize(
    ("tool_name", "tool_args", "tool_msg", "expected"),
    [
        (
            "skill_tool",
            {"skill_name": "invoice-parser", "relative_file_path": "SKILL.md"},
            None,
            ("invoice-parser", "SKILL.md"),
        ),
        (
            "skill_tool",
            {"skill_name": "invoice-parser", "relative_file_path": "SKILL"},
            None,
            ("invoice-parser", "SKILL.md"),
        ),
        (
            "skill_tool",
            {"relative_file_path": "SKILL.md"},
            _DummyToolMsg("body", metadata={"skill_name": "from-meta", "relative_file_path": "SKILL"}),
            ("from-meta", "SKILL.md"),
        ),
        (
            "skill_tool",
            {"skill_name": "invoice-parser", "relative_file_path": "docs/REF.md"},
            None,
            ("invoice-parser", "docs/REF.md"),
        ),
        (
            "skill_tool",
            {"relative_file_path": "SKILL.md"},
            None,
            None,
        ),
        (
            "read_file",
            {"file_path": r"C:\skills\invoice-parser\SKILL.md"},
            None,
            ("invoice-parser", "SKILL.md"),
        ),
        (
            "read_file",
            {"file_path": "/workspace/skills/my-skill/SKILL.md"},
            None,
            ("my-skill", "SKILL.md"),
        ),
        (
            "read_file",
            {"file_path": "/workspace/skills/my-skill/README.md"},
            None,
            None,
        ),
        (
            "list_skill",
            {"file_path": "/a/s/SKILL.md"},
            None,
            None,
        ),
        (
            "grep",
            {"pattern": "foo"},
            None,
            None,
        ),
        (
            "read_all",
            {"file_path": "/skills/invoice-parser/SKILL.md"},
            None,
            None,
        ),
        (
            "file_upload",
            {"file_path": "/skills/invoice-parser/SKILL.md"},
            None,
            None,
        ),
    ],
)
def test_resolve_skill_file_context(tool_name, tool_args, tool_msg, expected):
    assert (
        SkillEvolutionRail._resolve_skill_file_context(tool_name, tool_args, tool_msg)
        == expected
    )


@pytest.mark.parametrize(
    ("tool_name", "tool_args", "tool_msg", "expected"),
    [
        ("skill_tool", {"skill_name": "invoice-parser", "relative_file_path": "SKILL.md"}, None, "invoice-parser"),
        ("skill_tool", {"skill_name": "invoice-parser", "relative_file_path": "SKILL"}, None, "invoice-parser"),
        ("skill_tool", {"skill_name": "invoice-parser", "relative_file_path": "./SKILL"}, None, "invoice-parser"),
        (
            "skill_tool",
            {"relative_file_path": "SKILL"},
            _DummyToolMsg("x", metadata={"skill_name": "meta-skill"}),
            "meta-skill",
        ),
        ("skill_tool", {"skill_name": "invoice-parser", "relative_file_path": "docs/REF.md"}, None, None),
        ("skill_tool", {"relative_file_path": "SKILL.md"}, None, None),
        ("read_file", {"file_path": "/skills/demo/SKILL.md"}, None, "demo"),
        ("read_file", {"file_path": "/skills/demo/README.md"}, None, None),
    ],
)
def test_resolve_skill_name(tool_name, tool_args, tool_msg, expected):
    assert SkillEvolutionRail._resolve_skill_name(tool_name, tool_args, tool_msg) == expected


# =============================================================================
# after_tool_call Tests
# =============================================================================


@pytest.mark.asyncio
async def test_after_tool_call_injects_body_experience(tmp_path):
    rail = _make_rail(tmp_path)
    rail._evolution_store.format_body_experience_text = AsyncMock(return_value="\nnew experience")

    inputs = ToolCallInputs(
        tool_name="read_file",
        tool_args={"file_path": r"C:\skills\invoice-parser\SKILL.md"},
        tool_msg=_DummyToolMsg("original"),
    )
    ctx = AgentCallbackContext(agent=None, inputs=inputs, session=None)

    await rail.after_tool_call(ctx)

    assert inputs.tool_msg.content == "original\nnew experience"
    rail._evolution_store.format_body_experience_text.assert_awaited_once_with("invoice-parser")


@pytest.mark.asyncio
async def test_after_tool_call_injects_body_experience_for_skill_tool_bare_skill_path(tmp_path):
    rail = _make_rail(tmp_path)
    rail._evolution_store.format_body_experience_text = AsyncMock(return_value="\nnew experience")

    inputs = ToolCallInputs(
        tool_name="skill_tool",
        tool_args={"skill_name": "invoice-parser", "relative_file_path": "SKILL"},
        tool_msg=_DummyToolMsg("original"),
    )
    ctx = AgentCallbackContext(agent=None, inputs=inputs, session=None)

    await rail.after_tool_call(ctx)

    assert inputs.tool_msg.content == "original\nnew experience"
    rail._evolution_store.format_body_experience_text.assert_awaited_once_with("invoice-parser")


@pytest.mark.asyncio
async def test_after_tool_call_injects_body_experience_for_skill_tool_explicit_path(tmp_path):
    rail = _make_rail(tmp_path)
    rail._evolution_store.format_body_experience_text = AsyncMock(return_value="\nnew experience")

    inputs = ToolCallInputs(
        tool_name="skill_tool",
        tool_args={"skill_name": "invoice-parser", "relative_file_path": "SKILL.md"},
        tool_msg=_DummyToolMsg("original"),
    )
    ctx = AgentCallbackContext(agent=None, inputs=inputs, session=None)

    await rail.after_tool_call(ctx)

    assert inputs.tool_msg.content == "original\nnew experience"
    rail._evolution_store.format_body_experience_text.assert_awaited_once_with("invoice-parser")


@pytest.mark.asyncio
async def test_after_tool_call_injects_body_experience_for_skill_tool_metadata_fallback(tmp_path):
    rail = _make_rail(tmp_path)
    rail._evolution_store.format_body_experience_text = AsyncMock(return_value="\nnew experience")

    inputs = ToolCallInputs(
        tool_name="skill_tool",
        tool_args={"relative_file_path": "SKILL"},
        tool_msg=_DummyToolMsg("original", metadata={"skill_name": "invoice-parser"}),
    )
    ctx = AgentCallbackContext(agent=None, inputs=inputs, session=None)

    await rail.after_tool_call(ctx)

    assert inputs.tool_msg.content == "original\nnew experience"
    rail._evolution_store.format_body_experience_text.assert_awaited_once_with("invoice-parser")


@pytest.mark.asyncio
async def test_after_tool_call_skips_skill_tool_invalid_cases(tmp_path):
    rail = _make_rail(tmp_path)
    rail._evolution_store.format_body_experience_text = AsyncMock(return_value="\nnew experience")
    cases = [
        ToolCallInputs(
            tool_name="skill_tool",
            tool_args={"skill_name": "invoice-parser", "relative_file_path": "docs/REF.md"},
            tool_msg=_DummyToolMsg("x"),
        ),
        ToolCallInputs(
            tool_name="skill_tool",
            tool_args={"relative_file_path": "SKILL.md"},
            tool_msg=_DummyToolMsg("x"),
        ),
        ToolCallInputs(
            tool_name="skill_tool",
            tool_args='{"skill_name":"invoice-parser","relative_file_path":"docs/REF.md"}',
            tool_msg=_DummyToolMsg("x"),
        ),
    ]
    for item in cases:
        ctx = AgentCallbackContext(agent=None, inputs=item, session=None)
        await rail.after_tool_call(ctx)

    rail._evolution_store.format_body_experience_text.assert_not_awaited()
    for item in cases:
        assert item.tool_msg.content == "x"


@pytest.mark.asyncio
async def test_after_tool_call_skips_invalid_cases(tmp_path):
    rail = _make_rail(tmp_path)
    rail._evolution_store.format_body_experience_text = AsyncMock(return_value="\nnew experience")
    cases = [
        ToolCallInputs(tool_name="list_skill", tool_args={"file_path": "/a/s/SKILL.md"}, tool_msg=_DummyToolMsg("x")),
        ToolCallInputs(tool_name="read_file", tool_args={"file_path": "/a/s/README.md"}, tool_msg=_DummyToolMsg("x")),
        ToolCallInputs(tool_name="read_file", tool_args={"file_path": "/a/s/SKILL.md"}, tool_msg=None),
    ]
    for item in cases:
        ctx = AgentCallbackContext(agent=None, inputs=item, session=None)
        await rail.after_tool_call(ctx)

    rail._evolution_store.format_body_experience_text.assert_awaited_once_with("s")
    assert cases[0].tool_msg.content == "x"
    assert cases[1].tool_msg.content == "x"

    rail._evolution_store.format_body_experience_text = AsyncMock(return_value="")
    empty_case = ToolCallInputs(
        tool_name="read_file",
        tool_args={"file_path": "/a/demo/SKILL.md"},
        tool_msg=_DummyToolMsg("z"),
    )
    await rail.after_tool_call(AgentCallbackContext(agent=None, inputs=empty_case, session=None))
    assert empty_case.tool_msg.content == "z"


# =============================================================================
# after_invoke Tests
# =============================================================================


@pytest.mark.asyncio
async def test_run_evolution_returns_immediately_when_auto_scan_disabled(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=False)
    rail._collect_parsed_messages = AsyncMock(return_value=[{"role": "user", "content": "x"}])
    await rail.run_evolution(None, AgentCallbackContext(agent=None, inputs=None, session=None))
    rail._collect_parsed_messages.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_evolution_auto_save_appends_records(tmp_path, monkeypatch):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=True)
    signals = [_make_signal("skill-a")]
    records = [_make_record("skill-a"), _make_record("skill-a")]

    rail._collect_parsed_messages = AsyncMock(return_value=[{"role": "user", "content": "hello"}])
    rail._evolution_store.list_skill_names = Mock(return_value=["skill-a"])
    rail._evolution_store.skill_exists = Mock(return_value=True)
    _patch_detected_signals(monkeypatch, signals)
    rail._generate_experience_for_skill = AsyncMock(return_value=records)
    rail._evolution_store.append_record = AsyncMock()
    rail._emit_generated_records = AsyncMock()
    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)

    await rail.run_evolution(None, ctx)

    assert rail._evolution_store.append_record.await_count == 2
    rail._evolution_store.append_record.assert_any_await("skill-a", records[0])
    rail._evolution_store.append_record.assert_any_await("skill-a", records[1])
    rail._emit_generated_records.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_evolution_auto_save_false_emits_events(tmp_path, monkeypatch):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=False)
    signals = [_make_signal("skill-a")]
    parsed_messages = [{"role": "user", "content": "hello"}]

    rail._collect_parsed_messages = AsyncMock(return_value=parsed_messages)
    rail._evolution_store.list_skill_names = Mock(return_value=["skill-a"])
    rail._evolution_store.skill_exists = Mock(return_value=True)
    _patch_detected_signals(monkeypatch, signals)
    rail._generate_experience_via_optimizer = AsyncMock(return_value=True)
    rail._emit_generated_records = AsyncMock()
    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)

    await rail.run_evolution(None, ctx)

    rail._generate_experience_via_optimizer.assert_awaited_once_with("skill-a", signals, parsed_messages)
    rail._emit_generated_records.assert_awaited_once_with(ctx, "skill-a")


@pytest.mark.asyncio
async def test_run_evolution_filters_empty_skill_name_and_swallow_exceptions(tmp_path, monkeypatch):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=True)
    rail._collect_parsed_messages = AsyncMock(return_value=[{"role": "user", "content": "hello"}])
    rail._evolution_store.list_skill_names = Mock(return_value=["skill-a"])
    rail._evolution_store.skill_exists = Mock(return_value=True)
    _patch_detected_signals(monkeypatch, [_make_signal(None), _make_signal("skill-a")])
    rail._generate_experience_for_skill = AsyncMock(return_value=[])
    rail._evolution_store.append_record = AsyncMock()

    await rail.run_evolution(None, AgentCallbackContext(agent=None, inputs=None, session=None))
    rail._generate_experience_for_skill.assert_awaited_once()
    assert rail._generate_experience_for_skill.await_args.args[0] == "skill-a"

    rail2 = _make_rail(tmp_path, auto_scan=True)
    rail2._collect_parsed_messages = AsyncMock(side_effect=RuntimeError("boom"))
    await rail2.run_evolution(None, AgentCallbackContext(agent=None, inputs=None, session=None))


# =============================================================================
# Inline signal detection Tests
# =============================================================================


@pytest.mark.asyncio
async def test_run_evolution_deduplicates_with_processed_keys(tmp_path, monkeypatch):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=True)
    rail._evolution_store.skill_exists = Mock(return_value=True)
    rail._evolution_store.list_skill_names = Mock(return_value=["skill-a"])
    rail._collect_parsed_messages = AsyncMock(return_value=[{"role": "user", "content": "x"}])
    rail._generate_experience_for_skill = AsyncMock(return_value=[])
    signal = _make_signal("skill-a", excerpt="same-excerpt")
    _patch_detected_signals(monkeypatch, [signal])
    traj = _make_trajectory_with_messages([{"role": "user", "content": "x"}])
    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)

    await rail.run_evolution(traj, ctx)
    assert rail._generate_experience_for_skill.await_count == 1
    assert ("tool_failure", "bash", "skill-a", "same-excerpt") in rail.processed_signal_keys

    rail._infer_primary_skill = Mock(return_value=None)
    await rail.run_evolution(traj, ctx)
    # Second pass filters the same fingerprint; with no attributed signals, review is cancelled.
    assert rail._generate_experience_for_skill.await_count == 1


@pytest.mark.asyncio
async def test_run_evolution_clears_processed_keys_when_exceed_limit(tmp_path, monkeypatch):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=True)
    rail._evolution_store.skill_exists = Mock(return_value=True)
    rail._evolution_store.list_skill_names = Mock(return_value=["skill-a"])
    rail._collect_parsed_messages = AsyncMock(return_value=[{"role": "user", "content": "x"}])
    rail._generate_experience_for_skill = AsyncMock(return_value=[])
    rail._processed_signal_keys = {(f"type-{i}", f"excerpt-{i}") for i in range(_MAX_PROCESSED_SIGNAL_KEYS)}
    _patch_detected_signals(monkeypatch, [_make_signal("skill-a", excerpt="new-one")])
    traj = _make_trajectory_with_messages([{"role": "user", "content": "x"}])

    await rail.run_evolution(traj, AgentCallbackContext(agent=None, inputs=None, session=None))

    assert rail.processed_signal_keys == set()


@pytest.mark.asyncio
async def test_run_evolution_binds_evolver_llm_to_detector(tmp_path, monkeypatch):
    """run_evolution must bind evolver llm/model/language then call
    ``detect_trajectory_signals`` with hard signal types only."""
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=True)
    rail._evolution_store.skill_exists = Mock(return_value=True)
    rail._evolution_store.list_skill_names = Mock(return_value=["skill-a"])
    rail._collect_parsed_messages = AsyncMock(return_value=[{"role": "user", "content": "x"}])
    rail._generate_experience_for_skill = AsyncMock(return_value=[])
    captured: dict = {}
    traj = _make_trajectory_with_messages([{"role": "user", "content": "x"}])

    original_bind = SignalDetector.bind_llm

    def spy_bind(self: Any, *, llm: Any, model: str, language: str = "cn") -> Any:
        captured["llm"] = llm
        captured["model"] = model
        captured["language"] = language
        return original_bind(self, llm=llm, model=model, language=language)

    def spy_detect(self: Any, trajectory, *, messages=None, signal_types=None):
        captured["trajectory"] = trajectory
        captured["signal_types"] = signal_types
        captured["existing_skills"] = set(self._existing_skills)
        return [_make_signal("skill-a", excerpt="x")]

    monkeypatch.setattr(SignalDetector, "bind_llm", spy_bind)
    monkeypatch.setattr(SignalDetector, "detect_trajectory_signals", spy_detect)

    await rail.run_evolution(traj, AgentCallbackContext(agent=None, inputs=None, session=None))

    assert captured["llm"] is rail._evolver.llm
    assert captured["model"] == rail._evolver.model
    assert captured["language"] == rail._language
    assert captured["existing_skills"] == {"skill-a"}
    assert captured["trajectory"] is traj
    assert captured["signal_types"] == {"execution_failure", "script_artifact"}


# =============================================================================
# _collect_parsed_messages Tests
# =============================================================================


def _make_trajectory_with_messages(messages: list[dict]):
    return trajectory_from_legacy(
        LegacyTrajectory(
            execution_id="test-exec",
            steps=[
                TrajectoryStep(
                    kind="llm",
                    detail=LLMCallDetail(model="dummy", messages=messages),
                )
            ],
        )
    )


@pytest.mark.asyncio
async def test_collect_parsed_messages_from_trajectory(tmp_path):
    rail = _make_rail(tmp_path)
    traj = _make_trajectory_with_messages([{"role": "user", "content": "q"}])
    parsed = await rail._collect_parsed_messages(traj)
    assert parsed == [{"role": "user", "content": "q"}]


@pytest.mark.asyncio
async def test_collect_parsed_messages_returns_empty_for_none_trajectory(tmp_path):
    rail = _make_rail(tmp_path)
    parsed = await rail._collect_parsed_messages(None)
    assert parsed == []


@pytest.mark.asyncio
async def test_collect_parsed_messages_returns_empty_for_empty_trajectory(tmp_path):
    rail = _make_rail(tmp_path)
    traj = trajectory_from_legacy(LegacyTrajectory(execution_id="empty", steps=[]))
    parsed = await rail._collect_parsed_messages(traj)
    assert parsed == []


# =============================================================================
# _generate_experience_for_skill / event buffer Tests
# =============================================================================


@pytest.mark.asyncio
async def test_generate_experience_for_skill_builds_context(tmp_path):
    rail = _make_rail(tmp_path)
    signal = _make_signal("skill-a")
    old_desc = _make_record("skill-a", content="old desc")
    old_body = _make_record("skill-a", content="old body")
    new_record = _make_record("skill-a", content="new")

    rail._evolution_store.read_skill_content = AsyncMock(return_value="# skill")
    rail._evolution_store.get_pending_records = AsyncMock(side_effect=[[old_desc], [old_body]])
    rail._evolver.generate_records = AsyncMock(return_value=[new_record])

    result = await rail._generate_experience_for_skill("skill-a", [signal], [{"role": "user", "content": "x"}])

    assert result == [new_record]
    evo_ctx = rail._evolver.generate_records.await_args.args[0]
    assert evo_ctx.skill_name == "skill-a"
    assert evo_ctx.signals == [signal]
    assert evo_ctx.skill_content == "# skill"
    assert evo_ctx.existing_desc_records == [old_desc]
    assert evo_ctx.existing_body_records == [old_body]
    assert isinstance(evo_ctx.tool_call_chain, str)
    assert len(evo_ctx.tool_call_chain) > 0


@pytest.mark.asyncio
async def test_generate_experience_for_skill_returns_empty_on_evolver_exception(tmp_path):
    rail = _make_rail(tmp_path)
    rail._evolution_store.read_skill_content = AsyncMock(return_value="# skill")
    rail._evolution_store.get_pending_records = AsyncMock(return_value=[])
    rail._evolver.generate_records = AsyncMock(side_effect=RuntimeError("llm fail"))

    result = await rail._generate_experience_for_skill("skill-a", [_make_signal("skill-a")], [])
    assert result == []


@pytest.mark.asyncio
async def test_emit_generated_records_and_drain_pending_events(tmp_path):
    from openjiuwen.core.operator.skill_call import SkillCallOperator

    rail = _make_rail(tmp_path, auto_save=False)
    record = _make_record("skill-a", content="x" * 1200)
    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)

    # Set up SkillCallOperator with staged records as the new path does
    skill_op = SkillCallOperator("skill-a")
    skill_op._staged_records = [record]
    rail._skill_ops["skill-a"] = skill_op

    await rail._emit_generated_records(ctx, "skill-a")
    events = rail.drain_pending_approval_events()

    assert len(events) == 1
    event = events[0]
    assert event.type == "chat.ask_user_question"
    request_id = event.payload["request_id"]
    # request_id uses the "skill_evolve_" prefix (PendingChange.change_id)
    assert request_id.startswith("skill_evolve_")
    assert event.payload["questions"][0]["header"] == "技能演进审批"
    assert event.payload["_evolution_meta"]["skill_name"] == "skill-a"
    assert event.payload["_evolution_meta"]["request_id"] == request_id
    # Records should have been snapshotted (moved out of staged queue)
    assert skill_op._staged_records == []
    assert request_id in rail._pending_approval_snapshots
    assert rail.drain_pending_approval_events() == []


@pytest.mark.asyncio
async def test_on_approve_flushes_snapshot_records(tmp_path):
    from openjiuwen.core.operator.skill_call import SkillCallOperator

    rail = _make_rail(tmp_path, auto_save=False)
    record = _make_record("skill-a")
    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)

    skill_op = SkillCallOperator("skill-a")
    skill_op._staged_records = [record]
    rail._skill_ops["skill-a"] = skill_op
    rail._evolution_store.append_record = AsyncMock()
    rail._evolution_store.solidify = AsyncMock()

    await rail._emit_generated_records(ctx, "skill-a")
    events = rail.drain_pending_approval_events()
    request_id = events[0].payload["request_id"]

    await rail.on_approve(request_id)

    rail._evolution_store.append_record.assert_awaited_once_with("skill-a", record)
    rail._evolution_store.solidify.assert_awaited_once_with("skill-a")
    assert request_id not in rail._pending_approval_snapshots


@pytest.mark.asyncio
async def test_on_reject_discards_snapshot_records(tmp_path):
    from openjiuwen.core.operator.skill_call import SkillCallOperator

    rail = _make_rail(tmp_path, auto_save=False)
    record = _make_record("skill-a")
    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)

    skill_op = SkillCallOperator("skill-a")
    skill_op._staged_records = [record]
    rail._skill_ops["skill-a"] = skill_op
    rail._evolution_store.append_record = AsyncMock()
    rail._evolution_store.solidify = AsyncMock()

    await rail._emit_generated_records(ctx, "skill-a")
    events = rail.drain_pending_approval_events()
    request_id = events[0].payload["request_id"]

    await rail.on_reject(request_id)

    rail._evolution_store.append_record.assert_not_awaited()
    rail._evolution_store.solidify.assert_not_awaited()
    assert request_id not in rail._pending_approval_snapshots


@pytest.mark.asyncio
async def test_on_approve_partial_failure_retains_pending_change(tmp_path):
    """If append_record fails mid-batch, PendingChange must be kept for retry."""
    from openjiuwen.core.operator.skill_call import SkillCallOperator

    rail = _make_rail(tmp_path, auto_save=False)
    record_1 = _make_record("skill-a", content="first")
    record_2 = _make_record("skill-a", content="second")
    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)

    skill_op = SkillCallOperator("skill-a")
    skill_op._staged_records = [record_1, record_2]
    rail._skill_ops["skill-a"] = skill_op
    rail._evolution_store.solidify = AsyncMock()
    # First call succeeds, second raises → partial failure
    rail._evolution_store.append_record = AsyncMock(side_effect=[None, OSError("disk full")])

    await rail._emit_generated_records(ctx, "skill-a")
    request_id = rail.drain_pending_approval_events()[0].payload["request_id"]

    await rail.on_approve(request_id)

    # Only first record was written; second is still pending
    assert rail._evolution_store.append_record.await_count == 2
    rail._evolution_store.solidify.assert_not_awaited()
    # PendingChange must be retained so host can retry
    assert request_id in rail._pending_approval_snapshots
    pending = rail._pending_approval_snapshots[request_id]
    assert len(pending.payload) == 1  # one record still remaining

    # Host retries: now second record succeeds
    rail._evolution_store.append_record = AsyncMock()
    await rail.on_approve(request_id)
    rail._evolution_store.append_record.assert_awaited_once_with("skill-a", record_2)
    rail._evolution_store.solidify.assert_awaited_once_with("skill-a")
    assert request_id not in rail._pending_approval_snapshots


@pytest.mark.asyncio
async def test_on_approve_solidify_failure_retains_pending_change(tmp_path):
    """If solidify() fails after flush succeeds, PendingChange must be kept for retry."""
    from openjiuwen.core.operator.skill_call import SkillCallOperator

    rail = _make_rail(tmp_path, auto_save=False)
    record = _make_record("skill-a", content="experience")
    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)

    skill_op = SkillCallOperator("skill-a")
    skill_op._staged_records = [record]
    rail._skill_ops["skill-a"] = skill_op
    rail._evolution_store.append_record = AsyncMock()
    rail._evolution_store.solidify = AsyncMock(side_effect=OSError("permission denied"))

    await rail._emit_generated_records(ctx, "skill-a")
    request_id = rail.drain_pending_approval_events()[0].payload["request_id"]

    await rail.on_approve(request_id)

    # append_record succeeded; solidify raised
    rail._evolution_store.append_record.assert_awaited_once_with("skill-a", record)
    rail._evolution_store.solidify.assert_awaited_once_with("skill-a")
    # Snapshot must be preserved so host can retry on_approve
    assert request_id in rail._pending_approval_snapshots

    # Host retries: flush is a no-op (payload empty), solidify succeeds
    rail._evolution_store.solidify = AsyncMock()
    await rail.on_approve(request_id)
    rail._evolution_store.solidify.assert_awaited_once_with("skill-a")
    assert request_id not in rail._pending_approval_snapshots


@pytest.mark.asyncio
async def test_concurrent_approval_batches_are_independent(tmp_path):
    """Two approval prompts for the same skill must operate on disjoint record batches."""
    from openjiuwen.core.operator.skill_call import SkillCallOperator

    rail = _make_rail(tmp_path, auto_save=False)
    record_a = _make_record("skill-a", content="batch-1")
    record_b = _make_record("skill-a", content="batch-2")
    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)

    skill_op = SkillCallOperator("skill-a")
    rail._skill_ops["skill-a"] = skill_op
    rail._evolution_store.append_record = AsyncMock()
    rail._evolution_store.solidify = AsyncMock()

    # First batch: stage record_a and emit prompt
    skill_op._staged_records = [record_a]
    await rail._emit_generated_records(ctx, "skill-a")

    # Second batch: stage record_b and emit another prompt (same skill)
    skill_op._staged_records = [record_b]
    await rail._emit_generated_records(ctx, "skill-a")

    events = rail.drain_pending_approval_events()
    assert len(events) == 2
    req1 = events[0].payload["request_id"]
    req2 = events[1].payload["request_id"]

    # Approving the first prompt should write only record_a
    await rail.on_approve(req1)
    rail._evolution_store.append_record.assert_awaited_once_with("skill-a", record_a)
    rail._evolution_store.append_record.reset_mock()

    # Approving the second prompt should write only record_b
    await rail.on_approve(req2)
    rail._evolution_store.append_record.assert_awaited_once_with("skill-a", record_b)


# =============================================================================
# _infer_primary_skill Tests
# =============================================================================


def test_infer_primary_skill_picks_most_frequent(tmp_path):
    rail = _make_rail(tmp_path)
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"arguments": "/skills/skill-a/SKILL.md"},
                {"arguments": "/skills/skill-b/SKILL.md"},
            ],
        },
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"arguments": "/skills/skill-a/SKILL.md"},
            ],
        },
    ]
    result = rail._infer_primary_skill(messages, ["skill-a", "skill-b"])
    assert result == "skill-a"


def test_infer_primary_skill_from_skill_tool_openai_nested(tmp_path):
    rail = _make_rail(tmp_path)
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "type": "function",
                    "function": {
                        "name": "skill_tool",
                        "arguments": '{"skill_name":"weather","relative_file_path":"SKILL.md"}',
                    },
                }
            ],
        },
    ]
    result = rail._infer_primary_skill(messages, ["weather", "other"])
    assert result == "weather"


def test_infer_primary_skill_returns_none_when_no_match(tmp_path):
    rail = _make_rail(tmp_path)
    messages = [
        {"role": "user", "content": "just chatting"},
    ]
    result = rail._infer_primary_skill(messages, ["skill-a"])
    assert result is None


def test_infer_primary_skill_from_tool_result_content(tmp_path):
    rail = _make_rail(tmp_path)
    messages = [
        {"role": "tool", "content": "Content of /skills/skill-x/SKILL.md: ..."},
    ]
    result = rail._infer_primary_skill(messages, ["skill-x"])
    assert result == "skill-x"


def test_infer_primary_skill_ignores_unknown_skills(tmp_path):
    rail = _make_rail(tmp_path)
    messages = [
        {"role": "tool", "content": "/skills/unknown-skill/SKILL.md"},
    ]
    result = rail._infer_primary_skill(messages, ["skill-a"])
    assert result is None


# =============================================================================
# Zero-signal fallback & unattributed signal tests
# =============================================================================


@pytest.mark.asyncio
async def test_run_evolution_zero_signals_skips_experience_generation(tmp_path, monkeypatch):
    """Zero rule-based signals currently skip conversation_review fallback."""
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=True)

    rail._collect_parsed_messages = AsyncMock(
        return_value=[
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"arguments": "/skills/skill-a/SKILL.md"},
                ],
            },
            {"role": "user", "content": "hello"},
        ]
    )
    rail._evolution_store.list_skill_names = Mock(return_value=["skill-a"])
    rail._evolution_store.skill_exists = Mock(return_value=True)
    _patch_detected_signals(monkeypatch, [])
    rail._infer_primary_skill = Mock(return_value="skill-a")
    rail._generate_experience_for_skill = AsyncMock(return_value=[])
    rail._evolution_store.append_record = AsyncMock()

    await rail.run_evolution(None, AgentCallbackContext(agent=None, inputs=None, session=None))

    rail._generate_experience_for_skill.assert_not_awaited()
    rail._infer_primary_skill.assert_not_called()


@pytest.mark.asyncio
async def test_run_evolution_zero_signals_no_primary_skill_returns(tmp_path, monkeypatch):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=True)

    rail._collect_parsed_messages = AsyncMock(return_value=[{"role": "user", "content": "hi"}])
    rail._evolution_store.list_skill_names = Mock(return_value=["skill-a"])
    rail._evolution_store.skill_exists = Mock(return_value=True)
    _patch_detected_signals(monkeypatch, [])
    rail._infer_primary_skill = Mock(return_value=None)
    rail._generate_experience_for_skill = AsyncMock()

    await rail.run_evolution(None, AgentCallbackContext(agent=None, inputs=None, session=None))

    rail._generate_experience_for_skill.assert_not_awaited()
    rail._infer_primary_skill.assert_not_called()


@pytest.mark.asyncio
async def test_run_evolution_unattributed_signals_get_fallback_skill(tmp_path, monkeypatch):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=True)

    attributed = _make_signal("skill-a", excerpt="attributed")
    unattributed = _make_signal(None, excerpt="unattributed")

    rail._collect_parsed_messages = AsyncMock(return_value=[{"role": "user", "content": "hi"}])
    rail._evolution_store.list_skill_names = Mock(return_value=["skill-a"])
    rail._evolution_store.skill_exists = Mock(return_value=True)
    _patch_detected_signals(monkeypatch, [attributed, unattributed])
    rail._generate_experience_for_skill = AsyncMock(return_value=[])
    rail._evolution_store.append_record = AsyncMock()

    await rail.run_evolution(None, AgentCallbackContext(agent=None, inputs=None, session=None))

    assert unattributed.skill_name == "skill-a"
    rail._generate_experience_for_skill.assert_awaited_once()
    call_args = rail._generate_experience_for_skill.await_args
    assert len(call_args.args[1]) == 2


@pytest.mark.asyncio
async def test_run_evolution_all_unattributed_signals_cancel_review(tmp_path, monkeypatch):
    """When every signal lacks skill_name, cancel regular skill evolution (no primary infer)."""
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=True)
    unattributed = _make_signal(None, excerpt="correction")

    rail._collect_parsed_messages = AsyncMock(return_value=[{"role": "user", "content": "hi"}])
    rail._evolution_store.list_skill_names = Mock(return_value=["skill-a"])
    rail._evolution_store.skill_exists = Mock(return_value=True)
    _patch_detected_signals(monkeypatch, [unattributed])
    rail._infer_primary_skill = Mock(return_value="skill-a")
    rail._generate_experience_for_skill = AsyncMock(return_value=[])
    rail._evolution_store.append_record = AsyncMock()
    rail._should_propose_new_skill = AsyncMock(return_value=True)
    rail._emit_new_skill_create_suggestion = AsyncMock()

    await rail.run_evolution(None, AgentCallbackContext(agent=None, inputs=None, session=None))

    assert unattributed.skill_name is None
    rail._infer_primary_skill.assert_not_called()
    rail._generate_experience_for_skill.assert_not_awaited()
    rail._should_propose_new_skill.assert_not_awaited()
    rail._emit_new_skill_create_suggestion.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_evolution_multiple_attributed_skills_no_fallback(tmp_path, monkeypatch):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=True)

    sig_a = _make_signal("skill-a", excerpt="a")
    sig_b = _make_signal("skill-b", excerpt="b")
    sig_none = _make_signal(None, excerpt="none")

    rail._collect_parsed_messages = AsyncMock(return_value=[{"role": "user", "content": "hi"}])
    rail._evolution_store.list_skill_names = Mock(return_value=["skill-a", "skill-b"])
    rail._evolution_store.skill_exists = Mock(return_value=True)
    _patch_detected_signals(monkeypatch, [sig_a, sig_b, sig_none])
    rail._generate_experience_for_skill = AsyncMock(return_value=[])
    rail._evolution_store.append_record = AsyncMock()

    await rail.run_evolution(None, AgentCallbackContext(agent=None, inputs=None, session=None))

    assert sig_none.skill_name is None
    assert rail._generate_experience_for_skill.await_count == 2


# =============================================================================
# _should_propose_new_skill Tests
# =============================================================================


@pytest.mark.asyncio
async def test_should_propose_new_skill_meets_thresholds(tmp_path):
    rail = SkillEvolutionRail(
        skills_dir=str(tmp_path / "skills"),
        llm=Mock(),
        model="dummy-model",
        new_skill_tool_threshold=10,
        new_skill_tool_diversity=2,
    )
    rail._evolution_store = Mock()
    rail._evolver = Mock()
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"name": "read_file", "arguments": ""},
                {"name": "write_file", "arguments": ""},
                {"name": "run_bash", "arguments": ""},
                {"name": "grep", "arguments": ""},
                {"name": "ls", "arguments": ""},
                {"name": "read_file", "arguments": ""},
                {"name": "write_file", "arguments": ""},
                {"name": "python", "arguments": ""},
                {"name": "git", "arguments": ""},
                {"name": "read_file", "arguments": ""},
            ],
        },
    ]
    result = await rail._should_propose_new_skill(messages)
    assert result is True


@pytest.mark.asyncio
async def test_should_propose_new_skill_below_tool_count_threshold(tmp_path):
    """Fewer tool calls than new_skill_tool_threshold should not propose."""
    rail = SkillEvolutionRail(
        skills_dir=str(tmp_path / "skills"),
        llm=Mock(),
        model="dummy-model",
        new_skill_tool_threshold=10,
        new_skill_tool_diversity=2,
    )
    rail._evolution_store = Mock()
    rail._evolver = Mock()
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"name": "read_file", "arguments": ""},
            ],
        },
    ]
    result = await rail._should_propose_new_skill(messages)
    assert result is False


@pytest.mark.asyncio
async def test_should_propose_new_skill_insufficient_diversity(tmp_path):
    """Enough tool calls but unique tools below diversity threshold."""
    rail = SkillEvolutionRail(
        skills_dir=str(tmp_path / "skills"),
        llm=Mock(),
        model="dummy-model",
        new_skill_tool_threshold=10,
        new_skill_tool_diversity=2,
    )
    rail._evolution_store = Mock()
    rail._evolver = Mock()
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"name": "read_file", "arguments": ""},
                {"name": "read_file", "arguments": ""},
                {"name": "read_file", "arguments": ""},
                {"name": "read_file", "arguments": ""},
                {"name": "read_file", "arguments": ""},
                {"name": "read_file", "arguments": ""},
                {"name": "read_file", "arguments": ""},
                {"name": "read_file", "arguments": ""},
                {"name": "read_file", "arguments": ""},
                {"name": "read_file", "arguments": ""},
            ],
        },
    ]
    result = await rail._should_propose_new_skill(messages)
    assert result is False


@pytest.mark.asyncio
async def test_should_propose_new_skill_no_tool_calls(tmp_path):
    rail = _make_rail(tmp_path)
    messages = [{"role": "user", "content": "no tools used"}]
    result = await rail._should_propose_new_skill(messages)
    assert result is False


# =============================================================================
# _emit_new_skill_create_suggestion Tests
# =============================================================================


@pytest.mark.asyncio
async def test_emit_new_skill_create_suggestion_manual_emits_follow_up_event(tmp_path):
    """auto_save=False: emits skill_creator_follow_up with ask_user_question."""
    rail = _make_rail(tmp_path, auto_save=False)
    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)

    # Ensure skill-creator is "installed" for the check
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir(exist_ok=True)
    (skills_dir / "skill-creator").mkdir(exist_ok=True)
    (skills_dir / "skill-creator" / "SKILL.md").write_text("# skill-creator\n")

    # Rebuild rail with real path
    rail = SkillEvolutionRail(
        skills_dir=str(skills_dir),
        llm=Mock(),
        model="dummy-model",
        auto_save=False,
    )
    rail._evolution_store = Mock()
    rail._evolution_store.base_dir = skills_dir
    rail._evolver = Mock()

    await rail._emit_new_skill_create_suggestion(ctx)

    events = rail.drain_pending_approval_events()
    assert len(events) == 1
    event = events[0]
    payload = event.payload
    assert payload["action"] == "skill_creator_follow_up"
    assert payload["skill_creator_prompt"]
    assert "skill-creator" in payload["skill_creator_prompt"]
    assert "ask_user" in payload["skill_creator_prompt"]
    assert payload["skill_create_meta"]["auto_save"] is False
    assert payload["questions"]  # ask_user_question


@pytest.mark.asyncio
async def test_emit_new_skill_create_suggestion_auto_save_emits_follow_up(tmp_path):
    """auto_save=True: emits skill_creator_follow_up directly without ask_user."""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir(exist_ok=True)
    (skills_dir / "skill-creator").mkdir(exist_ok=True)
    (skills_dir / "skill-creator" / "SKILL.md").write_text("# skill-creator\n")

    rail = SkillEvolutionRail(
        skills_dir=str(skills_dir),
        llm=Mock(),
        model="dummy-model",
        auto_save=True,
    )
    rail._evolution_store = Mock()
    rail._evolution_store.base_dir = skills_dir
    rail._evolver = Mock()

    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)
    await rail._emit_new_skill_create_suggestion(ctx)

    events = rail.drain_pending_approval_events()
    assert len(events) == 1
    event = events[0]
    # Dedicated type — not chat.ask_user_question (no empty questions UI).
    assert event.type == "skill_creator_follow_up"
    payload = event.payload
    assert payload["action"] == "skill_creator_follow_up"
    assert "questions" not in payload
    assert payload["skill_creator_prompt"]
    assert "skill-creator" in payload["skill_creator_prompt"]
    assert "ask_user" not in payload["skill_creator_prompt"]
    assert "无需再次向用户确认" in payload["skill_creator_prompt"]
    assert payload["skill_create_meta"]["auto_save"] is True
    # Empty name would be a no-op in _record_new_skill; proposal_id must be recorded.
    request_id = payload["request_id"]
    assert rail._run_summary is not None
    assert rail._run_summary["new_skills"] == [
        {
            "name": request_id,
            "description": "",
            "reason": payload["skill_create_meta"]["reason"],
        }
    ]


@pytest.mark.asyncio
async def test_emit_new_skill_create_suggestion_uses_english_reason_and_question(tmp_path):
    """English rail should not inject Chinese reason/question text."""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir(exist_ok=True)
    (skills_dir / "skill-creator").mkdir(exist_ok=True)
    (skills_dir / "skill-creator" / "SKILL.md").write_text("# skill-creator\n")

    rail = SkillEvolutionRail(
        skills_dir=str(skills_dir),
        llm=Mock(),
        model="dummy-model",
        auto_save=False,
        language="en",
    )
    rail._evolution_store = Mock()
    rail._evolution_store.base_dir = skills_dir
    rail._evolver = Mock()

    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)
    await rail._emit_new_skill_create_suggestion(ctx)

    payload = rail.drain_pending_approval_events()[0].payload
    prompt = payload["skill_creator_prompt"]
    question = payload["questions"][0]["question"]

    assert "Reusable tool-call patterns" in prompt
    assert "检测到" not in prompt
    assert "Reusable tool-call patterns" in question
    assert "创建原因" not in question
    assert payload["questions"][0]["header"] == "New Skill Creation"


@pytest.mark.asyncio
async def test_emit_new_skill_create_suggestion_skips_when_skill_creator_missing(tmp_path):
    """When skill-creator/SKILL.md is missing, no event is emitted."""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir(exist_ok=True)
    # No skill-creator directory

    rail = SkillEvolutionRail(
        skills_dir=str(skills_dir),
        llm=Mock(),
        model="dummy-model",
        auto_save=False,
    )
    rail._evolution_store = Mock()
    rail._evolution_store.base_dir = skills_dir
    rail._evolver = Mock()

    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)
    await rail._emit_new_skill_create_suggestion(ctx)

    events = rail.drain_pending_approval_events()
    assert events == []


# =============================================================================
# on_approve_new_skill / on_reject_new_skill Tests (deprecated — returns prompt)
# =============================================================================


@pytest.mark.asyncio
async def test_on_approve_new_skill_returns_prompt_without_create_skill(tmp_path):
    """on_approve_new_skill now returns a skill_creator_prompt string, not a skill name."""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir(exist_ok=True)
    (skills_dir / "skill-creator").mkdir(exist_ok=True)
    (skills_dir / "skill-creator" / "SKILL.md").write_text("# skill-creator\n")

    rail = SkillEvolutionRail(
        skills_dir=str(skills_dir),
        llm=Mock(),
        model="dummy-model",
        auto_save=False,
    )
    rail._evolution_store = Mock()
    rail._evolution_store.base_dir = skills_dir
    rail._evolver = Mock()

    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)
    await rail._emit_new_skill_create_suggestion(ctx)
    events = rail.drain_pending_approval_events()
    request_id = events[0].payload["request_id"]

    result = await rail.on_approve_new_skill(request_id)
    assert result is not None
    assert "skill-creator" in result
    # User already approved via ask_user_question; AUTO template must not
    # instruct the Agent to ask again (avoid double-confirmation).
    assert "ask_user" not in result
    assert "无需再次向用户确认" in result
    assert request_id not in rail._pending_skill_proposals


@pytest.mark.asyncio
async def test_on_approve_new_skill_unknown_request_id(tmp_path):
    rail = _make_rail(tmp_path)
    result = await rail.on_approve_new_skill("ns_nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_on_reject_new_skill_discards_proposal(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir(exist_ok=True)
    (skills_dir / "skill-creator").mkdir(exist_ok=True)
    (skills_dir / "skill-creator" / "SKILL.md").write_text("# skill-creator\n")

    rail = SkillEvolutionRail(
        skills_dir=str(skills_dir),
        llm=Mock(),
        model="dummy-model",
        auto_save=False,
    )
    rail._evolution_store = Mock()
    rail._evolution_store.base_dir = skills_dir
    rail._evolver = Mock()

    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)
    await rail._emit_new_skill_create_suggestion(ctx)
    events = rail.drain_pending_approval_events()
    request_id = events[0].payload["request_id"]

    await rail.on_reject_new_skill(request_id)
    assert request_id not in rail._pending_skill_proposals


@pytest.mark.asyncio
async def test_on_reject_new_skill_unknown_id_is_noop(tmp_path):
    rail = _make_rail(tmp_path)
    await rail.on_reject_new_skill("ns_does_not_exist")
    # No exception should be raised


@pytest.mark.asyncio
async def test_take_pending_skill_create_prompts_returns_and_clears(tmp_path):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir(exist_ok=True)
    (skills_dir / "skill-creator").mkdir(exist_ok=True)
    (skills_dir / "skill-creator" / "SKILL.md").write_text("# skill-creator\n")

    rail = SkillEvolutionRail(
        skills_dir=str(skills_dir),
        llm=Mock(),
        model="dummy-model",
        auto_save=False,
    )
    rail._evolution_store = Mock()
    rail._evolution_store.base_dir = skills_dir
    rail._evolver = Mock()

    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)
    await rail._emit_new_skill_create_suggestion(ctx)
    # Drain approval events (but proposals remain)
    rail.drain_pending_approval_events()

    prompts = rail.take_pending_skill_create_prompts()
    assert len(prompts) == 1
    assert all("skill-creator" in p for p in prompts.values())
    assert not rail._pending_skill_proposals


# =============================================================================
# Constructor Validation Tests (Issue #4)
# =============================================================================


def test_init_invalid_eval_interval_raises():
    with pytest.raises(ValueError, match="eval_interval"):
        SkillEvolutionRail(skills_dir="skills", llm=Mock(), model="m", eval_interval=0)


def test_init_invalid_tool_threshold_raises():
    with pytest.raises(ValueError, match="new_skill_tool_threshold"):
        SkillEvolutionRail(skills_dir="skills", llm=Mock(), model="m", new_skill_tool_threshold=0)


def test_init_invalid_tool_diversity_raises():
    with pytest.raises(ValueError, match="new_skill_tool_diversity"):
        SkillEvolutionRail(skills_dir="skills", llm=Mock(), model="m", new_skill_tool_diversity=-1)


def test_init_valid_params_no_error():
    rail = SkillEvolutionRail(
        skills_dir="skills",
        llm=Mock(),
        model="m",
        eval_interval=3,
        new_skill_tool_threshold=4,
        new_skill_tool_diversity=2,
    )
    assert rail._eval_interval == 3
    assert rail._new_skill_tool_threshold == 4
    assert rail._new_skill_tool_diversity == 2


# =============================================================================
# Session-level State Isolation Tests
# =============================================================================


def test_session_presented_records_isolated_per_session(tmp_path):
    from types import SimpleNamespace

    rail = _make_rail(tmp_path)
    session_a = SimpleNamespace()
    session_b = SimpleNamespace()

    # Set different values for different sessions
    rail._set_session_presented_records(session_a, [("skill-a", _make_record("skill-a"), "snippet-a")])
    rail._set_session_presented_records(session_b, [("skill-b", _make_record("skill-b"), "snippet-b")])

    records_a = rail._get_session_presented_records(session_a)
    records_b = rail._get_session_presented_records(session_b)

    assert len(records_a) == 1
    assert records_a[0][0] == "skill-a"
    assert records_a[0][2] == "snippet-a"
    assert len(records_b) == 1
    assert records_b[0][0] == "skill-b"
    assert records_b[0][2] == "snippet-b"


def test_session_eval_counter_isolated_per_session(tmp_path):
    from types import SimpleNamespace

    rail = _make_rail(tmp_path)
    session_a = SimpleNamespace()
    session_b = SimpleNamespace()

    rail._set_session_eval_counter(session_a, 3)
    rail._set_session_eval_counter(session_b, 7)

    assert rail._get_session_eval_counter(session_a) == 3
    assert rail._get_session_eval_counter(session_b) == 7


def test_session_helpers_with_none_session(tmp_path):
    rail = _make_rail(tmp_path)
    assert rail._get_session_presented_records(None) == []
    assert rail._get_session_eval_counter(None) == 0
    # Setting with None session must not raise
    rail._set_session_presented_records(None, [])
    rail._set_session_eval_counter(None, 5)


# =============================================================================
# Path B: new skill creation is not entered from run_evolution without attributed signals
# =============================================================================


@pytest.mark.asyncio
async def test_run_evolution_path_b_not_reached_when_no_attributed_signals(tmp_path, monkeypatch):
    """When no attributed signals, cancel review and do not evaluate Path B."""
    rail = _make_rail(tmp_path)
    rail._auto_scan = True
    rail._new_skill_detection = True

    parsed_messages = [
        {
            "role": "user",
            "content": "do something",
            "tool_calls": [
                {"name": "bash", "arguments": ""},
                {"name": "read_file", "arguments": ""},
                {"name": "write_file", "arguments": ""},
                {"name": "grep", "arguments": ""},
                {"name": "ls", "arguments": ""},
                {"name": "python", "arguments": ""},
                {"name": "git", "arguments": ""},
                {"name": "npm", "arguments": ""},
                {"name": "docker", "arguments": ""},
                {"name": "curl", "arguments": ""},
            ],
        },
    ]
    rail._evolution_store.list_skill_names = Mock(return_value=["existing_skill"])
    rail._evolution_store.skill_exists = Mock(return_value=True)
    _patch_detected_signals(monkeypatch, [])

    should_propose_called = []

    async def fake_should_propose(msgs):
        should_propose_called.append(True)
        return False

    rail._should_propose_new_skill = fake_should_propose
    rail._collect_parsed_messages = AsyncMock(return_value=parsed_messages)
    rail._trigger_async_evaluation = AsyncMock()

    from openjiuwen.agent_evolving.trajectory import Trajectory

    traj = Mock(spec=Trajectory)
    ctx = Mock()
    ctx.session = None

    await rail.run_evolution(traj, ctx)

    assert not should_propose_called, "Path B must not run when no skill_groups (aligned cancel)"
    rail._trigger_async_evaluation.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_evolution_path_b_not_reached_when_path_a_handled(tmp_path, monkeypatch):
    """Path B should NOT run when Path A already handled at least one skill."""
    rail = _make_rail(tmp_path)
    rail._auto_scan = True
    rail._new_skill_detection = True
    rail._auto_save = True

    parsed_messages = [
        {"role": "tool", "content": "read /skills/my_skill/SKILL.md", "tool_calls": []},
    ]
    # Arrange: detect a signal attributed to an existing skill
    from openjiuwen.agent_evolving.signal import EvolutionSignal, EvolutionCategory

    dummy_signal = EvolutionSignal(
        signal_type="execution_failure",
        evolution_type=EvolutionCategory.SKILL_EXPERIENCE,
        section="",
        excerpt="err",
        skill_name="my_skill",
    )

    rail._evolution_store.list_skill_names = Mock(return_value=["my_skill"])
    rail._evolution_store.skill_exists = Mock(return_value=True)
    rail._collect_parsed_messages = AsyncMock(return_value=parsed_messages)
    _patch_detected_signals(monkeypatch, [dummy_signal])
    rail._generate_experience_for_skill = AsyncMock(return_value=[])
    rail._trigger_async_evaluation = AsyncMock()

    path_b_called = []

    async def fake_should_propose(msgs):
        path_b_called.append(True)
        return False

    rail._should_propose_new_skill = fake_should_propose

    from openjiuwen.agent_evolving.trajectory import Trajectory

    traj = Mock(spec=Trajectory)
    ctx = Mock()
    ctx.session = None

    await rail.run_evolution(traj, ctx)

    assert not path_b_called, "Path B must not run when Path A already handled signals"


# =============================================================================
# Async evaluation snippet (rebuilt at after_invoke from full conversation)
# =============================================================================


def test_build_evaluation_snippet_anchors_after_skill_load(tmp_path):
    """Snippet starts at last SKILL.md load and includes subsequent assistant/tool turns."""
    messages = [
        {"role": "user", "content": "before load"},
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "name": "skill_tool",
                    "arguments": '{"skill_name":"my_skill","relative_file_path":"SKILL"}',
                }
            ],
        },
        {"role": "tool", "name": "skill_tool", "content": "SKILL body for my_skill"},
        {"role": "assistant", "content": "after load reply"},
    ]
    snippet = SkillEvolutionRail._build_evaluation_snippet(messages, "my_skill")
    assert "after load reply" in snippet
    assert "before load" not in snippet


def test_build_evaluation_snippet_supports_openai_nested_tool_calls():
    """Nested function.name/arguments must appear in scorer snippets and anchors."""
    messages = [
        {"role": "user", "content": "郑州的天气"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_skill",
                    "type": "function",
                    "function": {
                        "name": "skill_tool",
                        "arguments": '{"skill_name":"weather","relative_file_path":"SKILL.md"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "content": "Skill weather body experience about uv_index",
            "tool_call_id": "call_skill",
        },
        {
            "role": "assistant",
            "content": "查询紫外线",
            "tool_calls": [
                {
                    "id": "call_bash",
                    "type": "function",
                    "function": {
                        "name": "bash",
                        "arguments": (
                            '{"command":"curl -s \\"https://api.open-meteo.com/v1/forecast?'
                            'latitude=34.7&longitude=113.6&current=uv_index\\""}'
                        ),
                    },
                }
            ],
        },
    ]

    assert SkillEvolutionRail._find_skill_load_anchor(messages, "weather") == 1
    snippet = SkillEvolutionRail._build_evaluation_snippet(messages, "weather")
    assert "[assistant/tool_call] skill_tool" in snippet
    assert "[assistant/tool_call] bash" in snippet
    assert "uv_index" in snippet
    assert "郑州的天气" not in snippet


def test_find_skill_load_anchor_uses_assistant_tool_call_not_substring_tool_content():
    """tool/function responses must not set anchor via skill_name substring match."""
    messages = [
        {"role": "user", "content": "go"},
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "name": "skill_tool",
                    "arguments": '{"skill_name":"test","relative_file_path":"SKILL"}',
                }
            ],
        },
        {
            "role": "tool",
            "name": "skill_tool",
            "content": "latest testing results (no exact skill_name field here)",
        },
        {"role": "assistant", "content": "done"},
    ]
    anchor = SkillEvolutionRail._find_skill_load_anchor(messages, "test")
    assert anchor == 1


def test_find_skill_load_anchor_not_overwritten_by_later_substring_false_positive():
    """A later tool response containing skill_name as substring must not move the anchor."""
    messages = [
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "name": "skill_tool",
                    "arguments": '{"skill_name":"test","relative_file_path":"SKILL"}',
                }
            ],
        },
        {"role": "tool", "name": "skill_tool", "content": "SKILL body"},
        {"role": "assistant", "content": "correct post-load context"},
        {
            "role": "tool",
            "name": "skill_tool",
            "content": "contest leaderboard update",
        },
    ]
    anchor = SkillEvolutionRail._find_skill_load_anchor(messages, "test")
    assert anchor == 0
    snippet = SkillEvolutionRail._build_evaluation_snippet(messages, "test")
    assert "correct post-load context" in snippet
    # Wrong anchor at the contest tool message would drop the assistant context above it.
    assert snippet.index("correct post-load context") < snippet.index("contest")


def test_find_skill_load_anchor_read_file_path(tmp_path):
    messages = [
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "name": "read_file",
                    "arguments": '{"file_path":"/workspace/skills/invoice-parser/SKILL.md"}',
                }
            ],
        },
        {"role": "tool", "name": "read_file", "content": "# Invoice parser"},
    ]
    anchor = SkillEvolutionRail._find_skill_load_anchor(messages, "invoice-parser")
    assert anchor == 0


@pytest.mark.parametrize(
    "tool_name",
    ["read_all", "file_upload", "bread_crumb", "profile_reader"],
)
def test_find_skill_load_anchor_rejects_non_file_read_tools(tool_name):
    messages = [
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "name": tool_name,
                    "arguments": '{"file_path":"/skills/my-skill/SKILL.md"}',
                }
            ],
        },
    ]
    assert SkillEvolutionRail._find_skill_load_anchor(messages, "my-skill") == -1


@pytest.mark.asyncio
async def test_trigger_async_evaluation_builds_snippet_from_messages(tmp_path):
    """Scorer must use snippet rebuilt from after-invoke messages, not session placeholders."""
    rail = _make_rail(tmp_path)
    rail._eval_interval = 1

    record_a = _make_record("skill_a")
    record_b = _make_record("skill_b")

    session = SimpleNamespace()
    # Production path stores "" at presentation time; legacy non-empty values are ignored.
    rail._set_session_presented_records(
        session,
        [
            ("skill_a", record_a, ""),
            ("skill_b", record_b, "stale_snippet_should_be_ignored"),
        ],
    )
    rail._set_session_eval_counter(session, 0)

    captured_snippets: list[str] = []

    async def fake_evaluate(snippet, records):
        captured_snippets.append(snippet)
        return []

    rail._scorer = Mock()
    rail._scorer.evaluate = fake_evaluate

    ctx = Mock()
    ctx.session = session

    messages = [
        {"role": "user", "content": "unrelated preamble"},
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "name": "skill_tool",
                    "arguments": '{"skill_name":"skill_a","relative_file_path":"SKILL"}',
                }
            ],
        },
        {"role": "tool", "name": "skill_tool", "content": "SKILL body skill_a"},
        {"role": "assistant", "content": "post-present skill_a behavior"},
    ]

    await rail._trigger_async_evaluation(ctx, messages)

    assert len(captured_snippets) == 2
    assert not any("stale_snippet" in s for s in captured_snippets)
    skill_a_snippet = captured_snippets[0]
    assert "post-present skill_a behavior" in skill_a_snippet
    assert "unrelated preamble" not in skill_a_snippet


# =============================================================================
# Fix #3: Only BODY records are tracked as presented
# =============================================================================


@pytest.mark.asyncio
async def test_track_presented_records_only_body_records(tmp_path):
    """_track_presented_records must only update BODY records, not DESCRIPTION records."""
    rail = _make_rail(tmp_path)

    body_record = _make_record("sk")
    body_record.target = EvolutionTarget.BODY
    body_record.score = 0.8

    desc_record = _make_record("sk", content="desc exp")
    desc_record.target = EvolutionTarget.DESCRIPTION
    desc_record.score = 0.9  # Higher score but wrong type

    rail._evolution_store.get_records_by_score = AsyncMock(return_value=[desc_record, body_record])
    rail._evolution_store.update_record_scores = AsyncMock()

    session = SimpleNamespace()
    ctx = Mock()
    ctx.session = session

    await rail._track_presented_records(ctx, "sk", "some_snippet")

    # update_record_scores must only contain the body record id
    call_args = rail._evolution_store.update_record_scores.call_args
    updates: dict = call_args[0][1]
    assert body_record.id in updates
    assert desc_record.id not in updates

    # Session must only contain the body record
    entries = rail._get_session_presented_records(session)
    assert len(entries) == 1
    assert entries[0][1].id == body_record.id


@pytest.mark.asyncio
async def test_track_presented_records_skips_when_no_body_records(tmp_path):
    """_track_presented_records must be a no-op when there are no BODY records."""
    rail = _make_rail(tmp_path)

    desc_record = _make_record("sk")
    desc_record.target = EvolutionTarget.DESCRIPTION
    desc_record.score = 0.9

    rail._evolution_store.get_records_by_score = AsyncMock(return_value=[desc_record])
    rail._evolution_store.update_record_scores = AsyncMock()

    session = SimpleNamespace()
    ctx = Mock()
    ctx.session = session

    await rail._track_presented_records(ctx, "sk", "snip")

    rail._evolution_store.update_record_scores.assert_not_called()
    assert rail._get_session_presented_records(session) == []


@pytest.mark.asyncio
async def test_after_tool_call_no_track_when_no_body_text(tmp_path):
    """after_tool_call must not call _track_presented_records when body injection is empty."""
    rail = _make_rail(tmp_path, auto_scan=True)

    # Simulate reading a SKILL.md with no body experiences
    tool_inputs = ToolCallInputs(
        tool_name="read_file",
        tool_args={"file_path": "/workspace/skills/my_skill/SKILL.md"},
    )
    ctx = Mock()
    ctx.inputs = tool_inputs
    ctx.session = None

    rail._evolution_store.format_body_experience_text = AsyncMock(return_value="")
    track_called = []

    async def fake_track(ctx_, skill, snippet):
        track_called.append(skill)

    rail._track_presented_records = fake_track

    await rail.after_tool_call(ctx)

    assert not track_called, "_track_presented_records must not be called when no body text was injected"


# =============================================================================
# Fix #4: create_skill refuses to overwrite existing skill
# =============================================================================


@pytest.mark.asyncio
async def test_create_skill_refuses_to_overwrite_existing(tmp_path):
    """create_skill must return None and not write files when skill dir already exists."""
    from openjiuwen.agent_evolving.checkpointing.evolution_store import EvolutionStore

    store = EvolutionStore(str(tmp_path))

    # Pre-create the skill directory to simulate an existing skill
    existing_dir = tmp_path / "my_skill"
    existing_dir.mkdir()
    existing_skill_md = existing_dir / "SKILL.md"
    existing_skill_md.write_text("original content")

    result = await store.create_skill(
        name="my_skill",
        description="duplicate",
        body="should not overwrite",
    )

    assert result is None, "create_skill must return None for existing skill"
    # Original file must be untouched
    assert existing_skill_md.read_text() == "original content"


@pytest.mark.asyncio
async def test_create_skill_succeeds_for_new_skill(tmp_path):
    """create_skill must succeed and create files when skill does not exist."""
    from openjiuwen.agent_evolving.checkpointing.evolution_store import EvolutionStore

    store = EvolutionStore(str(tmp_path))

    result = await store.create_skill(
        name="brand_new_skill",
        description="A fresh skill",
        body="## Instructions\nDo things.",
    )

    assert result is not None
    assert (result / "SKILL.md").exists()
    content = (result / "SKILL.md").read_text()
    assert "brand_new_skill" in content
    assert "A fresh skill" in content


# =============================================================================
# generate_and_emit_experience (Public API) Tests
# =============================================================================


@pytest.mark.asyncio
async def test_generate_and_emit_experience_returns_true_when_records_staged(tmp_path):
    """Public API should return True and emit approval events when records are staged."""
    rail = _make_rail(tmp_path, auto_save=False)
    signals = [_make_signal("skill-a")]
    messages = [{"role": "user", "content": "test"}]

    # Mock optimizer to stage records
    rail._generate_experience_via_optimizer = AsyncMock(return_value=True)
    rail._emit_generated_records = AsyncMock()

    result = await rail.generate_and_emit_experience("skill-a", signals, messages)

    assert result is True
    rail._generate_experience_via_optimizer.assert_awaited_once_with("skill-a", signals, messages)
    rail._emit_generated_records.assert_awaited_once_with(None, "skill-a")


@pytest.mark.asyncio
async def test_generate_and_emit_experience_returns_false_when_no_records(tmp_path):
    """Public API should return False when optimizer stages no records."""
    rail = _make_rail(tmp_path, auto_save=False)
    signals = [_make_signal("skill-a")]
    messages = [{"role": "user", "content": "test"}]

    # Mock optimizer to return no records
    rail._generate_experience_via_optimizer = AsyncMock(return_value=False)
    rail._emit_generated_records = AsyncMock()

    result = await rail.generate_and_emit_experience("skill-a", signals, messages)

    assert result is False
    rail._generate_experience_via_optimizer.assert_awaited_once_with("skill-a", signals, messages)
    rail._emit_generated_records.assert_not_awaited()


@pytest.mark.asyncio
async def test_generate_and_emit_experience_with_empty_inputs(tmp_path):
    """Public API should handle empty signals and messages."""
    rail = _make_rail(tmp_path, auto_save=False)

    rail._generate_experience_via_optimizer = AsyncMock(return_value=False)

    result = await rail.generate_and_emit_experience("skill-a", [], [])

    assert result is False
    rail._generate_experience_via_optimizer.assert_awaited_once_with("skill-a", [], [])


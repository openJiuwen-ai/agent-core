# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
# pylint: disable=protected-access

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from openjiuwen.agent_evolving.checkpointing import EvolutionStore
from openjiuwen.agent_evolving.checkpointing.types import (
    EvolutionPatch,
    EvolutionRecord,
    EvolutionTarget,
)
from openjiuwen.agent_evolving.signal import (
    SignalDetector,
    EvolutionSignal,
    EvolutionCategory,
)
from openjiuwen.core.context_engine.qa_block.registry import save_registry
from openjiuwen.core.context_engine.qa_block.schema import (
    QABlockEntry,
    QABlockRegistry,
)
from openjiuwen.core.context_engine.qa_block.store import QABlockStore
from openjiuwen.core.foundation.llm import AssistantMessage, UserMessage
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ToolCallInputs
from openjiuwen.harness.rails.skill_evolution_rail import (
    SkillEvolutionRail,
    _MAX_PROCESSED_SIGNAL_KEYS,
)
from openjiuwen.harness.workspace.workspace import Workspace


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
        source=f"signal:{skill_name}",
        context="ctx",
        change=EvolutionPatch(
            section="Troubleshooting",
            action="append",
            content=content,
            target=EvolutionTarget.BODY,
        ),
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


def _async_returning(value):
    """Build an async replacement for ``SignalDetector.detect``.

    The real ``detect`` may route through the LLM when an optimizer llm
    is configured; tests that only exercise dedup/clearing logic patch it out
    with a coroutine that returns a fixed value.
    """

    async def _detect_async(self, _):
        return value

    return _detect_async


class _MsgContext:
    def __init__(self, messages=None, *, raise_error: bool = False):
        self._messages = list(messages) if messages else []
        self._raise_error = raise_error

    def get_messages(self):
        if self._raise_error:
            raise RuntimeError("get_messages failed")
        return self._messages


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


def test_parse_messages():
    rail = SkillEvolutionRail(skills_dir="skills", llm=Mock(), model="dummy")
    tool_call = SimpleNamespace(id="tc1", name="read_file", arguments='{"file_path":"x"}')
    msg_obj = SimpleNamespace(
        role="assistant",
        content="done",
        tool_calls=[tool_call],
        name="assistant_name",
    )
    raw = [{"role": "user", "content": "hi"}, msg_obj]
    parsed = rail._parse_messages(raw)

    assert parsed[0] == {"role": "user", "content": "hi"}
    assert parsed[1]["role"] == "assistant"
    assert parsed[1]["content"] == "done"
    assert parsed[1]["name"] == "assistant_name"
    assert parsed[1]["tool_calls"][0]["id"] == "tc1"
    assert parsed[1]["tool_calls"][0]["name"] == "read_file"


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
    (skill_dir / "SKILL.md").write_text("# Current\n", encoding="utf-8")
    (skill_dir / "evolutions.json").write_text('{"entries": ["current"]}', encoding="utf-8")
    (archive / "SKILL.v20260622T123456.md").write_text("# Archived\n", encoding="utf-8")
    (archive / "evolutions.v20260622T123456.json").write_text('{"entries": []}', encoding="utf-8")

    rail = _make_rail(tmp_path)
    rail._evolution_store = EvolutionStore(str(root))

    assert await rail.rollback_skill("skill-a", "SKILL.v20260622T123456.md") is True
    assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == "# Archived\n"
    assert (skill_dir / "evolutions.json").read_text(encoding="utf-8") == "{\"entries\": []}"
    assert not (archive / "SKILL.v20260622T123456.md").exists()
    assert not (archive / "evolutions.v20260622T123456.json").exists()
    current_archives = [
        archive / name
        for name in rail._evolution_store.list_archives("skill-a")
        if name.startswith("SKILL.v")
    ]
    assert len(current_archives) == 1
    assert current_archives[0].read_text(encoding="utf-8") == "# Current\n"


@pytest.mark.asyncio
async def test_rollback_skill_recovers_mismatched_evo_suffix(tmp_path):
    root = tmp_path / "skills"
    skill_dir = root / "skill-a"
    archive = skill_dir / "archive"
    archive.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# Current\n", encoding="utf-8")
    (skill_dir / "evolutions.json").write_text("{\"entries\": [\"current\"]}", encoding="utf-8")
    (archive / "SKILL.v20260622T120000.md").write_text("# Archived\n", encoding="utf-8")
    (archive / "evolutions.v20260622T120001.json").write_text("{\"entries\": [\"archived\"]}", encoding="utf-8")

    rail = _make_rail(tmp_path)
    rail._evolution_store = EvolutionStore(str(root))

    assert await rail.rollback_skill("skill-a") is True
    assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == "# Archived\n"
    assert (skill_dir / "evolutions.json").read_text(encoding="utf-8") == "{\"entries\": [\"archived\"]}"
    assert not (archive / "SKILL.v20260622T120000.md").exists()
    assert not (archive / "evolutions.v20260622T120001.json").exists()


@pytest.mark.asyncio
async def test_rollback_skill_without_version_uses_latest_archive(tmp_path):
    root = tmp_path / "skills"
    skill_dir = root / "skill-a"
    archive = skill_dir / "archive"
    archive.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# Current\n", encoding="utf-8")
    (skill_dir / "evolutions.json").write_text('{"entries": ["current"]}', encoding="utf-8")
    (archive / "SKILL.v20260622T123456.md").write_text("# Old\n", encoding="utf-8")
    (archive / "evolutions.v20260622T123456.json").write_text('{"entries": ["old"]}', encoding="utf-8")
    (archive / "SKILL.v20260622T223456.md").write_text("# Latest\n", encoding="utf-8")
    (archive / "evolutions.v20260622T223456.json").write_text('{"entries": []}', encoding="utf-8")

    rail = _make_rail(tmp_path)
    rail._evolution_store = EvolutionStore(str(root))

    assert await rail.rollback_skill("skill-a") is True
    assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == "# Latest\n"
    assert (skill_dir / "evolutions.json").read_text(encoding="utf-8") == "{\"entries\": []}"
    assert not (archive / "SKILL.v20260622T223456.md").exists()
    assert not (archive / "evolutions.v20260622T223456.json").exists()
    assert (archive / "SKILL.v20260622T123456.md").exists()
    assert (archive / "evolutions.v20260622T123456.json").exists()


@pytest.mark.asyncio
async def test_rollback_skill_rejects_invalid_or_missing_version(tmp_path):
    root = tmp_path / "skills"
    skill_dir = root / "skill-a"
    archive = skill_dir / "archive"
    archive.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# Current\n", encoding="utf-8")

    rail = _make_rail(tmp_path)
    rail._evolution_store = EvolutionStore(str(root))

    assert await rail.rollback_skill("skill-a", "../SKILL.v20260622T123456.md") is False
    assert await rail.rollback_skill("skill-a", "README.md") is False
    assert await rail.rollback_skill("skill-a", "SKILL.v20260622T123456.md") is False
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
    (archive / "SKILL.v20260622T123456.md").write_text("", encoding="utf-8")

    rail = _make_rail(tmp_path)
    rail._evolution_store = EvolutionStore(str(root))

    assert await rail.rollback_skill("skill-a", "SKILL.v20260622T123456.md") is False
    assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == "# Current\n"


@pytest.mark.asyncio
async def test_rollback_skill_clears_evolutions_when_pair_missing(tmp_path):
    root = tmp_path / "skills"
    skill_dir = root / "skill-a"
    archive = skill_dir / "archive"
    archive.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# Current\n", encoding="utf-8")
    (skill_dir / "evolutions.json").write_text('{"entries": ["current"]}', encoding="utf-8")
    (archive / "SKILL.v20260622T123456.md").write_text("# Archived\n", encoding="utf-8")

    rail = _make_rail(tmp_path)
    rail._evolution_store = EvolutionStore(str(root))

    assert await rail.rollback_skill("skill-a", "SKILL.v20260622T123456.md") is True
    assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == "# Archived\n"
    assert (await rail._evolution_store.load_evolution_log("skill-a")).entries == []
    assert not (archive / "SKILL.v20260622T123456.md").exists()


@pytest.mark.asyncio
async def test_rollback_skill_keeps_target_archive_when_restore_fails(tmp_path):
    root = tmp_path / "skills"
    skill_dir = root / "skill-a"
    archive = skill_dir / "archive"
    archive.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# Current\n", encoding="utf-8")
    (skill_dir / "evolutions.json").write_text("{\"entries\": [\"current\"]}", encoding="utf-8")
    (archive / "SKILL.v20260622T123456.md").write_text("# Archived\n", encoding="utf-8")
    (archive / "evolutions.v20260622T123456.json").write_text("{\"entries\": []}", encoding="utf-8")

    rail = _make_rail(tmp_path)
    store = EvolutionStore(str(root))
    store.restore_evolution_log_from_archive = AsyncMock(return_value=False)
    rail._evolution_store = store

    assert await rail.rollback_skill("skill-a", "SKILL.v20260622T123456.md") is False
    assert (archive / "SKILL.v20260622T123456.md").exists()
    assert (archive / "evolutions.v20260622T123456.json").exists()


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
async def test_run_evolution_auto_save_appends_records(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=True)
    signals = [_make_signal("skill-a")]
    records = [_make_record("skill-a"), _make_record("skill-a")]

    rail._collect_parsed_messages = AsyncMock(return_value=[{"role": "user", "content": "hello"}])
    rail._evolution_store.list_skill_names = Mock(return_value=["skill-a"])
    rail._detect_signals = AsyncMock(return_value=signals)
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
async def test_run_evolution_auto_save_false_emits_events(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=False)
    signals = [_make_signal("skill-a")]
    parsed_messages = [{"role": "user", "content": "hello"}]

    rail._collect_parsed_messages = AsyncMock(return_value=parsed_messages)
    rail._evolution_store.list_skill_names = Mock(return_value=["skill-a"])
    rail._detect_signals = AsyncMock(return_value=signals)
    rail._generate_experience_via_optimizer = AsyncMock(return_value=True)
    rail._emit_generated_records = AsyncMock()
    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)

    await rail.run_evolution(None, ctx)

    rail._generate_experience_via_optimizer.assert_awaited_once_with("skill-a", signals, parsed_messages)
    rail._emit_generated_records.assert_awaited_once_with(ctx, "skill-a")


@pytest.mark.asyncio
async def test_run_evolution_filters_empty_skill_name_and_swallow_exceptions(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=True)
    rail._collect_parsed_messages = AsyncMock(return_value=[{"role": "user", "content": "hello"}])
    rail._evolution_store.list_skill_names = Mock(return_value=["skill-a"])
    rail._detect_signals = AsyncMock(return_value=[_make_signal(None), _make_signal("skill-a")])
    rail._generate_experience_for_skill = AsyncMock(return_value=[])
    rail._evolution_store.append_record = AsyncMock()

    await rail.run_evolution(None, AgentCallbackContext(agent=None, inputs=None, session=None))
    rail._generate_experience_for_skill.assert_awaited_once()
    assert rail._generate_experience_for_skill.await_args.args[0] == "skill-a"

    rail2 = _make_rail(tmp_path, auto_scan=True)
    rail2._collect_parsed_messages = AsyncMock(side_effect=RuntimeError("boom"))
    await rail2.run_evolution(None, AgentCallbackContext(agent=None, inputs=None, session=None))


# =============================================================================
# _detect_signals Tests
# =============================================================================


@pytest.mark.asyncio
async def test_detect_signals_deduplicates_with_processed_keys(tmp_path, monkeypatch):
    rail = _make_rail(tmp_path)
    rail._evolution_store.skill_exists = Mock(return_value=True)
    signal = _make_signal("skill-a", excerpt="same-excerpt")
    monkeypatch.setattr(SignalDetector, "detect", _async_returning([signal]))

    first = await rail._detect_signals([{"role": "user", "content": "x"}], ["skill-a"])
    second = await rail._detect_signals([{"role": "user", "content": "x"}], ["skill-a"])

    assert len(first) == 1
    assert len(second) == 0
    assert ("tool_failure", "bash", "skill-a", "same-excerpt") in rail.processed_signal_keys


@pytest.mark.asyncio
async def test_detect_signals_clears_processed_keys_when_exceed_limit(tmp_path, monkeypatch):
    rail = _make_rail(tmp_path)
    rail._evolution_store.skill_exists = Mock(return_value=True)
    rail._processed_signal_keys = {(f"type-{i}", f"excerpt-{i}") for i in range(_MAX_PROCESSED_SIGNAL_KEYS)}
    monkeypatch.setattr(SignalDetector, "detect", _async_returning([_make_signal("skill-a", excerpt="new-one")]))

    detected = await rail._detect_signals([{"role": "user", "content": "x"}], ["skill-a"])

    assert len(detected) == 1
    assert rail.processed_signal_keys == set()


@pytest.mark.asyncio
async def test_detect_signals_propagates_optimizer_llm_to_detector(tmp_path, monkeypatch):
    """_detect_signals must hand the optimizer llm/model/language to SignalDetector
    so that LLM-based correction judgment (15c68a27) is engaged."""
    rail = _make_rail(tmp_path)
    rail._evolution_store.skill_exists = Mock(return_value=True)
    captured: dict = {}

    def spy_init(self: Any, **kwargs: Any) -> None:
        captured.update(kwargs)
        # bypass real init to avoid needing a real detector; mark llm so detect works
        self._existing_skills = kwargs.get("existing_skills") or set()
        self._llm = kwargs.get("llm")
        self._model = kwargs.get("model")
        self._language = kwargs.get("language")

    monkeypatch.setattr(SignalDetector, "__init__", spy_init)
    monkeypatch.setattr(SignalDetector, "detect", _async_returning([_make_signal("skill-a", excerpt="x")]))

    await rail._detect_signals([{"role": "user", "content": "x"}], ["skill-a"])

    assert captured["llm"] is rail._optimizer_llm
    assert captured["model"] == rail._optimizer_model
    assert captured["language"] == rail._optimizer_language
    assert captured["existing_skills"] == {"skill-a"}


# =============================================================================
# _collect_parsed_messages Tests
# =============================================================================


@pytest.mark.asyncio
async def test_collect_parsed_messages_prefers_ctx_context(tmp_path):
    rail = _make_rail(tmp_path)
    ctx = AgentCallbackContext(
        agent=None,
        inputs=None,
        session=None,
        context=_MsgContext(messages=[{"role": "user", "content": "q"}]),
    )
    parsed = await rail._collect_parsed_messages(ctx)
    assert parsed == [{"role": "user", "content": "q"}]


@pytest.mark.asyncio
async def test_collect_parsed_messages_fallback_to_inner_context_engine(tmp_path):
    rail = _make_rail(tmp_path)
    inner_context = _MsgContext(messages=[{"role": "assistant", "content": "ok"}])
    context_engine = SimpleNamespace(create_context=AsyncMock(return_value=inner_context))
    inner_agent = SimpleNamespace(context_engine=context_engine)
    agent = SimpleNamespace(_react_agent=inner_agent)

    ctx = AgentCallbackContext(
        agent=agent,
        inputs=None,
        session=object(),
        context=_MsgContext(messages=[]),
    )
    parsed = await rail._collect_parsed_messages(ctx)
    assert parsed == [{"role": "assistant", "content": "ok"}]
    context_engine.create_context.assert_awaited_once()


@pytest.mark.asyncio
async def test_collect_parsed_messages_returns_empty_on_both_paths_failure(tmp_path):
    rail = _make_rail(tmp_path)
    bad_context = _MsgContext(raise_error=True)
    bad_engine = SimpleNamespace(create_context=AsyncMock(side_effect=RuntimeError("context fail")))
    agent = SimpleNamespace(_react_agent=SimpleNamespace(context_engine=bad_engine))
    ctx = AgentCallbackContext(agent=agent, inputs=None, session=object(), context=bad_context)

    parsed = await rail._collect_parsed_messages(ctx)
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
async def test_run_evolution_zero_signals_creates_conversation_review(tmp_path):
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
    rail._detect_signals = AsyncMock(return_value=[])
    rail._infer_primary_skill = Mock(return_value="skill-a")
    rail._generate_experience_for_skill = AsyncMock(return_value=[])
    rail._evolution_store.append_record = AsyncMock()

    await rail.run_evolution(None, AgentCallbackContext(agent=None, inputs=None, session=None))

    rail._generate_experience_for_skill.assert_awaited_once()
    call_args = rail._generate_experience_for_skill.await_args
    assert call_args.args[0] == "skill-a"
    signals_passed = call_args.args[1]
    assert len(signals_passed) == 1
    assert signals_passed[0].signal_type == "conversation_review"


@pytest.mark.asyncio
async def test_run_evolution_zero_signals_no_primary_skill_returns(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=True)

    rail._collect_parsed_messages = AsyncMock(return_value=[{"role": "user", "content": "hi"}])
    rail._evolution_store.list_skill_names = Mock(return_value=["skill-a"])
    rail._detect_signals = AsyncMock(return_value=[])
    rail._infer_primary_skill = Mock(return_value=None)
    rail._generate_experience_for_skill = AsyncMock()

    await rail.run_evolution(None, AgentCallbackContext(agent=None, inputs=None, session=None))

    rail._generate_experience_for_skill.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_evolution_unattributed_signals_get_fallback_skill(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=True)

    attributed = _make_signal("skill-a", excerpt="attributed")
    unattributed = _make_signal(None, excerpt="unattributed")

    rail._collect_parsed_messages = AsyncMock(return_value=[{"role": "user", "content": "hi"}])
    rail._evolution_store.list_skill_names = Mock(return_value=["skill-a"])
    rail._detect_signals = AsyncMock(return_value=[attributed, unattributed])
    rail._generate_experience_for_skill = AsyncMock(return_value=[])
    rail._evolution_store.append_record = AsyncMock()

    await rail.run_evolution(None, AgentCallbackContext(agent=None, inputs=None, session=None))

    assert unattributed.skill_name == "skill-a"
    rail._generate_experience_for_skill.assert_awaited_once()
    call_args = rail._generate_experience_for_skill.await_args
    assert len(call_args.args[1]) == 2


@pytest.mark.asyncio
async def test_run_evolution_multiple_attributed_skills_no_fallback(tmp_path):
    rail = _make_rail(tmp_path, auto_scan=True, auto_save=True)

    sig_a = _make_signal("skill-a", excerpt="a")
    sig_b = _make_signal("skill-b", excerpt="b")
    sig_none = _make_signal(None, excerpt="none")

    rail._collect_parsed_messages = AsyncMock(return_value=[{"role": "user", "content": "hi"}])
    rail._evolution_store.list_skill_names = Mock(return_value=["skill-a", "skill-b"])
    rail._detect_signals = AsyncMock(return_value=[sig_a, sig_b, sig_none])
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
    rail = _make_rail(tmp_path)
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"name": "read_file", "arguments": ""},
                {"name": "write_file", "arguments": ""},
                {"name": "run_bash", "arguments": ""},
                {"name": "read_file", "arguments": ""},
                {"name": "write_file", "arguments": ""},
            ],
        },
    ]
    result = await rail._should_propose_new_skill(messages)
    assert result is True


@pytest.mark.asyncio
async def test_should_propose_new_skill_too_few_tool_calls(tmp_path):
    rail = _make_rail(tmp_path)
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"name": "read_file", "arguments": ""},
                {"name": "write_file", "arguments": ""},
            ],
        },
    ]
    result = await rail._should_propose_new_skill(messages)
    assert result is False


@pytest.mark.asyncio
async def test_should_propose_new_skill_insufficient_diversity(tmp_path):
    # 5 tool calls but all the same tool
    rail = _make_rail(tmp_path)
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
            ],
        },
    ]
    # default diversity threshold is 2, only 1 unique tool → False
    result = await rail._should_propose_new_skill(messages)
    assert result is False


@pytest.mark.asyncio
async def test_should_propose_new_skill_no_tool_calls(tmp_path):
    rail = _make_rail(tmp_path)
    messages = [{"role": "user", "content": "no tools used"}]
    result = await rail._should_propose_new_skill(messages)
    assert result is False


# =============================================================================
# _check_skill_overlap Tests
# =============================================================================


@pytest.mark.asyncio
async def test_check_skill_overlap_detects_containment(tmp_path):
    rail = _make_rail(tmp_path)
    rail._evolution_store.list_skill_names = Mock(return_value=["data-analysis", "report-gen"])
    # "data" is contained in "data-analysis"
    result = await rail._check_skill_overlap("data", "analyze data")
    assert result is True


@pytest.mark.asyncio
async def test_check_skill_overlap_no_overlap(tmp_path):
    rail = _make_rail(tmp_path)
    rail._evolution_store.list_skill_names = Mock(return_value=["data-analysis", "report-gen"])
    result = await rail._check_skill_overlap("code-review", "review source code")
    assert result is False


@pytest.mark.asyncio
async def test_check_skill_overlap_empty_name(tmp_path):
    rail = _make_rail(tmp_path)
    rail._evolution_store.list_skill_names = Mock(return_value=["data-analysis"])
    # Empty proposal name is contained in "data-analysis" as empty string
    result = await rail._check_skill_overlap("", "some description")
    assert result is True


@pytest.mark.asyncio
async def test_check_skill_overlap_no_existing_skills(tmp_path):
    rail = _make_rail(tmp_path)
    rail._evolution_store.list_skill_names = Mock(return_value=[])
    result = await rail._check_skill_overlap("brand-new-skill", "something new")
    assert result is False


# =============================================================================
# _emit_new_skill_approval Tests
# =============================================================================


@pytest.mark.asyncio
async def test_emit_new_skill_approval_buffers_event(tmp_path):
    rail = _make_rail(tmp_path, auto_save=False)
    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)
    proposal = {
        "name": "my-skill",
        "description": "Does something",
        "body": "## Instructions\nDo stuff",
        "reason": "Useful pattern",
    }
    await rail._emit_new_skill_approval(ctx, proposal)

    events = rail.drain_pending_approval_events()
    assert len(events) == 1
    event = events[0]
    assert event.type == "chat.ask_user_question"
    payload = event.payload
    assert payload["_new_skill_data"]["name"] == "my-skill"
    request_id = payload["request_id"]
    # UUID-based prefix for skill creation
    assert request_id.startswith("skill_create_")
    assert request_id in rail._pending_skill_proposals


@pytest.mark.asyncio
async def test_emit_new_skill_approval_unique_ids(tmp_path):
    """Two concurrent proposals must not share the same request_id."""
    rail = _make_rail(tmp_path, auto_save=False)
    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)
    proposal_a = {"name": "skill-a", "description": "A", "body": "", "reason": ""}
    proposal_b = {"name": "skill-b", "description": "B", "body": "", "reason": ""}

    await rail._emit_new_skill_approval(ctx, proposal_a)
    await rail._emit_new_skill_approval(ctx, proposal_b)

    events = rail.drain_pending_approval_events()
    ids = {e.payload["request_id"] for e in events}
    assert len(ids) == 2  # must be distinct


# =============================================================================
# on_approve_new_skill / on_reject_new_skill Tests
# =============================================================================


@pytest.mark.asyncio
async def test_on_approve_new_skill_creates_skill(tmp_path):
    rail = _make_rail(tmp_path, auto_save=False)
    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)
    proposal = {"name": "new-skill", "description": "desc", "body": "body", "reason": "reason"}

    await rail._emit_new_skill_approval(ctx, proposal)
    events = rail.drain_pending_approval_events()
    request_id = events[0].payload["request_id"]

    rail._evolution_store.create_skill = AsyncMock(return_value=tmp_path / "new-skill")

    result = await rail.on_approve_new_skill(request_id)

    rail._evolution_store.create_skill.assert_awaited_once_with(
        name="new-skill",
        description="desc",
        body="body",
    )
    assert result == "new-skill"
    # Proposal should be removed
    assert request_id not in rail._pending_skill_proposals


@pytest.mark.asyncio
async def test_on_approve_new_skill_unknown_request_id(tmp_path):
    rail = _make_rail(tmp_path)
    result = await rail.on_approve_new_skill("ns_nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_on_approve_new_skill_store_failure_returns_none(tmp_path):
    rail = _make_rail(tmp_path, auto_save=False)
    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)
    proposal = {"name": "fail-skill", "description": "d", "body": "b", "reason": "r"}

    await rail._emit_new_skill_approval(ctx, proposal)
    events = rail.drain_pending_approval_events()
    request_id = events[0].payload["request_id"]

    rail._evolution_store.create_skill = AsyncMock(return_value=None)
    result = await rail.on_approve_new_skill(request_id)
    assert result is None


@pytest.mark.asyncio
async def test_on_approve_new_skill_exception_returns_none(tmp_path):
    rail = _make_rail(tmp_path, auto_save=False)
    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)
    proposal = {"name": "exc-skill", "description": "d", "body": "b", "reason": "r"}

    await rail._emit_new_skill_approval(ctx, proposal)
    events = rail.drain_pending_approval_events()
    request_id = events[0].payload["request_id"]

    rail._evolution_store.create_skill = AsyncMock(side_effect=OSError("disk full"))
    result = await rail.on_approve_new_skill(request_id)
    assert result is None


@pytest.mark.asyncio
async def test_on_reject_new_skill_discards_proposal(tmp_path):
    rail = _make_rail(tmp_path, auto_save=False)
    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)
    proposal = {"name": "rej-skill", "description": "d", "body": "b", "reason": "r"}

    await rail._emit_new_skill_approval(ctx, proposal)
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
async def test_emit_new_skill_approval_auto_save_creates_and_clears_pending(tmp_path):
    rail = _make_rail(tmp_path, auto_save=True)
    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)
    proposal = {"name": "auto-skill", "description": "d", "body": "b", "reason": "r"}

    rail._evolution_store.create_skill = AsyncMock(return_value=tmp_path / "auto-skill")

    await rail._emit_new_skill_approval(ctx, proposal)

    rail._evolution_store.create_skill.assert_awaited_once_with(
        name="auto-skill",
        description="d",
        body="b",
    )
    assert rail.drain_pending_approval_events() == []
    assert not rail._pending_skill_proposals


@pytest.mark.asyncio
async def test_emit_new_skill_approval_auto_save_failure_retains_pending_for_retry(tmp_path):
    rail = _make_rail(tmp_path, auto_save=True)
    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)
    proposal = {"name": "retry-skill", "description": "d", "body": "b", "reason": "r"}

    rail._evolution_store.create_skill = AsyncMock(return_value=None)

    await rail._emit_new_skill_approval(ctx, proposal)

    assert len(rail._pending_skill_proposals) == 1
    request_id = next(iter(rail._pending_skill_proposals))

    rail._evolution_store.create_skill = AsyncMock(return_value=tmp_path / "retry-skill")
    result = await rail.on_approve_new_skill(request_id)

    assert result == "retry-skill"
    assert request_id not in rail._pending_skill_proposals


@pytest.mark.asyncio
async def test_on_approve_new_skill_auto_save_already_created_returns_none(tmp_path):
    rail = _make_rail(tmp_path, auto_save=True)
    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)
    proposal = {"name": "done-skill", "description": "d", "body": "b", "reason": "r"}

    rail._evolution_store.create_skill = AsyncMock(return_value=tmp_path / "done-skill")
    await rail._emit_new_skill_approval(ctx, proposal)

    result = await rail.on_approve_new_skill("skill_create_nonexistent")
    assert result is None


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
# Fix #1: Path B reachable when no skill is inferred
# =============================================================================


@pytest.mark.asyncio
async def test_run_evolution_path_b_reached_when_no_primary_skill(tmp_path):
    """When no signals and no primary skill, Path B should still be evaluated."""
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
            ],
        },
    ]
    rail._evolution_store.list_skill_names = Mock(return_value=["existing_skill"])
    rail._evolution_store.skill_exists = Mock(return_value=True)
    # No SKILL.md reads → _infer_primary_skill returns None
    # Enough tool calls to pass _should_propose_new_skill threshold

    should_propose_called = []

    async def fake_should_propose(msgs):
        should_propose_called.append(True)
        return False  # Don't actually emit; just confirm Path B was entered

    rail._should_propose_new_skill = fake_should_propose
    rail._collect_parsed_messages = AsyncMock(return_value=parsed_messages)
    rail._trigger_async_evaluation = AsyncMock()

    from openjiuwen.agent_evolving.trajectory import Trajectory

    traj = Mock(spec=Trajectory)
    ctx = Mock()
    ctx.session = None

    await rail.run_evolution(traj, ctx)

    assert should_propose_called, "Path B (_should_propose_new_skill) must be reached when no primary skill"


@pytest.mark.asyncio
async def test_run_evolution_path_b_not_reached_when_path_a_handled(tmp_path):
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
    rail._detect_signals = AsyncMock(return_value=[dummy_signal])
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
    record = _make_record("skill-a")
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


# =============================================================================
# QA block history loading & prepend (PR #1777) Tests
# =============================================================================


class _QAHistorySession:
    """Minimal session backing ``load_registry``/``QABlockStore`` semantics."""

    def __init__(self, session_id: str = "session-qa"):
        self._session_id = session_id
        self.state: dict = {}

    def get_session_id(self) -> str:
        return self._session_id

    def get_state(self, key: str | None = None) -> Any:
        if key is None:
            return self.state
        return self.state.get(key)

    def update_state(self, updates: dict) -> None:
        self.state.update(updates)


def _make_qa_history_rail(tmp_path: Path) -> SkillEvolutionRail:
    rail = _make_rail(tmp_path)
    # workspace defaults to None on the rail; wire a real one so QABlockStore
    # resolves L0 files under tmp_path.
    rail.workspace = Workspace(root_path=str(tmp_path))
    rail.sys_operation = None
    return rail


async def _seed_qa_l0(tmp_path: Path, session_id: str, qa_id: str, messages: list[Any]) -> None:
    store = QABlockStore(str(tmp_path), session_id)
    await store.write_l0(qa_id, list(messages))


def _history_entry(qa_id: str, qa_index: int, *, is_history: bool = True) -> QABlockEntry:
    return QABlockEntry(
        qa_id=qa_id,
        qa_index=qa_index,
        status="completed",
        is_history=is_history,
    )


def test_dedup_messages_keeps_first_occurrence_and_preserves_order():
    """Pure function: identical fingerprints collapse to the first occurrence."""
    rail = SkillEvolutionRail(skills_dir="skills", llm=Mock(), model="dummy")
    a = {"role": "user", "content": "hi"}
    b = {"role": "assistant", "content": "ok"}
    dup_a = {"role": "user", "content": "hi"}  # same fingerprint as `a`
    messages = [a, b, dup_a, b]

    result = SkillEvolutionRail._dedup_messages(messages)

    assert result == [a, b]
    # Originals must be kept by identity (first occurrence retained)
    assert result[0] is a
    assert result[1] is b


def test_dedup_messages_empty_or_all_unique_returns_unchanged():
    rail = SkillEvolutionRail(skills_dir="skills", llm=Mock(), model="dummy")
    assert SkillEvolutionRail._dedup_messages([]) == []

    unique = [
        {"role": "user", "content": "q1"},
        {"role": "user", "content": "q2"},
        {"role": "assistant", "content": "a1"},
    ]
    assert SkillEvolutionRail._dedup_messages(unique) == unique


def test_dedup_messages_fingerprint_includes_role_content_name():
    """Same content but different role/name must NOT be treated as duplicate."""
    rail = SkillEvolutionRail(skills_dir="skills", llm=Mock(), model="dummy")
    messages = [
        {"role": "user", "content": "same"},
        {"role": "assistant", "content": "same"},
        {"role": "user", "content": "same", "name": "caller"},
        {"role": "user", "content": "same", "name": "caller"},  # dup of prev
    ]
    result = SkillEvolutionRail._dedup_messages(messages)
    assert len(result) == 3
    assert result[0] == {"role": "user", "content": "same"}
    assert result[1] == {"role": "assistant", "content": "same"}
    assert result[2] == {"role": "user", "content": "same", "name": "caller"}


def test_dedup_messages_tool_calls_order_invariant_and_missing_ok():
    """tool_calls fingerprint is sort_keys-stable; absence is its own fingerprint."""
    rail = SkillEvolutionRail(skills_dir="skills", llm=Mock(), model="dummy")
    tc_a = {"id": "1", "name": "read", "arguments": "{}"}
    tc_b = {"id": "2", "name": "write", "arguments": "{}"}
    messages = [
        {"role": "assistant", "content": "", "tool_calls": [tc_a, tc_b]},
        {"role": "assistant", "content": "", "tool_calls": [tc_b, tc_a]},  # same (sorted)
        {"role": "assistant", "content": "", "tool_calls": [tc_a]},  # different
        {"role": "assistant", "content": ""},  # no tool_calls, distinct fingerprint
        {"role": "assistant", "content": ""},  # dup of prev
    ]
    result = SkillEvolutionRail._dedup_messages(messages)
    assert len(result) == 4
    assert result[0]["tool_calls"] == [tc_a, tc_b]
    assert "tool_calls" not in result[3]


def test_dedup_messages_multimodal_content_key_order_invariant():
    """content as a list (OpenAI multimodal) fingerprints stably regardless of
    dict key ordering, matching the tool_calls handling."""
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "hi"}]},
        {"role": "user", "content": [{"text": "hi", "type": "text"}]},  # same, reordered keys
        {"role": "user", "content": [{"type": "text", "text": "bye"}]},  # different text
    ]
    result = SkillEvolutionRail._dedup_messages(messages)
    assert len(result) == 2
    assert result[0] is messages[0]
    assert result[1] is messages[2]


@pytest.mark.asyncio
async def test_load_qa_block_history_returns_empty_when_no_workspace(tmp_path):
    rail = _make_rail(tmp_path)
    assert rail.workspace is None
    ctx = AgentCallbackContext(agent=None, inputs=None, session=_QAHistorySession())
    assert await rail._load_qa_block_history(ctx) == []


@pytest.mark.asyncio
async def test_load_qa_block_history_returns_empty_when_no_session(tmp_path):
    rail = _make_qa_history_rail(tmp_path)
    ctx = AgentCallbackContext(agent=None, inputs=None, session=None)
    assert await rail._load_qa_block_history(ctx) == []


@pytest.mark.asyncio
async def test_load_qa_block_history_returns_empty_when_registry_empty(tmp_path):
    rail = _make_qa_history_rail(tmp_path)
    session = _QAHistorySession()
    # No registry saved -> load_registry returns empty registry
    ctx = AgentCallbackContext(agent=None, inputs=None, session=session)
    assert await rail._load_qa_block_history(ctx) == []


@pytest.mark.asyncio
async def test_load_qa_block_history_returns_empty_when_no_history_blocks(tmp_path):
    """Blocks present but none flagged is_history -> skipped."""
    rail = _make_qa_history_rail(tmp_path)
    session = _QAHistorySession()
    registry = QABlockRegistry(
        session_id=session.get_session_id(),
        blocks={"qa_001": _history_entry("qa_001", 1, is_history=False)},
    )
    save_registry(session, registry)
    ctx = AgentCallbackContext(agent=None, inputs=None, session=session)
    assert await rail._load_qa_block_history(ctx) == []


@pytest.mark.asyncio
async def test_load_qa_block_history_loads_history_blocks_in_qa_index_order(tmp_path):
    """History blocks are read from L0 and parsed, ordered by qa_index."""
    rail = _make_qa_history_rail(tmp_path)
    session = _QAHistorySession(session_id="session-qa")
    sid = session.get_session_id()

    # Seed two L0 files out of order to verify sort by qa_index
    await _seed_qa_l0(
        tmp_path, sid, "qa_002",
        [UserMessage(content="second q"), AssistantMessage(content="second a")],
    )
    await _seed_qa_l0(
        tmp_path, sid, "qa_001",
        [UserMessage(content="first q"), AssistantMessage(content="first a")],
    )

    registry = QABlockRegistry(
        session_id=sid,
        blocks={
            "qa_002": _history_entry("qa_002", 2, is_history=True),
            "qa_001": _history_entry("qa_001", 1, is_history=True),
            # A non-history block must be ignored even if it has L0 on disk
            "qa_003": _history_entry("qa_003", 3, is_history=False),
        },
    )
    save_registry(session, registry)
    ctx = AgentCallbackContext(agent=None, inputs=None, session=session)

    result = await rail._load_qa_block_history(ctx)

    assert [m["content"] for m in result] == [
        "first q", "first a", "second q", "second a",
    ]
    assert [m["role"] for m in result] == ["user", "assistant", "user", "assistant"]


@pytest.mark.asyncio
async def test_load_qa_block_history_caps_to_most_recent_n_blocks(tmp_path):
    """When history exceeds _MAX_QA_HISTORY_BLOCKS, only the most recent N
    blocks (by qa_index) are loaded — early blocks are dropped."""
    from openjiuwen.harness.rails.skill_evolution_rail import _MAX_QA_HISTORY_BLOCKS

    rail = _make_qa_history_rail(tmp_path)
    session = _QAHistorySession(session_id="session-qa")
    sid = session.get_session_id()

    total = _MAX_QA_HISTORY_BLOCKS + 5  # 25 blocks when cap is 20
    blocks: dict[str, QABlockEntry] = {}
    for i in range(1, total + 1):
        qa_id = f"qa_{i:03d}"
        await _seed_qa_l0(
            tmp_path, sid, qa_id,
            [UserMessage(content=f"q{i}")],
        )
        blocks[qa_id] = _history_entry(qa_id, i, is_history=True)

    save_registry(session, QABlockRegistry(session_id=sid, blocks=blocks))
    ctx = AgentCallbackContext(agent=None, inputs=None, session=session)

    result = await rail._load_qa_block_history(ctx)

    # Only the last _MAX_QA_HISTORY_BLOCKS blocks (qa_index 6..25) survive,
    # in ascending qa_index order.
    expected = [f"q{i}" for i in range(6, total + 1)]
    assert [m["content"] for m in result] == expected
    assert len(result) == _MAX_QA_HISTORY_BLOCKS



@pytest.mark.asyncio
async def test_load_qa_block_history_skips_block_when_read_raises(tmp_path):
    """A failing read_l0 for one block must not abort the whole load."""
    rail = _make_qa_history_rail(tmp_path)
    session = _QAHistorySession(session_id="session-qa")
    sid = session.get_session_id()

    # qa_001 has a valid L0 file; qa_002 has no file on disk -> read returns []
    await _seed_qa_l0(
        tmp_path, sid, "qa_001",
        [UserMessage(content="ok q"), AssistantMessage(content="ok a")],
    )

    registry = QABlockRegistry(
        session_id=sid,
        blocks={
            "qa_001": _history_entry("qa_001", 1, is_history=True),
            "qa_002": _history_entry("qa_002", 2, is_history=True),
        },
    )
    save_registry(session, registry)
    ctx = AgentCallbackContext(agent=None, inputs=None, session=session)

    result = await rail._load_qa_block_history(ctx)
    assert [m["content"] for m in result] == ["ok q", "ok a"]


@pytest.mark.asyncio
async def test_collect_parsed_messages_prepends_qa_history(tmp_path):
    """QA history is prepended to buffer messages with dedup applied."""
    rail = _make_qa_history_rail(tmp_path)
    session = _QAHistorySession(session_id="session-qa")
    sid = session.get_session_id()

    await _seed_qa_l0(
        tmp_path, sid, "qa_001",
        [UserMessage(content="old question"), AssistantMessage(content="old answer")],
    )
    registry = QABlockRegistry(
        session_id=sid,
        blocks={"qa_001": _history_entry("qa_001", 1, is_history=True)},
    )
    save_registry(session, registry)

    ctx = AgentCallbackContext(
        agent=None,
        inputs=None,
        session=session,
        context=_MsgContext(messages=[
            {"role": "user", "content": "current question"},
            {"role": "assistant", "content": "current answer"},
        ]),
    )

    parsed = await rail._collect_parsed_messages(ctx)
    assert [m["content"] for m in parsed] == [
        "old question", "old answer",
        "current question", "current answer",
    ]


@pytest.mark.asyncio
async def test_collect_parsed_messages_dedupes_overlap_between_history_and_buffer(tmp_path):
    """When buffer already contains the same history message, duplicates are removed."""
    rail = _make_qa_history_rail(tmp_path)
    session = _QAHistorySession(session_id="session-qa")
    sid = session.get_session_id()

    shared_user = UserMessage(content="repeated question")
    await _seed_qa_l0(
        tmp_path, sid, "qa_001",
        [shared_user, AssistantMessage(content="old answer")],
    )
    registry = QABlockRegistry(
        session_id=sid,
        blocks={"qa_001": _history_entry("qa_001", 1, is_history=True)},
    )
    save_registry(session, registry)

    # Buffer contains the same user content again -> dedup keeps the history one
    ctx = AgentCallbackContext(
        agent=None,
        inputs=None,
        session=session,
        context=_MsgContext(messages=[
            {"role": "user", "content": "repeated question"},
            {"role": "assistant", "content": "current answer"},
        ]),
    )

    parsed = await rail._collect_parsed_messages(ctx)
    contents = [m["content"] for m in parsed]
    assert contents.count("repeated question") == 1
    assert contents == ["repeated question", "old answer", "current answer"]


@pytest.mark.asyncio
async def test_collect_parsed_messages_falls_back_to_buffer_when_qa_history_unavailable(tmp_path):
    """No workspace/session QA history must not break message collection."""
    rail = _make_rail(tmp_path)  # workspace stays None
    ctx = AgentCallbackContext(
        agent=None,
        inputs=None,
        session=None,
        context=_MsgContext(messages=[{"role": "user", "content": "only buffer"}]),
    )
    parsed = await rail._collect_parsed_messages(ctx)
    assert parsed == [{"role": "user", "content": "only buffer"}]


@pytest.mark.asyncio
async def test_collect_parsed_messages_proceeds_when_load_qa_history_raises(tmp_path):
    """If _load_qa_block_history raises, buffer messages are still returned."""
    rail = _make_qa_history_rail(tmp_path)

    async def _boom(ctx):
        raise RuntimeError("qa history load failed")

    rail._load_qa_block_history = _boom
    ctx = AgentCallbackContext(
        agent=None,
        inputs=None,
        session=None,
        context=_MsgContext(messages=[{"role": "user", "content": "buffer only"}]),
    )

    parsed = await rail._collect_parsed_messages(ctx)
    assert parsed == [{"role": "user", "content": "buffer only"}]

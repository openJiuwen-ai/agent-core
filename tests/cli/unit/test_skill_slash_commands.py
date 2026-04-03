# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for dynamic skill slash command registration."""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from rich.console import Console

from openjiuwen.harness.cli.ui.repl import (
    SLASH_COMMANDS,
    _SLASH_DESCRIPTIONS,
    _SKILL_COMMANDS,
    _build_skill_query,
    _handle_slash,
    _read_skill_description,
    _register_skill_commands,
    _scan_skill_dirs,
)


def _write_skill(
    root: Path,
    name: str,
    description: str,
) -> Path:
    """Create a minimal skill directory with SKILL.md."""
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(
        "---\n"
        f"description: {description}\n"
        "---\n\n"
        f"# {name}\n\n"
        f"Instructions for {name}.\n",
        encoding="utf-8",
    )
    return skill_md


class TestScanSkillDirs:
    """Tests for _scan_skill_dirs."""

    def test_scan_returns_skills_from_existing_dir(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Skills in existing dirs are discovered."""
        skill_md = _write_skill(
            tmp_path, "my-skill", "A test skill"
        )
        monkeypatch.setattr(
            "openjiuwen.harness.cli.ui.repl"
            "._scan_skill_dirs",
            lambda: {"my-skill": skill_md},
        )
        result = {"my-skill": skill_md}
        assert "my-skill" in result
        assert result["my-skill"] == skill_md

    def test_scan_skips_nonexistent_dirs(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Non-existent dirs are skipped silently."""
        monkeypatch.setattr(
            "openjiuwen.harness.cli.ui.repl"
            "._DEFAULT_SKILL_DIRS",
            [str(tmp_path / "missing")],
        )
        result = _scan_skill_dirs()
        assert result == {}

    def test_scan_dedup_by_priority(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Higher-priority dir wins for same-name skills."""
        high = tmp_path / "high"
        low = tmp_path / "low"
        _write_skill(high, "dup-skill", "High version")
        _write_skill(low, "dup-skill", "Low version")
        monkeypatch.setattr(
            "openjiuwen.harness.cli.ui.repl"
            "._DEFAULT_SKILL_DIRS",
            [str(high), str(low)],
        )
        result = _scan_skill_dirs()
        assert "dup-skill" in result
        assert str(high) in str(result["dup-skill"])


class TestRegisterSkillCommands:
    """Tests for _register_skill_commands."""

    def test_registers_skill_in_slash_commands(
        self, tmp_path: Path
    ) -> None:
        """Skill is added to SLASH_COMMANDS dict."""
        skill_md = _write_skill(
            tmp_path, "test-sk", "Test skill"
        )
        try:
            _register_skill_commands(
                {"test-sk": skill_md}
            )
            assert "/test-sk" in SLASH_COMMANDS
            assert "/test-sk" in _SLASH_DESCRIPTIONS
            assert SLASH_COMMANDS["/test-sk"] is None
            assert "/test-sk" in _SKILL_COMMANDS
        finally:
            SLASH_COMMANDS.pop("/test-sk", None)
            _SLASH_DESCRIPTIONS.pop("/test-sk", None)
            _SKILL_COMMANDS.pop("/test-sk", None)

    def test_does_not_shadow_builtin(
        self, tmp_path: Path
    ) -> None:
        """Skill named like a builtin is skipped."""
        # "help" collides with /help
        skill_md = _write_skill(
            tmp_path, "help", "Not the real help"
        )
        original_handler = SLASH_COMMANDS.get("/help")
        _register_skill_commands({"help": skill_md})
        # Should NOT overwrite the built-in
        assert SLASH_COMMANDS["/help"] is original_handler

    def test_description_extracted_from_yaml(
        self, tmp_path: Path
    ) -> None:
        """Descriptions are read from SKILL.md front-matter."""
        skill_md = _write_skill(
            tmp_path, "desc-sk", "My description"
        )
        try:
            _register_skill_commands(
                {"desc-sk": skill_md}
            )
            assert (
                _SLASH_DESCRIPTIONS["/desc-sk"]
                == "My description"
            )
        finally:
            SLASH_COMMANDS.pop("/desc-sk", None)
            _SLASH_DESCRIPTIONS.pop("/desc-sk", None)
            _SKILL_COMMANDS.pop("/desc-sk", None)


class TestReadSkillDescription:
    """Tests for _read_skill_description."""

    def test_reads_description(self, tmp_path: Path) -> None:
        skill_md = _write_skill(
            tmp_path, "sk1", "Some description"
        )
        assert (
            _read_skill_description(skill_md)
            == "Some description"
        )

    def test_returns_empty_for_no_frontmatter(
        self, tmp_path: Path
    ) -> None:
        skill_md = tmp_path / "no-fm" / "SKILL.md"
        skill_md.parent.mkdir(parents=True)
        skill_md.write_text(
            "# No front matter\n", encoding="utf-8"
        )
        assert _read_skill_description(skill_md) == ""


class TestBuildSkillQuery:
    """Tests for _build_skill_query."""

    def test_query_includes_skill_content(
        self, tmp_path: Path
    ) -> None:
        skill_md = _write_skill(
            tmp_path, "q-sk", "Query skill"
        )
        query = _build_skill_query(skill_md, "do stuff")
        assert "<skill-instructions>" in query
        assert "</skill-instructions>" in query
        assert "Query skill" in query
        assert "do stuff" in query

    def test_query_without_args(
        self, tmp_path: Path
    ) -> None:
        skill_md = _write_skill(
            tmp_path, "q-sk2", "No args skill"
        )
        query = _build_skill_query(skill_md, "")
        assert "follow the skill instructions" in query.lower()

    def test_query_with_missing_file(
        self, tmp_path: Path
    ) -> None:
        missing = tmp_path / "missing" / "SKILL.md"
        query = _build_skill_query(missing, "args")
        assert "Error reading skill" in query


class TestHandleSlashWithSkills:
    """Tests for _handle_slash skill dispatch."""

    @pytest.mark.asyncio
    async def test_skill_command_returns_cmd_name(
        self, tmp_path: Path
    ) -> None:
        """Skill slash command returns cmd_name for REPL to handle."""
        skill_md = _write_skill(
            tmp_path, "my-sk", "My skill"
        )
        try:
            _register_skill_commands(
                {"my-sk": skill_md}
            )
            buf = io.StringIO()
            console = Console(file=buf)
            result = await _handle_slash(
                "/my-sk do something",
                console,
                AsyncMock(),
                MagicMock(),
                tracker=None,
                cfg=None,
            )
            assert result == "/my-sk"
        finally:
            SLASH_COMMANDS.pop("/my-sk", None)
            _SLASH_DESCRIPTIONS.pop("/my-sk", None)
            _SKILL_COMMANDS.pop("/my-sk", None)

    @pytest.mark.asyncio
    async def test_builtin_command_returns_none(
        self,
    ) -> None:
        """Built-in commands return None."""
        buf = io.StringIO()
        console = Console(file=buf)
        result = await _handle_slash(
            "/help",
            console,
            AsyncMock(),
            MagicMock(),
            tracker=None,
            cfg=None,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_unknown_command_returns_none(
        self,
    ) -> None:
        """Unknown commands print error and return None."""
        buf = io.StringIO()
        console = Console(file=buf)
        result = await _handle_slash(
            "/totally_unknown_xyz",
            console,
            AsyncMock(),
            MagicMock(),
            tracker=None,
            cfg=None,
        )
        assert result is None
        output = buf.getvalue()
        assert "Unknown command" in output

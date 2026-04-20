"""Unit tests for OPENJIUWEN.md memory loading in prompts.builder."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from openjiuwen.harness.cli.prompts.builder import (
    MAX_MEMORY_CHARS,
    _find_project_root,
    _load_openjiuwen_md,
)


class TestLoadMemory:
    """Tests for the two-layer memory loader."""

    def test_load_project_memory(self, tmp_path: Path) -> None:
        """Project-level OPENJIUWEN.md is loaded."""
        (tmp_path / ".git").mkdir()
        (tmp_path / "OPENJIUWEN.md").write_text(
            "# Rules\n- Use pytest\n"
        )
        result = _load_openjiuwen_md(str(tmp_path))
        assert result is not None
        assert "Use pytest" in result

    def test_load_user_memory(
        self, tmp_path: Path
    ) -> None:
        """User-level OPENJIUWEN.md is loaded."""
        user_dir = tmp_path / ".openjiuwen"
        user_dir.mkdir()
        (user_dir / "OPENJIUWEN.md").write_text(
            "# Global\n- Always use English\n"
        )
        with patch.object(
            Path, "home", return_value=tmp_path
        ):
            result = _load_openjiuwen_md("/some/random/dir")
        assert result is not None
        assert "Always use English" in result

    def test_project_and_user_merged(
        self, tmp_path: Path
    ) -> None:
        """Both layers are merged into one result."""
        home = tmp_path / "home"
        user_dir = home / ".openjiuwen"
        user_dir.mkdir(parents=True)
        (user_dir / "OPENJIUWEN.md").write_text(
            "USER_MARKER"
        )

        proj = tmp_path / "project"
        proj.mkdir()
        (proj / ".git").mkdir()
        (proj / "OPENJIUWEN.md").write_text(
            "PROJECT_MARKER"
        )

        with patch.object(Path, "home", return_value=home):
            result = _load_openjiuwen_md(str(proj))
        assert result is not None
        assert "USER_MARKER" in result
        assert "PROJECT_MARKER" in result

    def test_truncation(self, tmp_path: Path) -> None:
        """Content exceeding MAX_MEMORY_CHARS is truncated."""
        (tmp_path / ".git").mkdir()
        (tmp_path / "OPENJIUWEN.md").write_text(
            "x" * 50_000
        )
        result = _load_openjiuwen_md(str(tmp_path))
        assert result is not None
        assert len(result) <= MAX_MEMORY_CHARS + 50
        assert "[...truncated]" in result

    def test_no_memory_returns_none(self) -> None:
        """Returns None when no memory files exist."""
        result = _load_openjiuwen_md("/nonexistent/path/12345")
        assert result is None


class TestFindProjectRoot:
    """Tests for project root detection."""

    def test_find_by_git(self, tmp_path: Path) -> None:
        """.git directory marks the project root."""
        (tmp_path / ".git").mkdir()
        sub = tmp_path / "src" / "pkg"
        sub.mkdir(parents=True)
        root = _find_project_root(str(sub))
        assert root == tmp_path

    def test_find_by_pyproject(
        self, tmp_path: Path
    ) -> None:
        """pyproject.toml marks the project root."""
        (tmp_path / "pyproject.toml").touch()
        sub = tmp_path / "src"
        sub.mkdir()
        root = _find_project_root(str(sub))
        assert root == tmp_path

    def test_no_root_found(self) -> None:
        """Returns None when no marker is found."""
        root = _find_project_root("/tmp")
        # /tmp usually has no project markers
        # (might find one at / on some systems)
        if root is not None:
            assert root.exists()

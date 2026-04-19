"""Unit tests for Coding Memory conflict handling.

Scope:
- conflict_types.py data models
- frontmatter.py (enrich_frontmatter, rebuild_content_with_frontmatter, _extract_body)
"""

import datetime
import pytest

from openjiuwen.core.memory.lite.conflict_types import (
    WriteMode,
    WriteResult,
)
from openjiuwen.core.memory.lite.frontmatter import (
    parse_frontmatter,
    validate_frontmatter,
    enrich_frontmatter,
    rebuild_content_with_frontmatter,
    _extract_body,
)


class TestConflictTypes:
    """Conflict types definition tests."""

    def test_write_mode_values(self):
        """Test WriteMode enum values."""
        assert WriteMode.CREATE.value == "create"
        assert WriteMode.APPEND.value == "append"
        assert WriteMode.SKIP.value == "skip"

    def test_write_result_basic(self):
        """Test WriteResult basic functionality."""
        result = WriteResult(
            success=True,
            path="/test/path.md",
            mode=WriteMode.CREATE,
        )
        assert result.success is True
        assert result.path == "/test/path.md"
        assert result.mode == WriteMode.CREATE
        assert result.conflict_detected is False
        assert result.conflicting_files == []
        assert result.note is None
        assert result.error is None

    def test_write_result_with_conflict(self):
        """Test WriteResult with conflict information."""
        result = WriteResult(
            success=True,
            path="/test/path.md",
            mode=WriteMode.CREATE,
            conflict_detected=True,
            conflicting_files=["old1.md", "old2.md"],
            note="Conflicts detected",
        )
        assert result.conflict_detected is True
        assert result.conflicting_files == ["old1.md", "old2.md"]
        assert result.note == "Conflicts detected"

    def test_write_result_to_dict_create(self):
        """Test to_dict for CREATE mode."""
        result = WriteResult(
            success=True,
            path="/test/path.md",
            mode=WriteMode.CREATE,
        )
        d = result.to_dict()
        assert d == {
            "success": True,
            "path": "/test/path.md",
            "mode": "create",
        }

    def test_write_result_to_dict_with_conflict(self):
        """Test to_dict with conflict information."""
        result = WriteResult(
            success=True,
            path="/test/path.md",
            mode=WriteMode.APPEND,
            conflict_detected=True,
            conflicting_files=["old.md"],
            note="Has conflicts",
        )
        d = result.to_dict()
        assert d["success"] is True
        assert d["path"] == "/test/path.md"
        assert d["mode"] == "append"
        assert d["conflict_detected"] is True
        assert d["conflicting_files"] == ["old.md"]
        assert d["note"] == "Has conflicts"

    def test_write_result_to_dict_skip(self):
        """Test to_dict for SKIP mode."""
        result = WriteResult(
            success=True,
            path="/test/path.md",
            mode=WriteMode.SKIP,
            note="Content is redundant",
        )
        d = result.to_dict()
        assert d["mode"] == "skip"
        assert d["note"] == "Content is redundant"

    def test_write_result_to_dict_with_error(self):
        """Test to_dict with error information."""
        result = WriteResult(
            success=False,
            path="/test/path.md",
            mode=WriteMode.CREATE,
            error="Invalid frontmatter",
        )
        d = result.to_dict()
        assert d["success"] is False
        assert d["error"] == "Invalid frontmatter"


class TestFrontmatterEnrich:
    """Frontmatter enrichment function tests."""

    def test_enrich_frontmatter_create(self):
        """Test enrich_frontmatter in create mode."""
        fm = {
            "name": "Test Memory",
            "description": "Test description",
            "type": "user",
        }
        result = enrich_frontmatter(fm, is_edit=False)

        # Should add created_at and updated_at
        assert "created_at" in result
        assert "updated_at" in result
        assert result["created_at"] == result["updated_at"]
        # Original fields preserved
        assert result["name"] == "Test Memory"
        assert result["description"] == "Test description"
        assert result["type"] == "user"

    def test_enrich_frontmatter_create_preserves_existing_created_at(self):
        """Test enrich_frontmatter preserves existing created_at in create mode."""
        fm = {
            "name": "Test Memory",
            "description": "Test description",
            "type": "user",
            "created_at": "2026-01-01",
        }
        result = enrich_frontmatter(fm, is_edit=False)

        # Should preserve original created_at, only update updated_at
        assert result["created_at"] == "2026-01-01"
        assert result["updated_at"] != "2026-01-01"  # Should be today's date

    def test_enrich_frontmatter_edit(self):
        """Test enrich_frontmatter in edit mode."""
        fm = {
            "name": "Test Memory",
            "description": "Test description",
            "type": "user",
            "created_at": "2026-01-01",
        }
        result = enrich_frontmatter(fm, is_edit=True)

        # Edit mode should not add created_at (if already exists)
        assert result["created_at"] == "2026-01-01"
        # But should update updated_at
        assert "updated_at" in result
        assert result["updated_at"] != "2026-01-01"


class TestFrontmatterExtractBody:
    """Frontmatter body extraction tests."""

    def test_extract_body_with_frontmatter(self):
        """Test extracting body from content with frontmatter."""
        content = """---
name: Test Memory
description: Test description
type: user
---

This is the body content.
It can have multiple lines.
"""
        body = _extract_body(content)
        assert "This is the body content." in body
        assert "It can have multiple lines." in body
        assert "name:" not in body
        assert "---" not in body

    def test_extract_body_without_frontmatter(self):
        """Test extracting body from content without frontmatter."""
        content = "This is pure body content without frontmatter."
        body = _extract_body(content)
        assert body == content

    def test_extract_body_empty_after_frontmatter(self):
        """Test case with no content after frontmatter."""
        content = """---
name: Test Memory
description: Test description
type: user
---"""
        body = _extract_body(content)
        assert body == ""

    def test_extract_body_whitespace_handling(self):
        """Test whitespace handling."""
        content = """---
name: Test
---

  Trimmed content  """
        body = _extract_body(content)
        assert body.strip() == "Trimmed content"


class TestFrontmatterRebuild:
    """Frontmatter rebuild tests."""

    def test_rebuild_content_with_frontmatter(self):
        """Test rebuilding content with new frontmatter."""
        original_content = """---
name: Old Name
description: Old description
type: user
---

Body content here."""

        new_fm = {
            "name": "New Name",
            "description": "New description",
            "type": "feedback",
            "updated_at": "2026-04-14",
        }

        result = rebuild_content_with_frontmatter(original_content, new_fm)

        # New frontmatter should be included
        assert "name: New Name" in result
        assert "description: New description" in result
        assert "type: feedback" in result
        assert "updated_at: 2026-04-14" in result

        # Old frontmatter should not exist
        assert "Old Name" not in result
        assert "Old description" not in result

        # Body should be preserved
        assert "Body content here." in result

    def test_rebuild_content_preserves_body_formatting(self):
        """Test that body formatting is preserved during rebuild."""
        original_content = """---
name: Test
---

# Heading

- List item 1
- List item 2

Paragraph with **bold** text."""

        new_fm = {"name": "Updated", "updated_at": "2026-04-14"}
        result = rebuild_content_with_frontmatter(original_content, new_fm)

        assert "# Heading" in result
        assert "- List item 1" in result
        assert "**bold**" in result

    def test_rebuild_content_empty_body(self):
        """Test case with empty body."""
        original_content = """---
name: Test
---"""

        new_fm = {"name": "Updated", "updated_at": "2026-04-14"}
        result = rebuild_content_with_frontmatter(original_content, new_fm)

        # Should only have frontmatter
        assert "name: Updated" in result
        assert "updated_at: 2026-04-14" in result


class TestFrontmatterIntegration:
    """Frontmatter function integration tests."""

    def test_full_workflow_create(self):
        """Test full workflow for creating a memory."""
        # 1. Parse original content
        content = """---
name: User Preference
description: User likes dark mode
type: user
---

User prefers dark mode for all applications."""

        fm = parse_frontmatter(content)
        assert fm is not None

        # 2. Validate frontmatter
        valid, err = validate_frontmatter(fm)
        assert valid is True
        assert err == ""

        # 3. Enrich frontmatter
        fm = enrich_frontmatter(fm, is_edit=False)
        assert "created_at" in fm
        assert "updated_at" in fm

        # 4. Rebuild content
        new_content = rebuild_content_with_frontmatter(content, fm)

        # 5. Verify rebuilt content
        assert "created_at:" in new_content
        assert "updated_at:" in new_content
        assert "User prefers dark mode" in new_content

    def test_full_workflow_edit(self):
        """Test full workflow for editing a memory."""
        # 1. Existing content with timestamps
        content = """---
name: User Preference
description: User likes dark mode
type: user
created_at: 2026-01-01
updated_at: 2026-01-01
---

User prefers dark mode for all applications."""

        fm = parse_frontmatter(content)

        # 2. Edit mode enrichment (should preserve created_at)
        fm = enrich_frontmatter(fm, is_edit=True)
        assert fm["created_at"] == "2026-01-01"  # Preserve original value
        assert fm["updated_at"] != "2026-01-01"  # Update to new value

        # 3. Rebuild content
        new_content = rebuild_content_with_frontmatter(content, fm)

        # 4. Verify
        today = datetime.date.today().isoformat()
        assert "created_at: 2026-01-01" in new_content
        assert f"updated_at: {today}" in new_content  # Today's date


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

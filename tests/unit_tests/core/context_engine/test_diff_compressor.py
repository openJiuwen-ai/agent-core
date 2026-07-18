from __future__ import annotations

from openjiuwen.core.context_engine.processor.forked.offloader.rule_compression.compressors.diff_compressor import (
    DiffCompressor,
)
from openjiuwen.core.context_engine.processor.forked.offloader.rule_compression.types import (
    ContentType,
    RuleContext,
)


def _ctx(**overrides):
    values = {
        "max_tokens": 1600,
        "diff_min_lines": 1,
        "diff_max_context_lines": 1,
        "diff_max_hunks_per_file": 10,
        "diff_max_files": 20,
        "min_savings_ratio": 0.0,
        "count_tokens": lambda text: max(len(text) // 3, 1),
    }
    values.update(overrides)
    return RuleContext(**values)


def test_diff_compression_preserves_changes_and_appends_unified_summary():
    content = "\n".join(
        [
            "commit abc123",
            "Author: Dev <dev@example.com>",
            "diff --git a/auth.py b/auth.py",
            "index 1111111..2222222 100644",
            "--- a/auth.py",
            "+++ b/auth.py",
            "@@ -1,9 +1,9 @@",
            " import os",
            " import sys",
            "",
            " from config import settings",
            "-old_token = get_legacy_token()",
            "+new_token = get_secure_token()",
            " log('token loaded')",
            " user = get_user()",
            " return user",
            "diff --git a/readme.md b/readme.md",
            "--- a/readme.md",
            "+++ b/readme.md",
            "@@ -1,6 +1,6 @@",
            " # Project",
            " Intro",
            "-Old setup",
            "+New setup",
            " More docs",
            " More docs 2",
        ]
    )

    result = DiffCompressor().compress(
        content,
        _ctx(diff_max_files=1),
    )

    assert result.modified is True
    assert result.lossy is True
    assert result.content_type == ContentType.GIT_DIFF
    assert "commit abc123" in result.content
    assert "diff --git a/auth.py b/auth.py" in result.content
    assert "-old_token = get_legacy_token()" in result.content
    assert "+new_token = get_secure_token()" in result.content
    assert "diff --git a/readme.md b/readme.md" not in result.content
    assert "[2 files changed, +2 -2 lines, 0 hunks omitted, 1 files omitted]" in result.content
    assert result.details["additions"] == 2
    assert result.details["deletions"] == 2
    assert result.details["original_line_count"] == len(content.splitlines())
    assert result.details["compressed_line_count"] == len(result.content.splitlines())
    assert result.details["should_offload_original"] is True


def test_query_and_priority_scoring_keep_relevant_middle_hunk():
    content = "\n".join(
        [
            "diff --git a/auth.py b/auth.py",
            "--- a/auth.py",
            "+++ b/auth.py",
            "@@ -1,4 +1,4 @@",
            " first context",
            "-first_old",
            "+first_new",
            " first tail",
            "@@ -20,4 +20,4 @@",
            " password context",
            "-if password == 'admin':",
            "+if verify_password(user, password):",
            " security tail",
            "@@ -40,4 +40,4 @@",
            " ordinary context",
            "-ordinary_old",
            "+ordinary_new",
            " ordinary tail",
            "@@ -60,4 +60,4 @@",
            " final context",
            "-final_old",
            "+final_new",
            " final tail",
        ]
    )

    result = DiffCompressor().compress(
        content,
        _ctx(
            diff_max_hunks_per_file=3,
            query_terms=frozenset({"password"}),
        ),
    )

    assert result.modified is True
    assert "@@ -1,4 +1,4 @@" in result.content
    assert "@@ -20,4 +20,4 @@" in result.content
    assert "@@ -40,4 +40,4 @@" not in result.content
    assert "@@ -60,4 +60,4 @@" in result.content
    assert "[1 files changed, +4 -4 lines, 1 hunks omitted, 0 files omitted]" in result.content

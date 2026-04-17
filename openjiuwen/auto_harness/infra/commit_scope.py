# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Commit scope helpers for auto-harness."""

from __future__ import annotations

import re
from pathlib import PurePosixPath


_TEST_FILE_RE = re.compile(
    r"(tests/(?:unit_tests|system_tests)/[^\s:'\"]+\.py)"
)
_DOC_DIRS = ("docs/en/", "docs/zh/")


def is_documentation_file(path: str) -> bool:
    """Return whether path points to a markdown file under docs/."""
    normalized = path.strip().replace("\\", "/")
    return normalized.startswith("docs/") and normalized.endswith(".md")


def is_allowed_documentation_file(path: str) -> bool:
    """Return whether a documentation file follows the bilingual docs layout."""
    normalized = path.strip().replace("\\", "/")
    return any(normalized.startswith(prefix) for prefix in _DOC_DIRS)


def derive_test_files(
    task_files: list[str],
) -> list[str]:
    """Derive candidate test files from declared source files."""
    derived: list[str] = []

    for path in task_files:
        normalized = path.strip().replace("\\", "/")
        if not normalized or not normalized.endswith(".py"):
            continue
        if normalized.startswith("tests/"):
            continue
        if PurePosixPath(normalized).name == "__init__.py":
            continue

        stem = PurePosixPath(normalized).stem
        test_name = f"test_{stem}.py"
        derived.extend([
            f"tests/unit_tests/**/{test_name}",
            f"tests/system_tests/**/{test_name}",
        ])

    return list(dict.fromkeys(derived))


def is_derived_test_file(
    source_files: list[str],
    candidate: str,
) -> bool:
    """Check whether candidate matches the source->test mapping rule."""
    normalized_candidate = candidate.strip().replace("\\", "/")
    if not normalized_candidate.startswith("tests/"):
        return False
    candidate_name = PurePosixPath(normalized_candidate).name
    if not candidate_name.startswith("test_"):
        return False

    for path in source_files:
        normalized = path.strip().replace("\\", "/")
        if (
            not normalized
            or not normalized.endswith(".py")
            or normalized.startswith("tests/")
            or PurePosixPath(normalized).name == "__init__.py"
        ):
            continue
        stem = PurePosixPath(normalized).stem
        if candidate_name == f"test_{stem}.py":
            return True
    return False


def extract_verify_related_files(
    ci_result: dict | None,
    fix_logs: str | None = None,
) -> list[str]:
    """Extract test file paths explicitly mentioned by verification output."""
    texts: list[str] = []
    if ci_result:
        texts.append(str(ci_result.get("errors", "")))
        for gate in ci_result.get("gates", []):
            texts.append(str(gate.get("output", "")))
    if fix_logs:
        texts.append(fix_logs)

    files: list[str] = []
    for text in texts:
        files.extend(_TEST_FILE_RE.findall(text))
    return list(dict.fromkeys(files))


def derive_legacy_related_test_files(
    edited_files: list[str],
    verify_related_files: list[str],
) -> list[str]:
    """Allow adapted legacy tests only when both edited and directly referenced."""
    verify_set = {
        path.strip().replace("\\", "/")
        for path in verify_related_files
        if path.strip()
    }
    related = []
    for path in edited_files:
        normalized = path.strip().replace("\\", "/")
        if normalized.startswith("tests/") and normalized in verify_set:
            related.append(normalized)
    return list(dict.fromkeys(related))

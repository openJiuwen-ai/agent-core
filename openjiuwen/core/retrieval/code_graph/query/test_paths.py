# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Heuristics for dropping test files from Code Graph search (Retropus-compatible)."""

from __future__ import annotations

import re

_TEST_PATH_RE = re.compile(
    r"(^|/)(tests?|testing)(/|$)|(^|/)test_[^/]+\.(py|js|ts|java|go|rb|rs)$|"
    r"(^|/)[^/]+_test\.(py|js|ts|java|go|rb|rs)$|(^|/)conftest\.py$",
    re.IGNORECASE,
)
_ISSUE_ABOUT_TESTS_RE = re.compile(
    r"\b(unit\s*tests?|test\s*suite|pytest|unittest|failing\s+tests?|test\s+file)\b",
    re.IGNORECASE,
)


def is_test_path(rel: str) -> bool:
    """True when *rel* looks like a unit-test path."""
    return bool(_TEST_PATH_RE.search(str(rel or "").replace("\\", "/")))


def issue_about_tests(text: str) -> bool:
    """True when the issue itself is about tests (Retropus heuristic)."""
    return bool(_ISSUE_ABOUT_TESTS_RE.search(str(text or "")))

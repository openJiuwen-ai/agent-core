# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Semantic version helpers for skill self-evolution.

Self-evolution only bumps MINOR / PATCH. MAJOR is owned by the business
party or third-party software and is never incremented automatically.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any, Final, Iterable, Optional, Tuple

_DEFAULT_MAJOR: Final[int] = 1
_DEFAULT_MINOR: Final[int] = 0
_DEFAULT_PATCH: Final[int] = 0
_DEFAULT_VERSION: Final[str] = "1.0.0"

_SEMVER_RE = re.compile(
    r"^\s*v?(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)\s*$",
    re.IGNORECASE,
)

_PATCH_SOURCES: Final[frozenset[str]] = frozenset(
    {
        "execution_failure",
        "script_artifact",
        "conversation_review",
    }
)


class VersionBump(str, Enum):
    """Self-evolution bump levels (MAJOR is never auto-applied)."""

    PATCH = "patch"
    MINOR = "minor"


def classify_version_bump(source: str, section: str) -> VersionBump:
    """Classify bump level from experience source and change section.

    Hard rules (priority order):
    1. source in {execution_failure, script_artifact, conversation_review} -> PATCH
    2. section == Examples -> PATCH
    3. section == Instructions -> MINOR
    4. otherwise -> PATCH
    """
    if source in _PATCH_SOURCES:
        return VersionBump.PATCH
    if section == "Examples":
        return VersionBump.PATCH
    if section == "Instructions":
        return VersionBump.MINOR
    return VersionBump.PATCH


def aggregate_version_bump(records: Iterable[Any]) -> Optional[VersionBump]:
    """Aggregate bump level across evolution records for one rebuild.

    - Empty records -> None (no bump)
    - Any record classified as MINOR -> MINOR
    - Otherwise -> PATCH

    Each record is expected to expose ``source`` and ``change.section``.
    """
    saw_any = False
    for record in records:
        saw_any = True
        source = getattr(record, "source", "") or ""
        change = getattr(record, "change", None)
        section = getattr(change, "section", "") if change is not None else ""
        if classify_version_bump(source, section or "") is VersionBump.MINOR:
            return VersionBump.MINOR
    if not saw_any:
        return None
    return VersionBump.PATCH


def parse_semver(version: str) -> Tuple[int, int, int]:
    """Parse ``MAJOR.MINOR.PATCH``; invalid / empty values fall back to 1.0.0."""
    if not version:
        return _DEFAULT_MAJOR, _DEFAULT_MINOR, _DEFAULT_PATCH
    matched = _SEMVER_RE.match(version)
    if matched is None:
        return _DEFAULT_MAJOR, _DEFAULT_MINOR, _DEFAULT_PATCH
    return (
        int(matched.group("major")),
        int(matched.group("minor")),
        int(matched.group("patch")),
    )


def format_semver(major: int, minor: int, patch: int) -> str:
    """Format a semver triple as ``MAJOR.MINOR.PATCH``."""
    return f"{major}.{minor}.{patch}"


def bump_semver(current: str, level: VersionBump) -> str:
    """Bump MINOR or PATCH while preserving MAJOR.

    - PATCH: ``x.y.z`` -> ``x.y.(z+1)``
    - MINOR: ``x.y.z`` -> ``x.(y+1).0``
    """
    major, minor, patch = parse_semver(current or _DEFAULT_VERSION)
    if level == VersionBump.MINOR:
        return format_semver(major, minor + 1, 0)
    return format_semver(major, minor, patch + 1)

# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.

"""Version numbers and whitespace-separated comparison lists.

A version is ``MAJOR.MINOR.PATCH``. A specifier such as ``>=1.0.0 <2.0.0``
matches only when every clause matches. An empty specifier matches any version.
"""

import re
from functools import total_ordering

_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
_CLAUSE_RE = re.compile(r"^(>=|<=|!=|==|>|<)?\s*(\d+\.\d+\.\d+)$")


@total_ordering
class Version:
    """Three-part numeric version."""

    __slots__ = ("major", "minor", "patch")

    def __init__(self, major: int, minor: int, patch: int) -> None:
        self.major = major
        self.minor = minor
        self.patch = patch

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        return (self.major, self.minor, self.patch) == (other.major, other.minor, other.patch)

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        return (self.major, self.minor, self.patch) < (other.major, other.minor, other.patch)

    def __hash__(self) -> int:
        return hash((self.major, self.minor, self.patch))

    def __repr__(self) -> str:
        return f"Version({self.major}, {self.minor}, {self.patch})"

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


def parse_version(text: str) -> Version:
    """Parse ``MAJOR.MINOR.PATCH``.

    Raises:
        ValueError: The text is not three numeric components.
    """
    match = _VERSION_RE.fullmatch(text.strip())
    if match is None:
        raise ValueError(f"version must be MAJOR.MINOR.PATCH, got {text!r}")
    major, minor, patch = (int(part) for part in match.groups())
    return Version(major, minor, patch)


def specifier_matches(version: str, specifier: str) -> bool:
    """Return whether ``version`` satisfies every clause in ``specifier``.

    Raises:
        ValueError: A version or a clause cannot be parsed.
    """
    parsed = parse_version(version)
    text = specifier.strip()
    if not text:
        return True
    for clause in text.split():
        match = _CLAUSE_RE.fullmatch(clause)
        if match is None:
            raise ValueError(f"cannot parse version specifier clause {clause!r}")
        operator = match.group(1) or "=="
        other = parse_version(match.group(2))
        if not _compare(parsed, operator, other):
            return False
    return True


def _compare(left: Version, operator: str, right: Version) -> bool:
    if operator == "==":
        return left == right
    if operator == "!=":
        return left != right
    if operator == ">":
        return left > right
    if operator == ">=":
        return left >= right
    if operator == "<":
        return left < right
    if operator == "<=":
        return left <= right
    raise ValueError(f"unsupported version comparison {operator}")

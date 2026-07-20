# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for skill evolution semantic versioning helpers."""

from __future__ import annotations

import pytest

from openjiuwen.agent_evolving.checkpointing.versioning import (
    VersionBump,
    aggregate_version_bump,
    bump_semver,
    classify_version_bump,
    format_semver,
    parse_semver,
)
from openjiuwen.agent_evolving.checkpointing.types import EvolutionPatch, EvolutionRecord
from openjiuwen.agent_evolving.signal.base import EvolutionTarget


def _record(source: str, section: str) -> EvolutionRecord:
    return EvolutionRecord(
        id="ev_x",
        source=source,
        timestamp="2026-01-01T00:00:00+00:00",
        context="ctx",
        change=EvolutionPatch(
            section=section,
            action="append",
            content="c",
            target=EvolutionTarget.BODY,
        ),
    )


class TestClassifyVersionBump:
    @staticmethod
    @pytest.mark.parametrize(
        ("source", "section", "expected"),
        [
            # User example: execution_failure + Instructions -> PATCH
            ("execution_failure", "Instructions", VersionBump.PATCH),
            ("execution_failure", "Troubleshooting", VersionBump.PATCH),
            ("script_artifact", "Scripts", VersionBump.PATCH),
            ("conversation_review", "Examples", VersionBump.PATCH),
            ("user_correction", "Examples", VersionBump.PATCH),
            # User example: user_correction + Instructions -> MINOR
            ("user_correction", "Instructions", VersionBump.MINOR),
            ("low_score", "Instructions", VersionBump.MINOR),
            ("user_correction", "Troubleshooting", VersionBump.PATCH),
            ("unknown", "Scripts", VersionBump.PATCH),
        ],
    )
    def test_hard_rules(source: str, section: str, expected: VersionBump):
        assert classify_version_bump(source, section) is expected


class TestAggregateVersionBump:
    @staticmethod
    def test_empty_returns_none():
        assert aggregate_version_bump([]) is None

    @staticmethod
    def test_all_patch():
        records = [
            _record("execution_failure", "Troubleshooting"),
            _record("script_artifact", "Scripts"),
        ]
        assert aggregate_version_bump(records) is VersionBump.PATCH

    @staticmethod
    def test_any_minor_wins():
        records = [
            _record("execution_failure", "Troubleshooting"),
            _record("user_correction", "Instructions"),
        ]
        assert aggregate_version_bump(records) is VersionBump.MINOR


class TestParseAndBumpSemver:
    @staticmethod
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("1.2.3", (1, 2, 3)),
            ("v2.0.1", (2, 0, 1)),
            (" 3.4.5 ", (3, 4, 5)),
            ("", (1, 0, 0)),
            ("not-a-version", (1, 0, 0)),
            ("1.2", (1, 0, 0)),
        ],
    )
    def test_parse_semver(raw: str, expected: tuple[int, int, int]):
        assert parse_semver(raw) == expected

    @staticmethod
    def test_format_semver():
        assert format_semver(2, 1, 0) == "2.1.0"

    @staticmethod
    def test_bump_patch_preserves_major_and_minor():
        assert bump_semver("2.3.4", VersionBump.PATCH) == "2.3.5"

    @staticmethod
    def test_bump_minor_preserves_major_and_resets_patch():
        assert bump_semver("2.3.4", VersionBump.MINOR) == "2.4.0"

    @staticmethod
    def test_bump_invalid_falls_back_to_default():
        assert bump_semver("bad", VersionBump.PATCH) == "1.0.1"
        assert bump_semver("", VersionBump.MINOR) == "1.1.0"

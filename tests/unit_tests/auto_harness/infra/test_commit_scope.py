# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Unit tests for commit scope helpers."""

from openjiuwen.auto_harness.infra.commit_scope import (
    derive_legacy_related_test_files,
    derive_test_files,
    extract_verify_related_files,
    is_allowed_documentation_file,
    is_derived_test_file,
)


def test_derive_test_files_for_python_module():
    result = derive_test_files(
        ["openjiuwen/auto_harness/schema.py"]
    )
    assert result == [
        "tests/unit_tests/**/test_schema.py",
        "tests/system_tests/**/test_schema.py",
    ]


def test_derive_test_files_skips_init_and_tests():
    result = derive_test_files(
        [
            "openjiuwen/auto_harness/__init__.py",
            "tests/unit_tests/auto_harness/test_schema.py",
        ]
    )
    assert result == []


def test_is_derived_test_file_matches_same_basename():
    assert is_derived_test_file(
        ["openjiuwen/auto_harness/schema.py"],
        "tests/unit_tests/auto_harness/test_schema.py",
    )
    assert not is_derived_test_file(
        ["openjiuwen/auto_harness/schema.py"],
        "tests/unit_tests/auto_harness/test_other.py",
    )


def test_extract_verify_related_files_finds_test_paths():
    ci_result = {
        "errors": (
            "FAILED tests/unit_tests/auto_harness/test_schema.py::test_x"
        ),
        "gates": [],
    }
    assert extract_verify_related_files(ci_result) == [
        "tests/unit_tests/auto_harness/test_schema.py"
    ]


def test_derive_legacy_related_test_files_requires_edit_and_reference():
    result = derive_legacy_related_test_files(
        [
            "tests/unit_tests/auto_harness/test_schema.py",
            "tests/unit_tests/auto_harness/test_other.py",
        ],
        [
            "tests/unit_tests/auto_harness/test_schema.py",
        ],
    )
    assert result == [
        "tests/unit_tests/auto_harness/test_schema.py"
    ]


def test_is_allowed_documentation_file_limits_docs_layout():
    assert is_allowed_documentation_file(
        "docs/en/guide.md"
    )
    assert not is_allowed_documentation_file(
        "docs/auto-harness-agent-design.md"
    )
    assert not is_allowed_documentation_file(
        "README.md"
    )

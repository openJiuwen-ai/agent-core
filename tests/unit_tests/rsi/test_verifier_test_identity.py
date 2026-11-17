# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Failure identities must be grounded in verifier output, not fuzzy name matching."""

from __future__ import annotations

from copy import deepcopy

import pytest

from openjiuwen.rsi.harness_rsi.evaluation_result_analyzer.analyzer import (
    _diagnosis_validation_conflicts,
)


def _check(observed, reported, output="", *, regressions=None):
    inventory = {
        "patch_successfully_applied": True,
        "resolved": False,
        "failed_fail_to_pass_tests": reported,
        "failed_pass_to_pass_tests": regressions or [],
        "verifier_failure_output_excerpt": output,
    }
    diagnosis = {
        "root_cause": "The patch omitted an exception raised by the runtime operation.",
        "verifier_observations": {
            "patch_successfully_applied": True,
            "failed_fail_to_pass_tests": observed,
            "failed_pass_to_pass_tests": [],
        },
    }
    before = deepcopy((diagnosis, inventory))
    errors = _diagnosis_validation_conflicts(diagnosis, {}, inventory)
    assert (diagnosis, inventory) == before
    return errors


@pytest.mark.parametrize("suffix", ["", " - ValueError: unsupported value"])
def test_full_parameterized_name_matches_unique_report_token(suffix):
    complete = "tests/test_format.py::test_render[format value]"
    assert not _check([complete], [complete.split()[0]], f"FAILED {complete}{suffix}")


@pytest.mark.parametrize("newline", [r"\n", r"\\n", "\n"])
def test_multiline_parameter_id_is_grounded_in_failure_output(newline):
    prefix = "tests/test_format.py::test_render["
    complete = prefix + r"\n            " + "\"{:4x}\".format('1')" + r"\n            ]"
    observed = complete.replace(r"\n", newline)
    assert not _check([observed], [prefix + r"\n"], f"FAILED {complete}\n1 failed, 15 passed")


def test_failure_list_order_validation_is_unchanged():
    assert _check(["check_b", "check_a"], ["check_a", "check_b"])


@pytest.mark.parametrize(
    ("observed", "reported", "output"),
    [
        (["test_x[a b]"], ["test_x[a"], ""),
        (["test_x[a b]"], ["test_x[a"], "PASSED test_x[a b]"),
        (["test_x[a b]"], ["test_x[a"], "FAILED test_x[a c]"),
        (["test_x[a b"], ["test_x[a"], "FAILED test_x[a b - c]"),
        (["test_x[a b"], ["test_x[a"], "FAILED test_x[a b"),
        (["test_x - ValueError"], ["test_x"], "FAILED test_x - ValueError"),
        (["test_x[a] - [ValueError]"], ["test_x[a]"], "FAILED test_x[a] - [ValueError]"),
        (["test_x[a b]"], ["test_x[a"], "FAILED test_x[a b]\nFAILED test_x[a c]"),
        (["test_x[a b]"], ["test_x[a b c]"], "FAILED test_x[a b]"),
        (["test_x[a b]"], ["test_x[a b2]"], "FAILED test_x[a b]"),
        (["test_other[a b]"], ["test_x[a"], "FAILED test_other[a b]"),
        (["test_x"], ["test_x[a"], "FAILED test_x[a b]"),
        (["test_x[a b]", "invented"], ["test_x[a"], "FAILED test_x[a b]"),
        ([], ["test_x"], "FAILED test_x"),
        (["test_x"], [], "FAILED test_x"),
        (["test_x", "test_x"], ["test_x", "test_y"], "FAILED test_x\nFAILED test_y"),
        ("test_x", ["test_x"], "FAILED test_x"),
        ([None], ["test_x"], "FAILED test_x"),
        (None, [], ""),
    ],
)
def test_unproven_or_ambiguous_name_changes_still_fail(observed, reported, output):
    assert _check(observed, reported, output)


def test_missing_regression_cannot_be_hidden_by_target_failure_alias():
    complete = "test_x[a b]"
    assert _check([complete], ["test_x[a"], f"FAILED {complete}", regressions=["test_regression"])


def test_false_patch_application_claim_still_fails():
    inventory = {
        "patch_successfully_applied": True,
        "resolved": False,
        "failed_fail_to_pass_tests": ["test_x"],
        "failed_pass_to_pass_tests": [],
    }
    diagnosis = {"verifier_observations": {**inventory, "patch_successfully_applied": False}}
    assert _diagnosis_validation_conflicts(diagnosis, {}, inventory)

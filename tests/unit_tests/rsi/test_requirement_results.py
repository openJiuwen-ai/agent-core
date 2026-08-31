# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from openjiuwen.rsi.harness_rsi.evaluator.requirement_results import (
    evaluation_requirement_results,
    requirement_results_from_judge_criteria,
)


def test_judge_criteria_are_mapped_to_common_requirement_contract() -> None:
    contract = requirement_results_from_judge_criteria(
        [
            {"verifier_id": "ver_a", "score": 1.0, "rationale": "met"},
            {"verifier_id": "ver_b", "score": 0.0, "rationale": "missing"},
        ]
    )

    assert contract["schema_version"] == 1
    assert contract["items"] == [
        {
            "requirement_id": "ver_a",
            "group": "requirement",
            "passed": True,
            "score": 1.0,
            "evidence": "met",
            "status": "",
            "source": "judge_detail.criteria",
        },
        {
            "requirement_id": "ver_b",
            "group": "requirement",
            "passed": False,
            "score": 0.0,
            "evidence": "missing",
            "status": "",
            "source": "judge_detail.criteria",
        },
    ]


def test_legacy_atomic_and_swe_results_share_common_reader() -> None:
    results = evaluation_requirement_results(
        {
            "atomic_checks": [
                {"name": "summary", "passed": True},
                {"name": "rows", "passed": False, "detail": "expected four rows"},
            ],
            "instance_report": {
                "case_001": {
                    "tests_status": {
                        "FAIL_TO_PASS": {"success": ["fixed"], "failure": ["remaining"]},
                        "PASS_TO_PASS": {"success": ["stable"], "failure": []},
                    }
                }
            },
        },
        case_id="case_001",
    )

    by_id = {(item["group"], item["requirement_id"]): item for item in results}
    assert by_id[("atomic_check", "summary")]["passed"] is True
    assert by_id[("atomic_check", "rows")]["evidence"] == "expected four rows"
    assert by_id[("fail_to_pass", "fixed")]["passed"] is True
    assert by_id[("fail_to_pass", "remaining")]["passed"] is False
    assert by_id[("pass_to_pass", "stable")]["passed"] is True


def test_explicit_common_contract_is_authoritative_over_legacy_metadata() -> None:
    results = evaluation_requirement_results(
        {
            "requirement_results": {
                "schema_version": 1,
                "items": [{"requirement_id": "criterion", "passed": True}],
            },
            "atomic_checks": [{"name": "legacy", "passed": False}],
        }
    )

    assert [item["requirement_id"] for item in results] == ["criterion"]


def test_legacy_atomic_check_without_verdict_remains_unknown() -> None:
    results = evaluation_requirement_results(
        {
            "atomic_checks": [
                {"name": "not_instrumented", "status": "not_available"},
            ]
        }
    )

    assert results[0]["passed"] is None
    assert results[0]["status"] == "not_available"

# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""test_evaluate_prompt_context — evaluator CI 上下文摘要测试。"""

from openjiuwen.auto_harness.stages.implement import (
    _format_ci_status_for_evaluator,
)


def test_format_ci_status_for_evaluator_pass():
    status = _format_ci_status_for_evaluator({
        "passed": True,
        "gates": [
            {"name": "static_check", "passed": True, "output": ""},
            {"name": "ut", "passed": True, "output": ""},
        ],
        "errors": "",
    })

    assert status == (
        "结论: pass\n"
        "- static_check: PASS\n"
        "- ut: PASS"
    )


def test_format_ci_status_for_evaluator_blocking_failure():
    status = _format_ci_status_for_evaluator({
        "passed": False,
        "gates": [
            {
                "name": "st",
                "passed": False,
                "output": "environment failure: missing token",
            },
            {
                "name": "ut",
                "passed": False,
                "output": "AssertionError: expected 1 got 2",
            },
        ],
        "errors": "",
    })

    assert "结论: blocking failure" in status
    assert (
        "- st: FAIL | environment failure: missing token"
        in status
    )
    assert (
        "- ut: FAIL | AssertionError: expected 1 got 2"
        in status
    )

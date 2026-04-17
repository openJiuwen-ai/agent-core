# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Implement-stage helper tests."""

from __future__ import annotations

from unittest import IsolatedAsyncioTestCase

from openjiuwen.auto_harness.schema import (
    AutoHarnessConfig,
    OptimizationTask,
)
from openjiuwen.auto_harness.stages.verify import (
    _iter_ci_gate_messages,
    _start_fix_loop,
)
from openjiuwen.core.session.stream.base import (
    OutputSchema,
)


def _msg(text: str) -> OutputSchema:
    return OutputSchema(
        type="message",
        index=0,
        payload={"content": text},
    )


class _FakeFixLoop:
    async def run(
        self,
        ci_runner,
        agent_fixer,
        evaluator=None,
    ):
        del evaluator
        await ci_runner()
        await agent_fixer("E501 line too long")

        class _Result:
            success = False
            error_log = ["Phase 1 failed"]

        return _Result()


class _PassThroughFixLoop:
    async def run(
        self,
        ci_runner,
        agent_fixer,
        evaluator=None,
    ):
        del evaluator
        ci_result = await ci_runner()
        await agent_fixer(ci_result.errors)

        class _Result:
            success = False
            error_log = ["Phase 1 failed"]

        return _Result()


class _FakeCIGate:
    async def run(self, action="all"):
        del action
        return {
            "passed": False,
            "gates": [{
                "name": "lint",
                "passed": False,
                "output": "E501 line too long",
            }],
            "errors": "[lint]\nE501 line too long",
        }


class _WarningOnlyDetailCIGate:
    async def run(self, action="all"):
        del action
        return {
            "passed": False,
            "gates": [{
                "name": "test",
                "passed": False,
                "output": (
                    "=================================== FAILURES ===================================\n"
                    "E   AssertionError: expected value\n"
                    "\n"
                    "=========================== short test summary info ============================\n"
                    "FAILED tests/unit_tests/core/foundation/tool/test_api_param_mapper.py::test_x"
                ),
            }],
            "errors": (
                "[test]\n"
                "=================================== FAILURES ===================================\n"
                "E   AssertionError: expected value\n"
                "\n"
                "=========================== short test summary info ============================\n"
                "FAILED tests/unit_tests/core/foundation/tool/test_api_param_mapper.py::test_x"
            ),
        }


class TestImplementStageHelpers(
    IsolatedAsyncioTestCase,
):
    def test_iter_ci_gate_messages_contains_summary_and_excerpt(
        self,
    ):
        messages = _iter_ci_gate_messages({
            "passed": False,
            "gates": [
                {
                    "name": "lint",
                    "passed": False,
                    "output": "E501 line too long",
                },
                {
                    "name": "test",
                    "passed": True,
                    "output": "ok",
                },
            ],
            "errors": "",
        })
        assert messages[0] == "CI 结果: lint=FAIL, test=PASS"
        assert messages[1] == "[lint] E501 line too long"

    async def test_start_fix_loop_emits_progress_messages(
        self,
    ):
        task, queue, done = _start_fix_loop(
            config=AutoHarnessConfig(),
            task=OptimizationTask(topic="fix lint"),
            agent=None,
            git=object(),
            ci_gate=_FakeCIGate(),
            fix_loop_ctrl=_FakeFixLoop(),
            msg_factory=_msg,
        )

        items = []
        while not done.is_set() or not queue.empty():
            items.append(await queue.get())

        ok, result = await task
        assert ok is False
        assert result.error_log == ["Phase 1 failed"]

        texts = [
            item.payload["content"]
            for item in items
        ]
        assert "[修复循环] 第 1 次重跑 CI" in texts
        assert "[修复循环] CI 结果: lint=FAIL" in texts
        assert "[修复循环] 第 1 次修复" in texts
        assert "[修复循环] 修复目标:\nE501 line too long" in texts
        assert "[修复循环] 修复耗尽" in texts

    async def test_start_fix_loop_omits_warning_summary_in_fix_target(
        self,
    ):
        task, queue, done = _start_fix_loop(
            config=AutoHarnessConfig(),
            task=OptimizationTask(topic="fix pytest failure"),
            agent=None,
            git=object(),
            ci_gate=_WarningOnlyDetailCIGate(),
            fix_loop_ctrl=_PassThroughFixLoop(),
            msg_factory=_msg,
        )

        items = []
        while not done.is_set() or not queue.empty():
            items.append(await queue.get())

        await task
        texts = [
            item.payload["content"]
            for item in items
        ]
        joined = "\n".join(texts)
        assert "AssertionError: expected value" in joined
        assert "PydanticDeprecatedSince20" not in joined

# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""test_fix_loop — FixLoopController 单元测试。"""

from __future__ import annotations

import asyncio
from unittest import IsolatedAsyncioTestCase

from openjiuwen.auto_harness.infra.fix_loop import (
    FixLoopController,
    FixLoopResult,
)


class _CIStub:
    def __init__(self, passed: bool, errors: str = ""):
        self.passed = passed
        self.errors = errors


class _ReviewStub:
    def __init__(self, approved: bool):
        self.approved = approved


class TestFixLoopResult(IsolatedAsyncioTestCase):
    def test_defaults(self):
        r = FixLoopResult()
        assert r.success is False
        assert r.attempts == 0
        assert r.phase == 1
        assert r.error_log == []


class TestFixLoopPhase1(IsolatedAsyncioTestCase):
    async def test_pass_first_attempt(self):
        ctrl = FixLoopController(phase1_max_retries=3)

        async def ci():
            return _CIStub(passed=True)

        async def fixer(_errors: str):
            pass

        result = await ctrl.run(ci, fixer)
        assert result.success is True
        assert result.attempts == 1
        assert result.phase == 1

    async def test_pass_after_retries(self):
        call_count = 0

        async def ci():
            nonlocal call_count
            call_count += 1
            return _CIStub(
                passed=(call_count >= 3),
                errors="lint error",
            )

        async def fixer(_errors: str):
            pass

        ctrl = FixLoopController(phase1_max_retries=5)
        result = await ctrl.run(ci, fixer)
        assert result.success is True
        assert result.attempts == 3

    async def test_exhaust_retries(self):
        async def ci():
            return _CIStub(passed=False, errors="fail")

        async def fixer(_errors: str):
            pass

        ctrl = FixLoopController(
            phase1_max_retries=2, phase2_max_retries=0,
        )
        result = await ctrl.run(ci, fixer)
        assert result.success is False
        assert result.attempts == 2
        assert len(result.error_log) == 2

    async def test_ci_timeout(self):
        async def ci():
            await asyncio.sleep(10)
            return _CIStub(passed=True)

        async def fixer(_errors: str):
            pass

        ctrl = FixLoopController(
            phase1_max_retries=1,
            timeout_per_attempt=0.01,
        )
        result = await ctrl.run(ci, fixer)
        assert result.success is False
        assert "timeout" in result.error_log[0].lower()


class TestFixLoopPhase2(IsolatedAsyncioTestCase):
    async def test_evaluator_approves(self):
        async def ci():
            return _CIStub(passed=False, errors="err")

        async def fixer(_errors: str):
            pass

        call_count = 0

        async def evaluator():
            nonlocal call_count
            call_count += 1
            return _ReviewStub(approved=(call_count >= 2))

        ctrl = FixLoopController(
            phase1_max_retries=1,
            phase2_max_retries=3,
        )
        result = await ctrl.run(ci, fixer, evaluator)
        assert result.success is True
        assert result.phase == 2

    async def test_no_evaluator_skips_phase2(self):
        async def ci():
            return _CIStub(passed=False, errors="err")

        async def fixer(_errors: str):
            pass

        ctrl = FixLoopController(
            phase1_max_retries=1,
        )
        result = await ctrl.run(ci, fixer, evaluator=None)
        assert result.success is False
        assert result.phase == 1

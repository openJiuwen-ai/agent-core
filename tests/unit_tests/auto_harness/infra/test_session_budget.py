# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""test_session_budget — SessionBudgetController 单元测试。"""

from __future__ import annotations

import pytest

from openjiuwen.auto_harness.infra.session_budget import (
    SessionBudgetController,
)


class TestSessionBudgetController:
    def test_initial_state(self):
        ctrl = SessionBudgetController()
        assert ctrl.elapsed_secs == 0.0
        assert ctrl.should_stop is False

    def test_remaining_before_start(self):
        ctrl = SessionBudgetController(wall_clock_secs=100.0)
        assert ctrl.remaining_secs == 100.0

    def test_start_records_time(self):
        ctrl = SessionBudgetController(wall_clock_secs=100.0)
        ctrl.start()
        assert ctrl.elapsed_secs >= 0.0
        assert ctrl.remaining_secs <= 100.0

    def test_wall_clock_exceeded(self):
        ctrl = SessionBudgetController(wall_clock_secs=10.0)
        ctrl.start()
        # Simulate time passing by backdating _start
        import time
        ctrl._start = time.monotonic() - 11.0
        assert ctrl.should_stop is True

    def test_cost_exceeded(self):
        ctrl = SessionBudgetController(cost_limit_usd=1.0)
        ctrl.start()
        ctrl.add_cost(1.5)
        assert ctrl.should_stop is True

    def test_cost_not_exceeded(self):
        ctrl = SessionBudgetController(cost_limit_usd=10.0)
        ctrl.start()
        ctrl.add_cost(0.5)
        assert ctrl.should_stop is False

    def test_remaining_cost(self):
        ctrl = SessionBudgetController(cost_limit_usd=5.0)
        ctrl.add_cost(2.0)
        assert ctrl.remaining_cost_usd == pytest.approx(3.0)

    def test_remaining_cost_floor(self):
        ctrl = SessionBudgetController(cost_limit_usd=1.0)
        ctrl.add_cost(5.0)
        assert ctrl.remaining_cost_usd == 0.0

    def test_check_task_budget_sufficient(self):
        ctrl = SessionBudgetController(
            wall_clock_secs=3600.0,
            task_timeout_secs=600.0,
        )
        ctrl.start()
        assert ctrl.check_task_budget() is True

    def test_check_task_budget_insufficient(self):
        ctrl = SessionBudgetController(
            wall_clock_secs=100.0,
            task_timeout_secs=600.0,
        )
        ctrl.start()
        ctrl._start = ctrl._start - 90.0  # type: ignore[operator]
        assert ctrl.check_task_budget() is False

    def test_check_task_budget_custom_timeout(self):
        ctrl = SessionBudgetController(
            wall_clock_secs=3600.0,
            task_timeout_secs=600.0,
        )
        ctrl.start()
        assert ctrl.check_task_budget(
            task_timeout_secs=10.0
        ) is True

# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tests for edit budget scheduler."""

import math

import pytest

from openjiuwen.agent_evolving.optimizer.skill_document.scheduler import (
    build_scheduler,
)


class TestConstantScheduler:
    @staticmethod
    def test_returns_same_budget():
        sched = build_scheduler("constant", max_lr=5)
        assert sched.step() == 5
        assert sched.step() == 5
        assert sched.step() == 5

    @staticmethod
    def test_default_max_lr():
        sched = build_scheduler("constant")
        assert sched.step() == 8


class TestLinearScheduler:
    @staticmethod
    def test_linear_decay():
        sched = build_scheduler("linear", max_lr=10, min_lr=2, total_steps=4)
        budgets = [sched.step() for _ in range(4)]
        # step 1: t=0.25, lr = 10 + (2-10)*0.25 = 8
        assert budgets[0] == 8
        # step 4: t=1.0, lr = 10 + (2-10)*1.0 = 2
        assert budgets[-1] == 2
        # Should be monotonically non-increasing
        for i in range(len(budgets) - 1):
            assert budgets[i] >= budgets[i + 1]

    @staticmethod
    def test_single_step():
        sched = build_scheduler("linear", max_lr=10, min_lr=2, total_steps=1)
        assert sched.step() == 10

    @staticmethod
    def test_get_lr():
        sched = build_scheduler("linear", max_lr=10, min_lr=2, total_steps=4)
        # step 1: t=0.25, lr = 10 + (2-10)*0.25 = 8
        assert sched.get_lr(1) == 8
        # step 4: t=1.0, lr = 10 + (2-10)*1.0 = 2
        assert sched.get_lr(4) == 2


class TestCosineScheduler:
    @staticmethod
    def test_cosine_decay():
        sched = build_scheduler("cosine", max_lr=10, min_lr=2, total_steps=8)
        budgets = [sched.step() for _ in range(8)]
        assert budgets[0] == 10
        assert budgets[-1] <= 3
        # Cosine should be smoother than linear (middle values > min)
        assert budgets[4] > 2

    @staticmethod
    def test_single_step():
        sched = build_scheduler("cosine", max_lr=10, min_lr=2, total_steps=1)
        assert sched.step() == 10


class TestAutonomousScheduler:
    @staticmethod
    def test_raises_not_implemented():
        with pytest.raises(NotImplementedError, match="autonomous"):
            build_scheduler("autonomous")


class TestUnknownMode:
    @staticmethod
    def test_raises_value_error():
        with pytest.raises(ValueError, match="Unknown scheduler mode"):
            build_scheduler("nonexistent")


class TestStateDict:
    @staticmethod
    def test_round_trip():
        sched = build_scheduler("constant", max_lr=5)
        sched.step()
        sched.step()
        state = sched.state_dict()
        assert state["current_step"] == 2

        sched2 = build_scheduler("constant", max_lr=5)
        sched2.load_state_dict(state)
        assert sched2._current_step == 2

    @staticmethod
    def test_linear_resume():
        sched = build_scheduler("linear", max_lr=10, min_lr=2, total_steps=8)
        for _ in range(4):
            sched.step()
        state = sched.state_dict()

        sched2 = build_scheduler("linear", max_lr=10, min_lr=2, total_steps=8)
        sched2.load_state_dict(state)
        assert sched2.step() == sched.step()

    @staticmethod
    def test_load_empty_state():
        sched = build_scheduler("constant")
        sched.load_state_dict({})
        assert sched._current_step == 0

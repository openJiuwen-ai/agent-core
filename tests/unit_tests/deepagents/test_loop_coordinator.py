# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Unit tests for LoopCoordinator."""
from __future__ import annotations

from openjiuwen.deepagents.loop_coordinator import (
    LoopCoordinator,
)
from openjiuwen.deepagents.schema.stop_condition import (
    StopCondition,
)


def test_defaults() -> None:
    """Fresh coordinator starts at iteration 0."""
    coord = LoopCoordinator()
    assert coord.current_iteration == 0
    assert coord.is_aborted is False
    assert coord.should_continue() is True


def test_increment_iteration() -> None:
    """increment_iteration advances counter."""
    coord = LoopCoordinator()
    coord.reset()
    coord.increment_iteration()
    coord.increment_iteration()
    assert coord.current_iteration == 2


def test_max_iterations_stop() -> None:
    """Loop stops when max_iterations reached."""
    coord = LoopCoordinator(
        StopCondition(max_iterations=2)
    )
    coord.reset()
    assert coord.should_continue() is True
    coord.increment_iteration()
    assert coord.should_continue() is True
    coord.increment_iteration()
    assert coord.should_continue() is False


def test_max_token_usage_stop() -> None:
    """Loop stops when token budget exhausted."""
    coord = LoopCoordinator(
        StopCondition(max_token_usage=100)
    )
    coord.reset()
    coord.add_token_usage(50)
    assert coord.should_continue() is True
    coord.add_token_usage(50)
    assert coord.should_continue() is False


def test_abort_stops_immediately() -> None:
    """request_abort causes should_continue=False."""
    coord = LoopCoordinator()
    coord.reset()
    coord.request_abort()
    assert coord.is_aborted is True
    assert coord.should_continue() is False


def test_timeout_stop() -> None:
    """Loop stops when timeout exceeded."""
    coord = LoopCoordinator(
        StopCondition(timeout_seconds=0.0)
    )
    coord.reset()
    assert coord.should_continue() is False


def test_custom_predicate_stop() -> None:
    """Custom predicate returning True stops loop."""
    coord = LoopCoordinator(
        StopCondition(custom=lambda _ctx: True)
    )
    coord.reset()
    assert coord.should_continue() is False


def test_custom_predicate_continue() -> None:
    """Custom predicate returning False allows loop."""
    coord = LoopCoordinator(
        StopCondition(custom=lambda _ctx: False)
    )
    coord.reset()
    assert coord.should_continue() is True


def test_reset_clears_state() -> None:
    """reset() clears iteration, tokens, abort."""
    coord = LoopCoordinator(
        StopCondition(max_iterations=10)
    )
    coord.reset()
    coord.increment_iteration()
    coord.add_token_usage(999)
    coord.request_abort()

    coord.reset()
    assert coord.current_iteration == 0
    assert coord.is_aborted is False
    assert coord.should_continue() is True


def test_negative_tokens_ignored() -> None:
    """add_token_usage ignores negative values."""
    coord = LoopCoordinator(
        StopCondition(max_token_usage=100)
    )
    coord.reset()
    coord.add_token_usage(-50)
    coord.add_token_usage(0)
    assert coord.should_continue() is True

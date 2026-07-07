#!/usr/bin/env python
# coding: utf-8
"""Tests for the step_executor model-name registry."""

from __future__ import annotations

import pytest

from openjiuwen.harness.tools.robotic_arm.registry import SubTaskExecutorRegistry


@pytest.fixture(autouse=True)
def _cleanup_registry():
    before = dict(SubTaskExecutorRegistry._registry)
    yield
    SubTaskExecutorRegistry._registry = before


def test_register_and_create() -> None:
    @SubTaskExecutorRegistry.register("test-rig")
    class FakeExecutor:
        def __init__(self, arm_ip: str) -> None:
            self.arm_ip = arm_ip

    executor = SubTaskExecutorRegistry.create("test-rig", arm_ip="10.0.0.1")

    assert isinstance(executor, FakeExecutor)
    assert executor.arm_ip == "10.0.0.1"


def test_unknown_model_raises() -> None:
    with pytest.raises(ValueError, match="Unknown step_executor model"):
        SubTaskExecutorRegistry.create("does-not-exist")

#!/usr/bin/env python
# coding: utf-8
"""Tests for build_robotic_arm_rails: step_executor_model resolution and rail assembly."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from openjiuwen.harness.tools.robotic_arm.config import RoboticArmRuntimeSettings
from openjiuwen.harness.tools.robotic_arm.rails.context_summarizer_rail import ContextSummarizerRail
from openjiuwen.harness.tools.robotic_arm.rails.step_executor_rail import StepExecutorRail
from openjiuwen.harness.tools.robotic_arm.rails.vision_perception_rail import VisionPerceptionRail
from openjiuwen.harness.tools.robotic_arm.rails_factory import build_robotic_arm_rails
from openjiuwen.harness.tools.robotic_arm.registry import SubTaskExecutorRegistry


@pytest.fixture(autouse=True)
def _cleanup_registry():
    before = dict(SubTaskExecutorRegistry._registry)
    yield
    SubTaskExecutorRegistry._registry = before


def test_direct_step_executor_is_untouched() -> None:
    executor = MagicMock()
    settings = RoboticArmRuntimeSettings(step_executor=executor, health_check=False)

    build_robotic_arm_rails(settings, model=None)

    assert settings.step_executor is executor


def test_step_executor_model_resolves_and_backfills_settings() -> None:
    @SubTaskExecutorRegistry.register("unit-test-rig")
    class FakeExecutor:
        def __init__(self, arm_ip: str) -> None:
            self.arm_ip = arm_ip

        def capture(self):
            return "frame"

    settings = RoboticArmRuntimeSettings(
        step_executor_model="unit-test-rig", step_executor_params={"arm_ip": "10.0.0.5"}, health_check=False
    )

    build_robotic_arm_rails(settings, model=None)

    assert isinstance(settings.step_executor, FakeExecutor)
    assert settings.step_executor.arm_ip == "10.0.0.5"


def test_returns_all_three_rails() -> None:
    settings = RoboticArmRuntimeSettings(step_executor=MagicMock(), health_check=False)

    rails = build_robotic_arm_rails(settings, model=None)

    assert any(isinstance(r, StepExecutorRail) for r in rails)
    assert any(isinstance(r, VisionPerceptionRail) for r in rails)
    assert any(isinstance(r, ContextSummarizerRail) for r in rails)


def test_missing_step_executor_and_model_raises_when_building_rails() -> None:
    settings = RoboticArmRuntimeSettings()

    with pytest.raises(ValueError, match="step_executor or step_executor_model"):
        build_robotic_arm_rails(settings, model=None)

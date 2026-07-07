#!/usr/bin/env python
# coding: utf-8
"""Tests for create_robotic_arm_agent factory wiring."""

from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import MagicMock

from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.harness.schema.config import SubAgentConfig
from openjiuwen.harness.subagents.robotic_arm_agent import (
    DEFAULT_ROBOTIC_ARM_DESCRIPTION,
    build_robotic_arm_agent_config,
    create_robotic_arm_agent,
)
from openjiuwen.harness.tools.robotic_arm.config import RoboticArmRuntimeSettings
from openjiuwen.harness.tools.robotic_arm.rails.context_summarizer_rail import ContextSummarizerRail
from openjiuwen.harness.tools.robotic_arm.rails.step_executor_rail import StepExecutorRail
from openjiuwen.harness.tools.robotic_arm.rails.vision_perception_rail import VisionPerceptionRail


def _fake_model() -> MagicMock:
    return MagicMock(spec=Model)


def _fake_settings() -> RoboticArmRuntimeSettings:
    return RoboticArmRuntimeSettings(step_executor=MagicMock())


def _capture_create_deep_agent():
    calls: list[dict] = []

    def fake(**kwargs):
        agent = MagicMock()
        agent.card = kwargs.get("card")
        calls.append(kwargs)
        return agent

    return calls, fake


def _patch_create_deep_agent(fake_create):
    stack = ExitStack()
    from unittest.mock import patch

    stack.enter_context(
        patch(
            "openjiuwen.harness.subagents.robotic_arm_agent.create_deep_agent",
            side_effect=fake_create,
        )
    )
    return stack


def test_default_wiring_creates_one_agent() -> None:
    calls, fake = _capture_create_deep_agent()
    with _patch_create_deep_agent(fake):
        create_robotic_arm_agent(_fake_model(), settings=_fake_settings())

    assert len(calls) == 1


def test_default_wiring_main_agent_card_is_robotic_arm_agent() -> None:
    calls, fake = _capture_create_deep_agent()
    with _patch_create_deep_agent(fake):
        create_robotic_arm_agent(_fake_model(), settings=_fake_settings())

    assert calls[0]["card"].name == "robotic_arm_agent"


def test_default_wiring_registers_only_report_plan_tool() -> None:
    calls, fake = _capture_create_deep_agent()
    with _patch_create_deep_agent(fake):
        create_robotic_arm_agent(_fake_model(), settings=_fake_settings())

    tool_names = {tool.card.name for tool in calls[0]["tools"]}
    assert tool_names == {"report_plan"}


def test_default_wiring_registers_perception_execution_and_summarizer_rails() -> None:
    calls, fake = _capture_create_deep_agent()
    with _patch_create_deep_agent(fake):
        create_robotic_arm_agent(_fake_model(), settings=_fake_settings())

    rails = calls[0]["rails"]
    assert any(isinstance(rail, StepExecutorRail) for rail in rails)
    assert any(isinstance(rail, VisionPerceptionRail) for rail in rails)
    assert any(isinstance(rail, ContextSummarizerRail) for rail in rails)


def test_user_tools_are_merged_with_report_plan_tool() -> None:
    user_tool = MagicMock()
    calls, fake = _capture_create_deep_agent()
    with _patch_create_deep_agent(fake):
        create_robotic_arm_agent(_fake_model(), tools=[user_tool], settings=_fake_settings())

    assert user_tool in calls[0]["tools"]


def test_custom_subagents_are_forwarded() -> None:
    custom = MagicMock()
    calls, fake = _capture_create_deep_agent()
    with _patch_create_deep_agent(fake):
        create_robotic_arm_agent(_fake_model(), subagents=[custom], settings=_fake_settings())

    assert calls[0]["subagents"] == [custom]


def test_missing_step_executor_raises_before_agent_creation() -> None:
    settings = _fake_settings()
    settings.step_executor = None
    calls, fake = _capture_create_deep_agent()
    try:
        with _patch_create_deep_agent(fake):
            create_robotic_arm_agent(_fake_model(), settings=settings)
        raised = False
    except ValueError:
        raised = True

    assert raised
    assert not calls


def test_build_robotic_arm_agent_config_uses_factory_name() -> None:
    settings = _fake_settings()
    spec = build_robotic_arm_agent_config(_fake_model(), settings=settings, language="en")

    assert isinstance(spec, SubAgentConfig)
    assert spec.agent_card.name == "robotic_arm_agent"
    assert spec.agent_card.description == DEFAULT_ROBOTIC_ARM_DESCRIPTION["en"]
    assert spec.factory_name == "robotic_arm_agent"
    assert spec.factory_kwargs["settings"] is settings

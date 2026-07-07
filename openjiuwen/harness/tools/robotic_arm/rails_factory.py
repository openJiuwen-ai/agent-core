# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from typing import List

from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.single_agent.rail.base import AgentRail
from openjiuwen.harness.tools.robotic_arm.config import RoboticArmRuntimeSettings
from openjiuwen.harness.tools.robotic_arm.rails.context_summarizer_rail import ContextSummarizerRail
from openjiuwen.harness.tools.robotic_arm.rails.step_executor_rail import StepExecutorRail
from openjiuwen.harness.tools.robotic_arm.rails.vision_perception_rail import VisionPerceptionRail
from openjiuwen.harness.tools.robotic_arm.registry import SubTaskExecutorRegistry


def infer_model_display_name(model: Model | None) -> str:
    if model is None:
        return ""
    mc = getattr(model, "model_config", None)
    if mc is None:
        return ""
    name = getattr(mc, "model", None) or getattr(mc, "model_name", None)
    return str(name or "")


def _resolve_step_executor(settings: RoboticArmRuntimeSettings) -> None:
    """Resolve ``step_executor_model`` to an instance once, ahead of any rail/invoke.

    Doing this here (rather than inside a rail's ``before_invoke``) means every
    rail just reads ``settings.step_executor`` directly in its own ``__init__`` --
    no cross-rail handoff through ``ctx.extra`` is needed.
    """
    if settings.step_executor is None and settings.step_executor_model is not None:
        settings.step_executor = SubTaskExecutorRegistry.create(
            settings.step_executor_model, **settings.step_executor_params
        )


def build_robotic_arm_rails(settings: RoboticArmRuntimeSettings, *, model: Model | None) -> List[AgentRail]:
    _resolve_step_executor(settings)
    model_name = infer_model_display_name(model)
    return [
        StepExecutorRail(settings),
        VisionPerceptionRail(settings, model_name=model_name),
        ContextSummarizerRail(settings.mcs_screenshots_to_keep),
    ]


__all__ = ["build_robotic_arm_rails", "infer_model_display_name"]

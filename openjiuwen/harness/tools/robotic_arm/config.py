# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Runtime settings for the robotic-arm (VLM 2D grounding + auto execution) subagent.

A single caller-supplied object, ``step_executor`` (``SubTaskExecutor``), owns both
the camera and the arm/algorithm pipeline -- they are typically coupled anyway,
since camera extrinsics are calibrated against the arm's base frame. It captures
photos for the model to see, and -- once per turn, right after the model calls
``report_plan`` -- turns the model's raw normalized 2D point into the physical
arm/gripper action for whichever sub-task is ``in_progress``. Pixel conversion,
depth estimation, coordinate transform, and trajectory computation are entirely
this object's own responsibility, not the framework's. ``StepExecutorRail``
drives both halves automatically; the model never calls a movement tool directly.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, runtime_checkable


def _get_int(name: str, default: int) -> int:
    v = os.getenv(name)
    if v is None or v.strip() == "":
        return default
    return int(v)


@runtime_checkable
class SubTaskExecutor(Protocol):
    """User-supplied pipeline: captures photos and executes sub-tasks.

    ``capture()`` is called before every model turn (see ``VisionPerceptionRail``).
    ``execute()`` is invoked automatically by ``StepExecutorRail`` right after the
    model calls ``report_plan``, for whichever sub-task is currently ``in_progress``.
    Pixel conversion, depth estimation, coordinate transform, trajectory
    computation, and the actual arm/gripper movement are entirely up to this
    implementation -- the framework hands over the model's raw output as-is.
    """

    def capture(self) -> Any:
        """Return the latest RGB frame (e.g. a ``PIL.Image``)."""
        ...

    def execute(self, frame: Any, sub_task: dict) -> str:
        """Run ``sub_task`` against ``frame`` and return a human-readable status string.

        ``sub_task`` is the current ``in_progress`` item exactly as the model
        submitted it via ``report_plan``: ``id``/``description``/``status`` plus
        ``start_x``/``start_y``/``end_x``/``end_y`` -- normalized coordinates in
        ``[0, vlm_coordinate_scale]`` on ``frame``, present only if that sub-task
        has a point. Converting them to real pixels (or anything else) is up to
        this implementation.
        """
        ...


@dataclass
class RoboticArmRuntimeSettings:
    """The per-step execution pipeline (camera + CV + trajectory + arm, all in one).

    ``step_executor`` is hardware/algorithm-specific and must be supplied by the
    caller; there is no default local connection the way ``MobileGuiRuntimeSettings``
    has for ``uiautomator2``.

    As an alternative to passing an already-constructed object directly, set
    ``step_executor_model`` to a name registered via :class:`SubTaskExecutorRegistry`
    (see ``registry.py``) plus the constructor kwargs in ``step_executor_params``.
    This lets a new rig be added by registering a class elsewhere (e.g. in the
    caller's own module) without touching this package's code -- selecting it is
    then just a name + a params dict.
    """

    step_executor: Optional[SubTaskExecutor] = None
    step_executor_model: Optional[str] = None
    step_executor_params: dict = field(default_factory=dict)

    health_check: bool = True

    vlm_grounding_max_width: int = field(default_factory=lambda: _get_int("ARM_VLM_MAX_WIDTH", 1280))
    vlm_grounding_jpeg_quality: int = field(default_factory=lambda: _get_int("ARM_VLM_JPEG_QUALITY", 85))
    vlm_coordinate_scale: int = field(default_factory=lambda: _get_int("ARM_VLM_COORDINATE_SCALE", 1000))

    mcs_screenshots_to_keep: int = field(default_factory=lambda: _get_int("ARM_MCS_SCREENSHOTS_TO_KEEP", 3))

    context_max_message_num: int = field(default_factory=lambda: _get_int("ARM_CONTEXT_MAX_MESSAGES", 120))
    context_default_window_round_num: int = field(default_factory=lambda: _get_int("ARM_CONTEXT_WINDOW_ROUNDS", 20))

    @classmethod
    def from_env(cls) -> "RoboticArmRuntimeSettings":
        return cls()


__all__ = [
    "RoboticArmRuntimeSettings",
    "SubTaskExecutor",
]

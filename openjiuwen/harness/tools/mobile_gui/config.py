# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Runtime settings for the mobile GUI (VLM grounding) subagent."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

SkillConsultMode = Literal["branch", "inline"]


def _get_str(name: str, default: str) -> str:
    v = os.getenv(name)
    return default if v is None or v.strip() == "" else v


def _get_int(name: str, default: int) -> int:
    v = os.getenv(name)
    if v is None or v.strip() == "":
        return default
    return int(v)


def _get_env_str(primary: str, legacy: str, default: str) -> str:
    """Read env var, falling back to legacy name when primary is unset."""
    primary_val = os.getenv(primary)
    if primary_val is not None and primary_val.strip() != "":
        return primary_val
    return _get_str(legacy, default)


def _get_env_int(primary: str, legacy: str, default: int) -> int:
    primary_val = os.getenv(primary)
    if primary_val is not None and primary_val.strip() != "":
        return int(primary_val)
    return _get_int(legacy, default)


def _get_float(name: str, default: float) -> float:
    v = os.getenv(name)
    if v is None or v.strip() == "":
        return default
    return float(v)


@dataclass
class MobileGuiRuntimeSettings:
    """Defaults aligned with mobile-agent ``vlm_grounding`` configuration."""

    device_serial: str = field(default_factory=lambda: _get_str("DEVICE_SERIAL", "emulator-5554"))
    device: Optional[Any] = None
    cleanup_go_home: bool = True
    health_check: bool = True

    vlm_grounding_max_width: int = field(
        default_factory=lambda: _get_int("VLM_GROUNDING_MAX_WIDTH", 1280)
    )
    vlm_grounding_jpeg_quality: int = field(
        default_factory=lambda: _get_int("VLM_GROUNDING_JPEG_QUALITY", 85)
    )
    vlm_grounding_ui_settle_seconds: float = field(
        default_factory=lambda: _get_float("VLM_GROUNDING_UI_SETTLE_SECONDS", 1.0)
    )
    vlm_coordinate_scale: int = field(
        default_factory=lambda: _get_int("VLM_COORDINATE_SCALE", 1000)
    )
    vlm_claude_image_width: int = field(
        default_factory=lambda: _get_int("VLM_CLAUDE_IMAGE_WIDTH", 1280)
    )
    vlm_claude_image_height: int = field(
        default_factory=lambda: _get_int("VLM_CLAUDE_IMAGE_HEIGHT", 720)
    )
    vlm_claude_opus_max_dimension: int = field(
        default_factory=lambda: _get_int("VLM_CLAUDE_OPUS_MAX_DIMENSION", 1280)
    )

    mcs_screenshots_to_keep: int = field(
        default_factory=lambda: _get_int("MCS_SCREENSHOTS_TO_KEEP", 3)
    )

    scroll_default_width: int = field(
        default_factory=lambda: _get_int("SCROLL_DEFAULT_WIDTH", 1080)
    )
    scroll_default_height: int = field(
        default_factory=lambda: _get_int("SCROLL_DEFAULT_HEIGHT", 1920)
    )
    scroll_duration_ms_default: int = field(
        default_factory=lambda: _get_int("SCROLL_DURATION_MS_DEFAULT", 300)
    )

    wait_gui_load_min_seconds: float = field(
        default_factory=lambda: _get_float("WAIT_GUI_LOAD_MIN_SECONDS", 0.5)
    )
    wait_gui_load_max_seconds: float = field(
        default_factory=lambda: _get_float("WAIT_GUI_LOAD_MAX_SECONDS", 30.0)
    )
    wait_gui_load_default_seconds: float = field(
        default_factory=lambda: _get_float("WAIT_GUI_LOAD_DEFAULT_SECONDS", 2.0)
    )

    vlm_grounding_only_settle_after_tools: bool = True

    context_max_message_num: int = field(
        default_factory=lambda: _get_int("MOBILE_CONTEXT_MAX_MESSAGES", 120)
    )
    context_default_window_round_num: int = field(
        default_factory=lambda: _get_int("MOBILE_CONTEXT_WINDOW_ROUNDS", 20)
    )

    skill_consult_mode: SkillConsultMode = field(
        default_factory=lambda: _normalize_skill_consult_mode(
            _get_env_str("MULTIMODAL_SKILL_CONSULT_MODE", "MOBILE_SKILL_CONSULT_MODE", "branch")
        )
    )
    skill_branch_max_images: int = field(
        default_factory=lambda: _get_env_int(
            "MULTIMODAL_SKILL_BRANCH_MAX_IMAGES", "MOBILE_SKILL_BRANCH_MAX_IMAGES", 4
        )
    )
    skill_branch_max_consults_per_skill: int = field(
        default_factory=lambda: _get_env_int(
            "MULTIMODAL_SKILL_BRANCH_MAX_CONSULTS_PER_SKILL",
            "MOBILE_SKILL_BRANCH_MAX_CONSULTS_PER_SKILL",
            2,
        )
    )
    skill_branch_previous_steps_turns: int = field(
        default_factory=lambda: _get_env_int(
            "MULTIMODAL_SKILL_BRANCH_PREVIOUS_STEPS_TURNS",
            "MOBILE_SKILL_BRANCH_PREVIOUS_STEPS_TURNS",
            10,
        )
    )

    @classmethod
    def from_env(cls) -> "MobileGuiRuntimeSettings":
        return cls()


def _normalize_skill_consult_mode(raw: str) -> SkillConsultMode:
    normalized = (raw or "branch").strip().lower()
    if normalized == "inline":
        return "inline"
    return "branch"

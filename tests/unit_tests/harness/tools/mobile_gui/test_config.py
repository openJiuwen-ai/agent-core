# coding: utf-8
"""Tests for mobile_gui config and VLM grounding prompts."""

from __future__ import annotations

import pytest

from openjiuwen.harness.tools.mobile_gui.config import (
    MobileGuiRuntimeSettings,
    _normalize_skill_consult_mode,
)
from openjiuwen.harness.tools.mobile_gui.vlm_grounding_prompt import (
    build_vlm_grounding_system_prompt,
)


def test_normalize_skill_consult_mode_accepts_inline_and_branch_aliases():
    """Known modes pass through (case-insensitive for branch); unknown values default to branch."""
    assert _normalize_skill_consult_mode("inline") == "inline"
    assert _normalize_skill_consult_mode("INLINE") == "inline"
    assert _normalize_skill_consult_mode("BRANCH") == "branch"
    assert _normalize_skill_consult_mode("branch") == "branch"
    assert _normalize_skill_consult_mode("unknown") == "branch"
    assert _normalize_skill_consult_mode("") == "branch"


def test_vlm_prompt_branch_vs_inline_skill_guidance():
    """Branch mode steers away from read_file on skill screenshots; inline allows read_file."""
    branch_settings = MobileGuiRuntimeSettings(skill_consult_mode="branch")
    inline_settings = MobileGuiRuntimeSettings(skill_consult_mode="inline")

    branch_prompt = build_vlm_grounding_system_prompt(branch_settings)
    inline_prompt = build_vlm_grounding_system_prompt(inline_settings)

    assert "planner memo" in branch_prompt
    assert "Do **not** call `read_file`" in branch_prompt
    assert "read_file" in inline_prompt
    assert "Do **not** call `read_file`" not in inline_prompt
    assert "screenshot" in branch_prompt.lower()
    assert "screenshot" in inline_prompt.lower()


@pytest.mark.parametrize(
    "primary_env,legacy_env,expected",
    [
        ("MULTIMODAL_SKILL_CONSULT_MODE", "MOBILE_SKILL_CONSULT_MODE", "inline"),
        ("MULTIMODAL_SKILL_CONSULT_MODE", "MOBILE_SKILL_CONSULT_MODE", "branch"),
    ],
    ids=["multimodal_inline", "legacy_mobile_branch"],
)
def test_skill_consult_mode_env_precedence(monkeypatch, primary_env, legacy_env, expected):
    """MULTIMODAL_* wins when set; MOBILE_* is used only as fallback."""
    monkeypatch.delenv("MULTIMODAL_SKILL_CONSULT_MODE", raising=False)
    monkeypatch.delenv("MOBILE_SKILL_CONSULT_MODE", raising=False)

    if expected == "inline":
        monkeypatch.setenv(primary_env, "inline")
    else:
        monkeypatch.delenv(primary_env, raising=False)
        monkeypatch.setenv(legacy_env, "branch")

    settings = MobileGuiRuntimeSettings.from_env()
    assert settings.skill_consult_mode == expected


def test_skill_branch_limits_from_multimodal_env(monkeypatch):
    """Branch tuning knobs load from MULTIMODAL_SKILL_BRANCH_* env vars."""
    for key in (
        "MOBILE_SKILL_BRANCH_MAX_IMAGES",
        "MOBILE_SKILL_BRANCH_MAX_CONSULTS_PER_SKILL",
        "MOBILE_SKILL_BRANCH_PREVIOUS_STEPS_TURNS",
        "MULTIMODAL_SKILL_BRANCH_MAX_IMAGES",
        "MULTIMODAL_SKILL_BRANCH_MAX_CONSULTS_PER_SKILL",
        "MULTIMODAL_SKILL_BRANCH_PREVIOUS_STEPS_TURNS",
    ):
        monkeypatch.delenv(key, raising=False)

    monkeypatch.setenv("MULTIMODAL_SKILL_BRANCH_MAX_IMAGES", "7")
    monkeypatch.setenv("MULTIMODAL_SKILL_BRANCH_MAX_CONSULTS_PER_SKILL", "3")
    monkeypatch.setenv("MULTIMODAL_SKILL_BRANCH_PREVIOUS_STEPS_TURNS", "5")

    settings = MobileGuiRuntimeSettings.from_env()

    assert settings.skill_branch_max_images == 7
    assert settings.skill_branch_max_consults_per_skill == 3
    assert settings.skill_branch_previous_steps_turns == 5


def test_mobile_gui_runtime_settings_defaults():
    """Dataclass defaults match documented mobile-agent baseline values."""
    settings = MobileGuiRuntimeSettings()

    assert settings.vlm_grounding_max_width == 1280
    assert settings.vlm_coordinate_scale == 1000
    assert settings.mcs_screenshots_to_keep == 3
    assert settings.skill_consult_mode in ("branch", "inline")
    assert settings.skill_branch_max_images == 4
    assert settings.skill_branch_max_consults_per_skill == 2
    assert settings.skill_branch_previous_steps_turns == 10


def test_multimodal_env_overrides_legacy_mobile_env(monkeypatch):
    """When both env vars are set, MULTIMODAL_SKILL_CONSULT_MODE wins."""
    monkeypatch.setenv("MULTIMODAL_SKILL_CONSULT_MODE", "inline")
    monkeypatch.setenv("MOBILE_SKILL_CONSULT_MODE", "branch")

    assert MobileGuiRuntimeSettings.from_env().skill_consult_mode == "inline"


def test_vlm_grounding_max_width_from_env(monkeypatch):
    monkeypatch.setenv("VLM_GROUNDING_MAX_WIDTH", "512")
    assert MobileGuiRuntimeSettings.from_env().vlm_grounding_max_width == 512


def test_mcs_screenshots_to_keep_from_env(monkeypatch):
    monkeypatch.setenv("MCS_SCREENSHOTS_TO_KEEP", "5")
    assert MobileGuiRuntimeSettings.from_env().mcs_screenshots_to_keep == 5

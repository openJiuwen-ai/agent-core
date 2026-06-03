# coding: utf-8
"""Tests for VlmGroundingPerceptionRail image prep and coordinate scaling."""

from __future__ import annotations

from PIL import Image

from openjiuwen.harness.tools.mobile_gui.config import MobileGuiRuntimeSettings
from openjiuwen.harness.tools.mobile_gui.rails.vlm_grounding_perception_rail import (
    VlmGroundingPerceptionRail,
)


def test_prepare_image_for_model_resizes_wide_screenshots_proportionally():
    """Images wider than ``vlm_grounding_max_width`` are downscaled; aspect ratio is preserved."""
    settings = MobileGuiRuntimeSettings(vlm_grounding_max_width=64)
    rail = VlmGroundingPerceptionRail(settings)

    wide = Image.new("RGB", (128, 32), color="red")
    prepared = rail._prepare_image_for_model(wide)

    assert prepared.width == 64
    assert prepared.height == 16
    assert prepared.mode == "RGB"


def test_prepare_image_for_model_leaves_narrow_images_unchanged():
    """Screenshots already within max width are not upscaled."""
    settings = MobileGuiRuntimeSettings(vlm_grounding_max_width=200)
    rail = VlmGroundingPerceptionRail(settings)

    small = Image.new("RGB", (40, 80), color="blue")
    prepared = rail._prepare_image_for_model(small)

    assert prepared.size == (40, 80)


def test_active_coordinate_scale_uses_configured_normalized_scale():
    """Default models use ``vlm_coordinate_scale`` for both axes."""
    settings = MobileGuiRuntimeSettings(vlm_coordinate_scale=999)
    rail = VlmGroundingPerceptionRail(settings)
    displayed = Image.new("RGB", (10, 20))

    assert rail._active_coordinate_scale(displayed) == (999, 999)


def test_prepare_image_for_model_resizes_to_claude_dimensions():
    """Claude-family models receive a fixed resize before grounding."""
    settings = MobileGuiRuntimeSettings(
        vlm_claude_image_width=100,
        vlm_claude_image_height=50,
    )
    rail = VlmGroundingPerceptionRail(settings, model_name="claude-sonnet")
    prepared = rail._prepare_image_for_model(Image.new("RGB", (20, 40)))

    assert prepared.size == (100, 50)


def test_active_coordinate_scale_uses_displayed_pixels_for_claude_models():
    """Claude path uses the sent screenshot size, not ``vlm_coordinate_scale``."""
    settings = MobileGuiRuntimeSettings(vlm_coordinate_scale=999)
    rail = VlmGroundingPerceptionRail(settings, model_name="claude-sonnet")
    displayed = Image.new("RGB", (400, 300))

    assert rail._active_coordinate_scale(displayed) == (400, 300)


def test_coordinate_instruction_normalized_vs_pixel_wording():
    """Instruction text reflects whether x/y share one scale or use pixel ranges."""
    rail = VlmGroundingPerceptionRail(MobileGuiRuntimeSettings(vlm_coordinate_scale=1000))

    normalized = rail._coordinate_instruction(1000, 1000)
    assert "[0, 1000]" in normalized
    assert "normalized" in normalized.lower()

    pixel = rail._coordinate_instruction(800, 600)
    assert "x in [0, 800]" in pixel
    assert "y in [0, 600]" in pixel


def test_active_coordinate_scale_unit_scale_for_kimi_models():
    """Kimi-k models use (1, 1) normalized coordinates."""
    rail = VlmGroundingPerceptionRail(
        MobileGuiRuntimeSettings(vlm_coordinate_scale=1000),
        model_name="kimi-k2",
    )
    displayed = Image.new("RGB", (500, 800))
    assert rail._active_coordinate_scale(displayed) == (1, 1)


def test_prepare_image_for_model_adaptive_resize_for_opus():
    """Opus models downscale by max dimension while preserving aspect ratio."""
    settings = MobileGuiRuntimeSettings(vlm_claude_opus_max_dimension=100)
    rail = VlmGroundingPerceptionRail(settings, model_name="claude-opus-4")
    tall = Image.new("RGB", (50, 200))
    prepared = rail._prepare_image_for_model(tall)

    assert max(prepared.size) == 100
    assert prepared.width == 25
    assert prepared.height == 100


def test_pil_to_base64_returns_jpeg_payload():
    rail = VlmGroundingPerceptionRail(MobileGuiRuntimeSettings())
    b64 = rail._pil_to_base64(Image.new("RGB", (4, 4), color="green"))
    assert isinstance(b64, str)
    assert len(b64) > 20


def test_get_foreground_app_returns_package_from_device():
    rail = VlmGroundingPerceptionRail(MobileGuiRuntimeSettings())

    class _Device:
        @staticmethod
        def app_current():
            return {"package": "com.example.app"}

    assert rail._get_foreground_app(_Device()) == "com.example.app"


def test_get_foreground_app_returns_unknown_on_device_error():
    rail = VlmGroundingPerceptionRail(MobileGuiRuntimeSettings())

    class _BrokenDevice:
        @staticmethod
        def app_current():
            raise RuntimeError("adb offline")

    assert rail._get_foreground_app(_BrokenDevice()) == "Unknown"
